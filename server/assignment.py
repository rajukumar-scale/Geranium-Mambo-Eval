"""Fill a chat's assignment and task date from the platform task record (2026-11-03, Evan).

Contributors used to paste their assignment and pick the task date themselves. On the 317 tasks that passed /hardness
from 10-22 to 11-02, the pasted text left out the difficulty mode on 78 (so the evals assumed Medium), had the wrong
difficulty mode on 3 and the wrong task type on 7, and differed from the platform's business function, task skill,
competency or persona on 23 more. The platform keeps all of it on the task (tasks.metadata), so the portal reads it
from there with one saved Redash query (REDASH_TASK_QUERY_ID).

What it does, at most once per need and never while a run is active (a review box writes the inputs back when it ends):
- No assignment saved yet: save the platform's as task-details, with the eval's own input tool (so it is exactly what
  the eval would have saved from a paste).
- No task date saved yet: save the platform's universe (hg4-synth-sep30cut = Oct 1, 2025; hg4-2026-08-31 = Feb 14,
  2026), so the date picker never has to ask.
- An assignment is already saved but it asks for less than the platform's (an easier difficulty mode, or normal on a
  long-horizon task; a paste, now or later): the platform wins. More than assigned (Hard on a Medium task, long-horizon
  on a normal one) is kept, with no note: it grades stricter, so nothing it passed needs redoing (Evan, 2026-11-04). The portal saves the platform's assignment over it and says so in the chat; the passes
  that read the old one show as outdated, because they were graded against the wrong mode or task type (Evan,
  2026-11-03: "just use that as the default, no need to ask them any inputs").

Called before the first review in send() and when a chat is created at login. Off for load tests and with
ASSIGNMENT_AUTOFILL=0.
"""

import asyncio
import difflib
import logging
import os
import re
import time

from . import auth, config, db, hub, staleness, workspace

log = logging.getLogger("portal.assignment")

UNIVERSE_BY_ID = {"hg4-synth-sep30cut": "synthcut", "hg4-2026-08-31": "normal"}
SOURCE_NAME = "platform_assignment.md"
CACHE_SECONDS = 3600
_cache = {}  # task_id -> (time, row or None)
_locks = {}
TASK_ID = re.compile(r"^[0-9a-f]{24}$")


def enabled(chat):
    return bool(config.ASSIGNMENT_AUTOFILL and chat and TASK_ID.match(chat.get("task_id") or "")
                and not chat.get("archived_from"))


async def platform_record(task_id):
    """The task's assignment fields from the platform, or None (unknown task, lookup failed)."""
    held = _cache.get(task_id)
    if held and time.time() - held[0] < CACHE_SECONDS:
        return held[1]
    try:
        rows = await auth.redash_rows(config.REDASH_TASK_QUERY_ID, {"task_id": task_id}, max_age=CACHE_SECONDS)
    except Exception as error:  # noqa: BLE001 - a lookup failure must never stop a review
        log.warning("platform assignment lookup failed for %s: %s", task_id, error)
        return None
    row = {k.lower(): v for k, v in rows[0].items()} if rows else None
    _cache[task_id] = (time.time(), row)
    return row


def render(row):
    """The assignment as the eval reads it: one "Field: value" line each, only the fields the platform has."""
    lines = ["Your Assignment (from the platform task record)"]
    fields = (("Business Function", "business_function"), ("Persona", "persona_name"), ("Role", "persona_role"),
              ("Competency", "competency"), ("Task Skill", "task_skill"), ("Task Type", "task_type"),
              ("Difficulty Mode", "difficulty_mode"), ("Universe Date", "universe_date"))
    for label, key in fields:
        value = (row.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines) + "\n"


def universe_key(row):
    return UNIVERSE_BY_ID.get((row.get("universe_id") or "").strip())


def _saved(attempt_id, key):
    entry = next((i for i in workspace.saved_inputs(attempt_id) if i["key"] == key), None)
    return entry if entry and entry.get("state") == "saved" and entry.get("files") else None


def _saved_text(attempt_id, key):
    entry = _saved(attempt_id, key)
    if not entry:
        return ""
    root = workspace.task_inputs(attempt_id).resolve()
    path = workspace._input_path(root, entry["files"][0])  # noqa: SLF001 - same module family, same rules
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path else ""
    except OSError:
        return ""


def _pasted(text, pattern):
    m = re.search(pattern, (text or "").lower())
    return m.group(1) if m else None


MODE_RANK = {"easy": 0, "medium": 1, "hard": 2}


