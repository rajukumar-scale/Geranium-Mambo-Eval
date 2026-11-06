"""DM Evan on Slack when a task is submitted while one of its passes is outdated (2026-11-05).

Task 6adfbc55...6c6f (attempt 6aeb9f8e): rubric and OE passed on Nov 3, the CB rewrote the SQL on Nov 4, re-ran only
/injection, /prompt and /hardness, and submitted the Nov 3 screenshots. The portal had marked them outdated; nothing
told anyone at submit. The portal can't take back an image already downloaded, so this is the check after the fact.

After each submissions refresh (every 10 minutes, server/submissions.py) every portal chat whose task the contributor
submitted since the last check is looked at: if any stage's newest pass no longer covers the saved inputs
(staleness.latest_outdated), Evan gets one Slack alert for that submit, with the stages, what changed and a link to
the chat: a DM when SLACK_BOT_TOKEN is set (same target as the linter alerts, LINTER_ALERT_SLACK_USER), otherwise the
admin-notification webhook (notify.slack, the channel /admin requests go to). The first run only records the submits
that already exist, so turning this on doesn't alert about old ones.
"""

import logging
import time

from . import config, db, linter_alerts, notify, staleness, submissions

log = logging.getLogger("portal.submit_watch")
BASELINE = "submitwatch:baseline"


def _key(attempt_id, at):
    return f"submitwatch:{attempt_id}:{int(at)}"


def _chats():
    return db.q("select attempt_id, task_id, user_id, email from chats where coalesce(task_id, '') != 'loadtest' "
                "order by created_at")


def message(hits):
    many = len(hits) != 1
    lines = [f"🚨 {len(hits)} task{'s' if many else ''} submitted with outdated passes (Atlas Commercial):"]
    for h in hits:
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(h["at"]))
        lines.append(f"• {h['email']} · task {h['task_id']} · portal chat {h['attempt_id']} · submitted {when}")
        lines.append(f"    {notify.admin_link(h['attempt_id'])}")
        for stage in staleness.rerun_order(h["outdated"]):
            lines.append(f"    – /{stage}: {', '.join(h['outdated'][stage]['labels'])} changed after it passed")
    lines.append("Their pass screenshots for those stages were graded on older inputs: send the task back to L-1.")
    return "\n".join(lines)


def scan(baseline):
    """Submits after `baseline` not checked yet: [(key, hit or None)]. hit = the chat's outdated passes."""
    out, seen = [], set()
    for chat in reversed(_chats()):  # newest chat first: a contributor's latest portal chat for the task
        pair = (chat["task_id"], chat["user_id"])
        if pair in seen:
            continue
        seen.add(pair)
        at = submissions.submitted_at(*pair)
        if not at or at <= baseline:
            continue
        key = _key(chat["attempt_id"], at)
        if db.one("select 1 from status_notified where key=?", (key,)):
            continue
        outdated = staleness.latest_outdated(chat["attempt_id"])
        hit = ({"attempt_id": chat["attempt_id"], "task_id": chat["task_id"], "email": chat["email"], "at": at,
                "outdated": outdated} if outdated else None)
        out.append((key, hit))
    return out


async def check_once():
    if not submissions.fresh():
        return []
    row = db.one("select created_at from status_notified where key=?", (BASELINE,))
    if not row:  # first run: everything submitted so far is the baseline
        db.ex("insert or replace into status_notified(key, created_at) values (?,?)", (BASELINE, time.time()))
        return []
    found = scan(row["created_at"])
    hits = [h for _, h in found if h]
    if hits and not await send(message(hits)):
        log.warning("submit-watch DM failed; will retry %d submit(s) next check", len(hits))
        found = [(k, h) for k, h in found if not h]  # keep the clean ones as checked, retry the hits
    for key, _ in found:
        db.ex("insert or replace into status_notified(key, created_at) values (?,?)", (key, time.time()))
    if hits:
        log.info("submit-watch: %d submit(s) with outdated passes", len(hits))
    return hits


async def send(text):
    """A DM to Evan (bot token or the DM workflow's webhook), else the admin-notification webhook. False if neither worked."""
    if linter_alerts.dm_configured() and await linter_alerts.dm(text):
        return True
    if notify.configured():
        return await notify.slack(text)
    return False


_send_real = send  # the unpatched sender, for tests


async def after_refresh():
    if not config.SUBMIT_WATCH or not (config.SLACK_BOT_TOKEN or notify.configured()):
        return
    try:
        await check_once()
    except Exception:  # noqa: BLE001 - never break the submissions loop
        log.exception("submit-watch check failed")
