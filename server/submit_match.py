"""After a submit: do the submitted prompt / SQL / OE / rubric match the inputs the chat's passes were reviewed on?
(Evan, 2026-11-11.)

Two tasks reached L10 with a prompt that wasn't the one their /oe and /rubric passed with: …ec32 submitted the
294-character prompt (its only /prompt pass, on older SQL) while hardness, OE and rubric all passed on a later
238-character version; …6c32 the same with 724 vs 672 characters. Each platform fingerprint linter checks its own
field against its own pass screenshot, and an old prompt pass screenshot still matched the old prompt, so nothing
checked that all four passes came from one version of the task.

So, after each submissions refresh (server/submissions.py, every 10 minutes), for every portal chat whose task the
contributor submitted since the last check: read what was submitted (query SUBMIT_MATCH_QUERY_ID; the CDS response
view lands ~20-30 min after submit, so a submit is retried until it has, up to GIVE_UP), take the taxonomy
fingerprint of each submitted field (server/fingerprint.py, the platform's own formula), and compare it with the
fingerprint of the chat's saved input for every stage that has a pass. The saved inputs are what the latest passes
were reviewed on (a pass reviewed on anything older is OUTDATED, which submit_watch already reports). A difference
means the submission isn't the version the passes cover: Evan gets one alert per submit (submit_watch.send) and the
contributor's chat gets a note saying which field and which version to put back. Read-only: no review is run.
"""

import asyncio
import base64
import json
import logging
import re
import time

from . import auth, config, db, fingerprint, hub, notify, submissions

log = logging.getLogger("portal.submit_match")
STAGES = ("prompt", "injection", "oe", "rubric")
LABELS = {"prompt": "prompt", "injection": "injection SQL", "oe": "oracle events", "rubric": "rubric"}
BASELINE = "submitmatch:baseline"
GIVE_UP = 6 * 3600  # still not in the CDS view after this: stop asking
TASK_ID = re.compile(r"^[0-9a-f]{24}$")


def _key(attempt_id, at):
    return f"submitmatch:{attempt_id}:{int(at)}"


def _decode(b64):
    return base64.b64decode(b64).decode("utf-8", "replace") if b64 else ""


def submitted_fingerprints(row):
    """{stage: fingerprint} of what the platform row says was submitted."""
    out = {"prompt": fingerprint.text("prompt", _decode(row.get("PROMPT_B64"))),
           "injection": fingerprint.text("injection", _decode(row.get("INJECTION_B64"))),
           "oe": fingerprint.text("oe", _decode(row.get("OE_B64")))}
    try:
        criteria = json.loads(_decode(row.get("RUBRIC_B64")) or "null")
        out["rubric"] = fingerprint.rubric(criteria) if criteria else fingerprint.EMPTY["rubric"]
    except (TypeError, ValueError):
        out["rubric"] = None
    return out


def passed_stages(attempt_id):
    stages = set()
    for r in db.q("select payload from proofs where attempt_id=?", (attempt_id,)):
        stages.add(json.loads(r["payload"]).get("stage"))
    return [s for s in STAGES if s in stages]


def mismatches(attempt_id, submitted):
    """[{stage, submitted, reviewed}] for the stages with a pass whose saved input isn't what was submitted."""
    out = []
    for stage in passed_stages(attempt_id):
        mine, theirs = fingerprint.for_attempt(attempt_id, stage), submitted.get(stage)
        if mine and theirs and mine != theirs:
            out.append({"stage": stage, "submitted": theirs, "reviewed": mine})
    return out


def short(fp):
    """The fingerprint's size part and first/last words, short enough for a chat line."""
    head, _, rest = (fp or "").partition(" — ")
    if not rest:
        return fp
    words = rest.split(" … ")
    return f"{head}: “{words[0][:30]}…{words[-1][-30:]}”" if len(words) > 1 else f"{head}: “{rest[:60]}”"


