"""Platform state of the portal's tasks, for the admin page's "Where they are now" panel: who has submitted each
task (someone who passed /rubric here and then submitted it is finished) and who holds it now (a task someone
else has claimed, or submitted after this contributor last worked on it, no longer counts for them).

A submission is a TASKATTEMPTS row; a live claim is a pending PIPELINEV3HUMANNODES row. Both come from Snowflake
through a saved Redash query, so they can lag the platform. Refreshed every 10 minutes in the background; the
last good answer is kept if Redash fails.
"""

import asyncio
import logging
import re
import time

from . import auth, config, db

log = logging.getLogger("portal.submissions")
REFRESH_SEC = 600
TASK_ID = re.compile(r"^[0-9a-f]{24}$")
_latest = {}  # (task_id, user_id) -> latest submission time (epoch seconds)
_holders = {}  # task_id -> contributors with a live claim on it now
state = {"checked_at": None, "error": None}


def submitted_after(task_id, user_id, since):
    """Did this contributor submit this task after `since`?"""
    at = _latest.get((task_id, user_id))
    return bool(at and since and at > since)


def submitted_at(task_id, user_id):
    """When this contributor last submitted this task (epoch seconds), or None."""
    return _latest.get((task_id, user_id)) or None


def holds_claim(task_id, user_id):
    """Does this contributor have a live claim on this task now?"""
    return user_id in _holders.get(task_id, set())


def fresh(max_age=1800):
    """Is the platform state recent enough to act on (checked in the last `max_age` seconds, without error)?"""
    return bool(state["checked_at"] and time.time() - state["checked_at"] < max_age and not state["error"])


def taken_by_other(task_id, user_id, since):
    """Is this task now with someone else: claimed by another contributor (and not by this one), or submitted by
    another contributor after `since` (when this one last worked on it)?"""
    holders = _holders.get(task_id, set())
    if holders and user_id not in holders:
        return True
    return any(at > (since or 0) for (task, user), at in _latest.items() if task == task_id and user != user_id)


def _portal_tasks():
    rows = db.q("select distinct task_id from chats where coalesce(task_id, '') != 'loadtest'")
    return sorted({r["task_id"] for r in rows if TASK_ID.match(r["task_id"] or "")})


async def refresh():
    tasks = _portal_tasks()
    if not config.SUBMISSIONS_QUERY_ID or not tasks:
        return
    latest, holders = {}, {}
    for i in range(0, len(tasks), 200):
        rows = await auth.redash_rows(config.SUBMISSIONS_QUERY_ID, {"task_ids": ",".join(tasks[i:i + 200])})
        for row in rows:
            row = {k.lower(): v for k, v in row.items()}
            if row.get("holds_claim"):
                holders.setdefault(row["task_id"], set()).add(row["user_id"])
            else:
                latest[(row["task_id"], row["user_id"])] = float(row["last_submitted_at"] or 0)
    _latest.clear()
    _latest.update(latest)
    _holders.clear()
    _holders.update(holders)
    state.update(checked_at=time.time(), error=None)


async def loop():
    while True:
        try:
            await refresh()
        except Exception as error:  # noqa: BLE001 - keep the last good answer
            state["error"] = str(error)[:200]
            log.warning("submission lookup failed: %s", error)
        else:
            from . import submit_watch  # here, not at the top: submit_watch imports this module
            await submit_watch.after_refresh()
        await asyncio.sleep(REFRESH_SEC)