# The labels a pasted assignment uses, in any of the formats seen (one per line, "Label: value", or the taxonomy's
# emoji row "🏢 Business Function Executive  🧑 Persona …" on one line).
_LABELS = r"business function|persona|role|competency|task skill|task type(?: / horizon)?|horizon(?: type)?(?: \(normal or long\))?|difficulty mode|difficulty|universe date|task date"
# A different business function or task skill changes how a task is labelled, not what it must be: correcting one keeps
# the passes (Evan, 2026-11-04: 8 contributors had typed their competency as their business function).
LABEL_ONLY = {"business function", "task skill"}
_FIELDS = (("persona", "persona_name"), ("business function", "business_function"), ("competency", "competency"),
           ("task skill", "task_skill"))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower().replace("&", " and ")).strip()


def pasted_fields(text):
    """{label: [values]} from a saved assignment, whatever its layout (a label can appear more than once, for example in
    a template's instructions and again in its filled-in block)."""
    t = re.sub(r"[^\w\s:/().,&+-]", " ", text or "")  # drop the emoji
    marks = [(m.start(), m.end(), m.group(1).lower()) for m in re.finditer(rf"(?i)\b({_LABELS})\b\s*[:\-]?", t)]
    out = {}
    for i, (start, end, label) in enumerate(marks):
        value = t[end: marks[i + 1][0] if i + 1 < len(marks) else len(t)]
        value = re.split(r"[\n,(]", value.strip(" :-\t"), maxsplit=1)[0].strip(" :-.")
        key = next((k for k, _ in _FIELDS if label.startswith(k)), None)
        if key and value:
            out.setdefault(key, []).append(value)
    return out


def same_value(saved, platform):
    """A typo, an old taxonomy name or a first name only is the same value (2026-11-04: of 449 chats compared, all but
    two differences were "Simon Walkera", "Retrival", "Enginerring Ops", "Founders-Exec-Strategy", "Claire")."""
    a, b = _norm(saved), _norm(platform)
    if not a or not b or a == b or a in b or b in a:
        return True
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
        return True
    first = b.split()[0][:4]  # "exec" in "founders exec strategy"; "claire" in "claire morgan"
    return len(first) >= 4 and any(w.startswith(first) for w in a.split())


def disagreements(text, row):
    """What in a saved (pasted) assignment really differs from the platform's record: another persona, business
    function, competency or task skill, or an easier difficulty mode / shorter task type than assigned."""
    out = []
    saved = pasted_fields(text)
    for label, key in _FIELDS:
        want = (row.get(key) or "").strip()
        if want and saved.get(label) and not any(same_value(v, want) for v in saved[label]):
            out.append((label, saved[label][-1], want))
    mode = _pasted(text, r"difficulty(?:\s*mode)?\s*[:\-]?\s*[^\w\n]*\n?\s*(easy|medium|hard)\b")
    want_mode = (row.get("difficulty_mode") or "").strip().lower()
    # Only an easier mode than the assigned one matters: Hard grades stricter than Medium (and Medium than Easy), so a
    # task built Hard on a Medium assignment keeps its saved mode and passes, with no note (Evan, 2026-11-04).
    if want_mode in MODE_RANK and mode in MODE_RANK and MODE_RANK[mode] < MODE_RANK[want_mode]:
        out.append(("difficulty mode", mode, want_mode))
    kind = _pasted(text, r"(?:task type|horizon(?: type)?)[^a-z]*(super[- ]long[- ]horizon|long[- ]horizon|long|normal)")
    want_kind = (row.get("task_type") or "").strip().lower()
    if want_kind and kind:
        kind = "long-horizon" if "long" in kind else "normal"
        want = "long-horizon" if "long" in want_kind else "normal"
        if kind == "normal" and want == "long-horizon":  # likewise: long-horizon on a normal task is fine, only shorter isn't
            out.append(("task type", kind, want))
    return out


def keep_harder(text, row):
    """The platform's record to save, keeping a saved difficulty mode or task type that asks for more than assigned."""
    out = dict(row)
    mode = _pasted(text, r"difficulty(?:\s*mode)?\s*[:\-]?\s*[^\w\n]*\n?\s*(easy|medium|hard)\b")
    want_mode = (row.get("difficulty_mode") or "").strip().lower()
    if mode in MODE_RANK and want_mode in MODE_RANK and MODE_RANK[mode] > MODE_RANK[want_mode]:
        out["difficulty_mode"] = mode
    kind = _pasted(text, r"(?:task type|horizon(?: type)?)[^a-z]*(super[- ]long[- ]horizon|long[- ]horizon|long|normal)")
    if kind and "long" in kind and "long" not in (row.get("task_type") or "").lower():
        out["task_type"] = "super-long-horizon" if "super" in kind else "long-horizon"
    return out


async def _store(attempt_id, *args):
    """Run the eval's own input tool in the attempt's workspace, like /api/universe does."""
    await asyncio.to_thread(workspace.thaw, attempt_id)  # a cold chat: unpack before docker mounts it (login stays fast otherwise)
    cmd = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "--network", "none", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "-w", "/work", *workspace.docker_mounts(attempt_id),
           "--entrypoint", "python3", config.RUNNER_IMAGE, "Evals/tools/hg_store_input.py", *args]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError((err or out).decode(errors="replace")[-300:])


