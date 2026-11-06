"""Env data check after /hardness (Evan, 2026-11-06): data in the contributor's env that their SQL doesn't create.

QC failed two tasks on 2026-11-06 for env data the submitted SQL didn't explain: 6aecc5e4 had rows left by older SQL
drafts run on the same env (re-running a draft adds rows and never removes the old ones), and 6aec82c2 had data added
by hand in the env explorer. Nothing in the portal looked at the env itself, and the portal can't reach it. But the
Opus run the contributor uploads for /hardness shows what the env returned: 6aecc5e4's earlier upload read 178 such
records (Wise and Griffin payment emails, duplicate creator agreements) a day before submit.

- After a /hardness review finishes, in the background: take the ids the run read from the env (Drive/Docs file ids,
  Gmail message and thread ids, Slack timestamps, Linear issue keys; fields that come straight from the exported data),
  skip ones the agent created itself (first seen in a write tool's result), drop those in the saved SQL, and look the
  rest up in the task date's base universe with one ripgrep pass (idle CPU and disk priority, one scan at a time).
- Anything left is in neither the base data nor the SQL. The chat gets a warning card listing them, with the fix, and
  the chat page shows it as a pop-up that only Acknowledge closes (after ticking that they redeployed from the base
  universe and ran only their final SQL); until then it opens every time the chat does (pending(), POST
  /api/env-check/ack). No "clean now" note: a run that didn't read the old rows doesn't prove they're gone
  (6aecc5e4's final upload was clean while 5 of 6 baseline runs still read them).
- Calibration (2026-11-06, 25 uploads): the 6aecc5e4 draft-leftover upload flagged 178; 21 of the other 22 flagged
  nothing; 2 flagged 1-2 Slack messages that are in neither their SQL nor the base copy (likely real; on Feb 14 the
  env can load a newer universe version than the portal's copy, so a single message there can be version drift).
- Advice only: never changes a verdict or blocks anything. Off for admins' runs, load tests, archived chats, and
  anyone outside ENV_CHECK_PILOT ("*" = everyone, the default; empty = off).
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import config, db, hub, workspace

log = logging.getLogger("portal.env_check")

TRAJECTORY = "opus/trajectory.json"
UNIVERSE_DIRS = {"normal": "Universe_Normal", "synthcut": "Universe_SynthCut"}
WRITE_TOOL = re.compile(r"(send|create|post|copy|update|share|reply|add|comment|insert|append|upload|move|delete|draft|"
                        r"label|react|archive|assign|set_|edit|write|merge|close|transition|batch)", re.I)
ID_KEYS = {"id", "ts", "thread_ts", "threadid", "identifier", "documentid"}
KEEP = re.compile(r"^(gdrive_[a-z_]+\.result(\.files\[\])?\.id|gdocs_[a-z_]+\.result\.documentId"
                  r"|gmail_[a-z_]+\.result(\.messages\[\])?\.(id|threadId)"
                  r"|slack_[a-z_]+\.result(\.messages\[\])?\.(ts|thread_ts)"
                  r"|linear_[a-z_]+\.result(\.issues\[\])?\.(id|identifier))$")
SERVICE = {"gdrive": "Drive", "gdocs": "Docs", "gmail": "Gmail", "slack": "Slack", "linear": "Linear"}
LABEL_KEYS = ("name", "title", "subject", "text", "snippet")
TICKET = re.compile(r"^[A-Z][A-Z0-9]{1,7}-\d{1,6}$")
MAILBOX = re.compile(r"^[^/\s]+@[^/\s]+/")  # Gmail ids come back as "<mailbox>/<message id>"
SHOW = 6  # records listed per service in the card
_lock = asyncio.Lock()  # one scan at a time: each reads the whole universe (14-27 GB)


def _loads(x):
    for _ in range(4):
        if not isinstance(x, str) or not x.strip()[:1] in ("[", "{"):
            return x
        try:
            x = json.loads(x)
        except ValueError:
            return x
    return x


def _walk(o, path, out):
    if isinstance(o, dict):
        label = next((str(o[k]) for k in LABEL_KEYS if isinstance(o.get(k), str) and o[k].strip()), "")
        for k, v in o.items():
            p = path + "." + k
            if k.lower() in ID_KEYS and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                s = str(v)
                if len(s) >= 5 or TICKET.match(s):
                    out.append((s, p, label))
            _walk(_loads(v), p, out)
    elif isinstance(o, list):
        for v in o:
            _walk(_loads(v), path + "[]", out)


def read_ids(text):
    """{id: (field path, label)} for the env records a run export read, or None if the format isn't one we know.
    Ids first seen in a write tool's result are the agent's own and are left out."""
    try:
        events = json.loads(text)
    except ValueError:
        try:
            events = [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError:
            return None
    if isinstance(events, dict):
        events = events.get("messages")
    if not isinstance(events, list):
        return None
    tools, first = {}, {}
    for e in events:
        msg = e.get("message") if isinstance(e, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                tools[b.get("id")] = str(b.get("name") or "")
            elif b.get("type") == "tool_result":
                name = tools.get(b.get("tool_use_id"), "")
                if not name.startswith("mcp__"):
                    continue
                tool = name.split("__")[-1]
                body = b.get("content")
                parts = [x.get("text", "") for x in body if isinstance(x, dict)] if isinstance(body, list) else [body]
                for part in parts:
                    out = []
                    _walk(_loads(part), tool, out)
                    for s, p, label in out:
                        first.setdefault(s, (p, label, bool(WRITE_TOOL.search(tool))))
    return {s: (p, label) for s, (p, label, write) in first.items() if not write and KEEP.match(p)}


def _norm(s):
    s = MAILBOX.sub("", s)
    return s[1:-1] if s.startswith("<") and s.endswith(">") else s


def _found_in(universe, ids):
    """The ids that occur anywhere in the universe folder's files."""
    if not ids:
        return set()
    rg = shutil.which("rg")
    if rg:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            # longest first: with -o, ripgrep reports the first pattern that matches at a spot ("ZOM-44" in "ZOM-444")
            f.write("\n".join(sorted(ids, key=len, reverse=True)))
        cmd = ["nice", "-n", "19", "ionice", "-c3", rg, "-j2", "-F", "-o", "-N", "--no-filename", "-f", f.name, str(universe)]
        if not shutil.which("ionice"):
            cmd = cmd[:3] + cmd[5:]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        finally:
            try:
                os.unlink(f.name)
            except OSError:
                pass
        if r.returncode not in (0, 1):
            raise RuntimeError("ripgrep failed: " + r.stderr[-300:])
        return set(r.stdout.split("\n")) & set(ids)
    found = set()  # no ripgrep (tests): read every file
    for p in universe.rglob("*"):
        if p.is_file():
            data = p.read_text(encoding="utf-8", errors="replace")
            found |= {i for i in ids if i in data}
    return found