def chat_note(found):
    names = ", ".join(LABELS[m["stage"]] for m in found)
    lines = [f"⚠ **Your Outlier submission doesn't match your passes here.** The {names} you submitted "
             f"{'is' if len(found) == 1 else 'are'} not the version{'s' if len(found) != 1 else ''} your portal passes were reviewed on:"]
    for m in found:
        lines.append(f"- **{LABELS[m['stage']].capitalize()}:** you submitted {short(m['submitted'])}; "
                     f"your passes were for {short(m['reviewed'])}.")
    lines += ["", "Your /oe and /rubric passes only cover the prompt, SQL and OE they were reviewed with, so the task "
                  "will likely be sent back. Put the versions that passed into Outlier, or run the reviews again on "
                  "the version you submitted. Type /admin if this looks wrong."]
    return "\n".join(lines)


def alert_text(hits):
    lines = [f"🚨 {len(hits)} submitted task{'s' if len(hits) != 1 else ''} don't match {'their' if len(hits) != 1 else 'its'} portal passes (Atlas Commercial):"]
    for h in hits:
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(h["at"]))
        lines.append(f"• {h['email']} · task {h['task_id']} · portal chat {h['attempt_id']} · submitted {when}")
        lines.append(f"    {notify.admin_link(h['attempt_id'])}")
        for m in h["mismatches"]:
            lines.append(f"    – {LABELS[m['stage']]}: submitted {m['submitted'][:70]} · passes were for {m['reviewed'][:70]}")
    lines.append("The OE and rubric passes don't cover what was submitted: send the task back to L-1, or have them "
                 "re-run the reviews on the submitted version.")
    return "\n".join(lines)


def due(baseline, now=None):
    """[(chat, submitted_at)] submits after baseline not checked yet and not given up on."""
    now = now or time.time()
    out, seen = [], set()
    for chat in reversed(db.q("select attempt_id, task_id, user_id, email from chats where coalesce(task_id,'') != 'loadtest' "
                              "and archived_from is null order by created_at")):
        pair = (chat["task_id"], chat["user_id"])
        if pair in seen or not TASK_ID.match(chat["task_id"] or ""):
            continue
        seen.add(pair)
        at = submissions.submitted_at(*pair)
        if not at or at <= baseline or now - at > GIVE_UP:
            continue
        if db.one("select 1 from status_notified where key=?", (_key(chat["attempt_id"], at),)):
            continue
        out.append((chat, at))
    return out


def _mark(key):
    db.ex("insert or replace into status_notified(key, created_at) values (?,?)", (key, time.time()))


async def check_once(send):
    """send(text) -> bool: submit_watch.send (Evan's DM, else the admin channel). Returns the hits found."""
    if not config.SUBMIT_MATCH_QUERY_ID or not submissions.fresh():
        return []
    row = db.one("select created_at from status_notified where key=?", (BASELINE,))
    if not row:  # first run: every submit so far is the baseline
        _mark(BASELINE)
        return []
    todo = due(row["created_at"])
    if not todo:
        return []
    tasks = sorted({c["task_id"] for c, _ in todo})
    rows = await auth.redash_rows(config.SUBMIT_MATCH_QUERY_ID, {"task_ids": ",".join(tasks)}, timeout=180)
    by_pair = {(r.get("TASK_ID"), r.get("USER_ID")): r for r in rows}
    hits, done = [], []
    for chat, at in todo:
        r = by_pair.get((chat["task_id"], chat["user_id"]))
        if not r or not r.get("LANDED") or abs(float(r.get("SUBMITTED_AT") or 0) - at) > 120:
            continue  # not in the CDS view yet (or a newer submit than this check knows): next time
        # reads the saved inputs (SQL can be ~400 KB): off the event loop
        found = await asyncio.to_thread(mismatches, chat["attempt_id"], submitted_fingerprints(r))
        done.append(_key(chat["attempt_id"], at))
        if found:
            hits.append({"attempt_id": chat["attempt_id"], "task_id": chat["task_id"], "email": chat["email"], "at": at,
                         "mismatches": found})
    if hits and not await send(alert_text(hits)):
        log.warning("submit-match alert failed; will retry %d submit(s)", len(hits))
        retry = {_key(h["attempt_id"], h["at"]) for h in hits}
        done = [k for k in done if k not in retry]
        hits = []
    for h in hits:
        msg = db.add_message(h["attempt_id"], "system", chat_note(h["mismatches"]), {"submit_match": True, "warn": True})
        hub.publish(h["attempt_id"], "message", msg)
    for key in done:
        _mark(key)
    if hits:
        log.info("submit-match: %d submit(s) don't match their passes", len(hits))
    return hits