def _posted(attempt_id, flag):
    return any(m["meta"].get(flag) for m in db.messages(attempt_id) if m["role"] == "system")


async def ensure(attempt_id):
    """Fill what's missing from the platform record. Returns a short summary dict (for logs and tests)."""
    chat = db.get_chat(attempt_id)
    if not enabled(chat) or config.RUNNER_MODE != "docker" or db.active_run(attempt_id):
        return {"skipped": True}
    lock = _locks.setdefault(attempt_id, asyncio.Lock())
    async with lock:
        row = await platform_record(chat["task_id"])
        if not row:
            return {"skipped": True, "reason": "no platform record"}
        done = {}
        if not _saved(attempt_id, "task-details"):
            (workspace.uploads_dir(attempt_id) / SOURCE_NAME).write_text(render(row), encoding="utf-8")
            await _store(attempt_id, "save", "task-details", "--source", f"Evals/task_inputs/_uploads/{SOURCE_NAME}")
            done["task_details"] = True
        else:
            text = _saved_text(attempt_id, "task-details")
            diff = disagreements(text, row)
            if diff:
                before = staleness.snapshot(attempt_id).get("task-details")
                (workspace.uploads_dir(attempt_id) / SOURCE_NAME).write_text(render(keep_harder(text, row)), encoding="utf-8")
                await _store(attempt_id, "save", "task-details", "--source", f"Evals/task_inputs/_uploads/{SOURCE_NAME}")
                parts = "; ".join(f"the {what} was **{a}**, the platform says **{b}**" for what, a, b in diff)
                if {d[0] for d in diff} <= LABEL_ONLY:  # nothing the reviews graded changed: the passes still count
                    staleness.restamp(attempt_id, "task-details", before, staleness.snapshot(attempt_id).get("task-details"))
                    text = (f"Your saved assignment now matches the platform's record of this task ({parts}). Your passes "
                            "still count; there's nothing to send again.")
                else:
                    text = (f"Your saved assignment didn't match the platform's record of this task ({parts}), so it's "
                            "now the platform's: the evals read the assignment from it. If the portal marks earlier "
                            "passes as outdated, send those reviews again.")
                m = db.add_message(attempt_id, "system", text, {"assignment_replaced": True, "warn": True})
                hub.publish(attempt_id, "message", m)
                done["replaced"] = [d[0] for d in diff]
        key = universe_key(row)
        saved_date = workspace.universe_date(attempt_id) if key and workspace.dated_release() else None
        if key and workspace.dated_release() and saved_date is None:
            await _store(attempt_id, "universe", key)
            done["universe"] = key
        elif key and saved_date and saved_date != key:  # a date picked or pasted that isn't the task's (Evan, 2026-11-04)
            await _store(attempt_id, "universe", key)
            done["universe_replaced"] = key
            m = db.add_message(attempt_id, "system", "Your task date didn't match the platform's record of this task, so "
                               f"it's now the platform's: **{workspace.UNIVERSE_DATES[key][1]}**. If the portal marks "
                               "earlier passes as outdated, send those reviews again.", {"universe_replaced": True, "warn": True})
            hub.publish(attempt_id, "message", m)
        if (done.get("task_details") or done.get("universe")) and not _posted(attempt_id, "assignment_autofill"):
            bits = []
            if done.get("task_details"):
                bits.append("your assignment (persona, business function, competency, task skill, task type and "
                            "difficulty mode)")
            if done.get("universe"):
                bits.append(f"the task date (**{workspace.UNIVERSE_DATES[key][1]}**)")
            m = db.add_message(attempt_id, "system", "Filled in from the platform's record of this task: " +
                               " and ".join(bits) + ". You don't need to paste it or pick it.",
                               {"assignment_autofill": True})
            hub.publish(attempt_id, "message", m)
        if done:
            log.info("assignment from the platform for %s: %s", attempt_id, done)
        return done


LOOP = None  # the portal's event loop, set at startup: /api/me runs in a worker thread
_checked = {}  # attempt_id -> last time a check was scheduled (the chat page polls /api/me)
RECHECK_SECONDS = 600


async def ensure_safe(attempt_id):
    try:
        return await ensure(attempt_id)
    except Exception:  # noqa: BLE001 - filling the assignment must never stop a review or a page from opening
        log.exception("assignment from the platform failed for %s", attempt_id)
        return {"error": True}


def schedule(attempt_id):
    """Run ensure() in the background, at most once per RECHECK_SECONDS per chat. Safe from any thread."""
    now = time.time()
    if LOOP is None or now - _checked.get(attempt_id, 0) < RECHECK_SECONDS:
        return False
    _checked[attempt_id] = now
    asyncio.run_coroutine_threadsafe(ensure_safe(attempt_id), LOOP)
    return True