def unknown_records(trajectory_text, sql_text, universe):
    """[(id, service, label)] the run read that are in neither the SQL nor the base universe, or None if the run's
    format isn't known."""
    ids = read_ids(trajectory_text)
    if ids is None:
        return None
    cand = {}
    for s, (path, label) in ids.items():
        n = _norm(s)
        if n and n not in sql_text and s not in sql_text:
            cand[n] = (SERVICE.get(path.split("_")[0], path.split("_")[0]), label)
    found = _found_in(universe, list(cand))
    return [(s, svc, label) for s, (svc, label) in cand.items() if s not in found]


def card_text(unknown):
    by = {}
    for s, svc, label in unknown:
        by.setdefault(svc, []).append(f"`{s}`" + (f" ({label[:60].strip()})" if label else ""))
    lines = [f"⚠ **Your env has data your SQL doesn't create.** Your Opus run read **{len(unknown)}** record"
             f"{'s' if len(unknown) != 1 else ''} that {'are' if len(unknown) != 1 else 'is'} in neither the base data "
             "nor your saved SQL:"]
    for svc, items in by.items():
        more = f" and {len(items) - SHOW} more" if len(items) > SHOW else ""
        lines.append(f"- {svc}: " + ", ".join(items[:SHOW]) + more)
    lines += ["",
              "This comes from data added by hand in the env explorer, or from an older SQL draft you ran on the same env "
              "(running a draft again adds rows but never removes the old ones). QC fails tasks for env data the SQL "
              "doesn't create, and the baseline runs read it too.",
              "",
              "**Fix:** put everything you want in the task into your SQL. Then redeploy the env, apply only your final "
              "SQL, and redo the Opus run. Redeploy from the base universe, never from a snapshot: a snapshot keeps "
              "the old rows. If you didn't add these records, type /admin."]
    return "\n".join(lines)


