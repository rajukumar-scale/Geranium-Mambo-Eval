"""Task IDs are for admins only: a contributor may see their attempt ID, never a task ID (Evan, 2026-10-17).

Everything the chat page and the public verify page send to a non-admin goes through hide(): task_id fields are
dropped, and any task ID the portal knows becomes "(hidden)" inside text, except in a message an admin wrote. Text carries them in many places: report
headers (171 of 254 reports on 2026-10-17), pass-card records, the assistant's replies, activity lines, pasted inputs.
"""

import re
import time

from . import db

HIDDEN = "(hidden)"
_HEX_RUN = re.compile(r"[0-9a-fA-F]{24,}")
_cache = {"at": 0.0, "ids": frozenset()}


def known():
    """Every task ID in the portal's chats (re-read every 30 seconds)."""
    if time.time() - _cache["at"] > 30:
        rows = db.q("select distinct task_id from chats where task_id is not null")
        _cache.update(at=time.time(), ids=frozenset(r["task_id"].lower() for r in rows if len(r["task_id"]) == 24))
    return _cache["ids"]


def scrub(text, ids):
    """The text with every task ID in `ids` replaced, also where one sits inside a longer run of hex characters."""
    def fix(match):
        run, out, i = match.group(), [], 0
        while i + 24 <= len(run):
            if run[i:i + 24].lower() in ids:
                out.append(HIDDEN)
                i += 24
            else:
                out.append(run[i])
                i += 1
        return "".join(out) + run[i:]
    return _HEX_RUN.sub(fix, text)


def hide(value, extra=()):
    """A copy of `value` (JSON-like data or text) without task IDs. `extra`: task IDs to hide besides the known
    ones (a chat created in the last 30 seconds)."""
    return _hide(value, known() | {e.lower() for e in extra if e})


def _hide(value, ids):
    if isinstance(value, dict):
        # An admin's own chat message is sent as typed: an ID an admin writes to a contributor is meant for them
        # (Evan, 2026-10-24). Everything else in the message record is still scrubbed.
        keep = "content" if value.get("role") == "admin" else None
        return {k: (v if k == keep else _hide(v, ids)) for k, v in value.items() if k.lower() != "task_id"}
    if isinstance(value, (list, tuple)):
        return [_hide(v, ids) for v in value]
    if isinstance(value, str) and len(value) >= 24:
        return scrub(value, ids)
    return value