def in_pilot(chat):
    pilot = {x.lower() for x in config.ENV_CHECK_PILOT}
    if not pilot or not chat:
        return False
    if "*" in pilot:
        return True
    email = (chat.get("email") or "").strip().lower()
    return bool(pilot & {x for x in (chat.get("user_id"), email, email.split("+")[0]) if x})


def pending(attempt_id):
    """The newest env warning card in the chat if it isn't acknowledged yet: {"id", "content"}, else None."""
    row = db.one("select id, content, meta from messages where attempt_id=? and role='system' "
                 "and meta like '%\"env_check\":%' order by id desc limit 1", (attempt_id,))
    if not row or not (json.loads(row["meta"] or "{}").get("env_check") or 0) > 0:
        return None
    if db.one("select 1 from env_check_acks where attempt_id=? and message_id=?", (attempt_id, row["id"])):
        return None
    return {"id": row["id"], "content": row["content"]}


def acknowledge(attempt_id, message_id):
    """The contributor's Acknowledge on a warning card of their own chat. False if it isn't one."""
    row = db.one("select meta from messages where id=? and attempt_id=? and role='system'", (message_id, attempt_id))
    if not row or not (json.loads(row["meta"] or "{}").get("env_check") or 0) > 0:
        return False
    db.ex("insert or ignore into env_check_acks(attempt_id, message_id, acked_at) values (?,?,?)",
          (attempt_id, message_id, time.time()))
    return True


def wanted(chat, run, status):
    return bool(run and run.get("kind") == "review" and run.get("command") == "hardness" and status == "done"
                and not run.get("actor_admin") and not run.get("loadtest") and chat and not chat.get("archived_from")
                and in_pilot(chat))


def check(chat, run_id=None):
    """Scan the attempt's saved Opus run and post the warning card when it read unknown records. Returns the posted
    message, or None. Blocking: run it in a thread."""
    attempt = chat["attempt_id"]
    universe = UNIVERSE_DIRS.get(workspace.universe_date(attempt) or "")
    inputs = workspace.task_inputs(attempt)
    if not universe:
        return None
    try:
        text = (inputs / TRAJECTORY).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    sql = ""
    entry = next((i for i in workspace.saved_inputs(attempt) if i["key"] == "injection" and i["state"] == "saved"), None)
    for f in (entry or {}).get("files", []):
        try:
            sql += (inputs / (f.get("path") or "").removeprefix("Evals/task_inputs/")).read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    started = time.time()
    unknown = unknown_records(text, sql, config.EVAL_BASE_DIR / universe)
    if unknown is None:
        return None
    log.info("env check %s: %d unknown records (%.0fs)", attempt, len(unknown), time.time() - started)
    if not unknown:
        return None
    msg = db.add_message(attempt, "system", card_text(unknown), {"run_id": run_id, "env_check": len(unknown), "warn": True})
    hub.publish(attempt, "message", msg)
    return msg


async def after_review(chat, run, status):
    """Runner._complete schedules this after a finished review; it must never break finishing a run."""
    if not wanted(chat, run, status):
        return None
    async with _lock:
        try:
            return await asyncio.to_thread(check, chat, run["id"])
        except Exception:  # noqa: BLE001
            log.exception("env check failed for %s", chat["attempt_id"])
            return None


_tasks = set()  # running checks, kept referenced until they finish


def schedule(chat, run, status):
    """Start the check in the background when it applies (it takes minutes; finishing the run doesn't wait)."""
    if not wanted(chat, run, status):
        return None
    task = asyncio.get_running_loop().create_task(after_review(chat, run, status))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task
