"""The "plan your task" kickoff (pilot, PLAN_PILOT, 2026-10-31).

- The card: the first time an enabled contributor opens an attempt's chat before its first /prompt review, the chat gets
  one system message (meta kickoff: true) with the card text, which ends by suggesting they ask the assistant to check
  their idea against the guides. Once per attempt, never after a /prompt exists; never posted for an admin viewing the
  chat, an archived chat, a blocked chat or a load-test chat. Posted from GET /api/me, the call the chat page makes when
  it opens a chat (sign-in, reload or a switch from the sidebar), so it lands right after the welcome message and also
  reaches chats that already existed. Once posted it stays in the chat as it is.
- The early guard: one bullet in the assistant's standing instructions for every chat (runner.GUARD). It covers the
  request the card suggests: checking the contributor's own idea or draft against the guides.

(A "Plan my task with the guides" button that sent a hidden planning note was tried and dropped on 2026-10-31: one
planning run wasn't reliable enough.)

The texts are files in server/prompts/ (config.PROMPTS_DIR), so final wording is a file copy, no code change.
"""

import logging
import re
import threading

from . import blocks, broadcasts, config, db, hub

log = logging.getLogger("portal.plan")

FILES = {"card": "plan_kickoff_card.md", "guard": "early_guard.md"}
_LEADING_COMMENTS = re.compile(r"\A(?:\s*<!--.*?-->)+", re.S)
_lock = threading.Lock()  # two tabs opening the same chat at once must not post two cards


def read(name):
    """One prompts file, whole, minus leading <!-- maintainer notes -->."""
    return _LEADING_COMMENTS.sub("", (config.PROMPTS_DIR / name).read_text(encoding="utf-8")).strip()


def card_text():
    return read(FILES["card"])


def early_guard_text():
    return read(FILES["guard"])


def placeholders():
    """The prompts files that still carry placeholder text (logged at startup, so nobody ships them unknowingly)."""
    out = []
    for name in FILES.values():
        try:
            if "PLACEHOLDER" in (config.PROMPTS_DIR / name).read_text(encoding="utf-8"):
                out.append(name)
        except OSError:
            out.append(name + " (missing)")
    return out


def in_pilot(chat):
    """PLAN_PILOT lists emails or Outlier IDs ("*" = everyone, empty = off), matched against the attempt's owner: their
    Outlier ID or the attempt's email, with or without the +outlier suffix. Never the admin login: an admin opening
    someone's chat sees what that contributor sees, and an admin's own chat isn't a contributor's."""
    pilot = {x.lower() for x in config.PLAN_PILOT}
    if not pilot or not chat:
        return False
    if "*" in pilot:
        return True
    email = (chat.get("email") or "").strip().lower()
    who = {chat.get("user_id"), email, broadcasts.base_email(email)}
    return bool(pilot & {str(x).lower() for x in who if x})


def has_prompt(attempt_id):
    """A /prompt review was ever started on this attempt (any outcome), or a /prompt report or pass card is in the chat."""
    if db.one("select 1 from runs where attempt_id=? and kind='review' and command='prompt' limit 1", (attempt_id,)):
        return True
    return bool(db.one("select 1 from messages where attempt_id=? and role in ('report','proof') "
                       "and meta like '%\"stage\": \"prompt\"%' limit 1", (attempt_id,)))


def kickoff_shown(attempt_id):
    return bool(db.one("select 1 from messages where attempt_id=? and role='system' and meta like '%\"kickoff\": true%' "
                       "limit 1", (attempt_id,)))


def _eligible_chat(chat):
    return bool(chat and not chat.get("archived_from") and (chat.get("task_id") or "") != "loadtest"
                and in_pilot(chat) and not blocks.chat_blocked(chat["attempt_id"]))


def maybe_kickoff(session, chat):
    """Post the kickoff card if this is the contributor's own chat, in the pilot, before any /prompt, and the card isn't
    in it yet. Returns the posted message, or None."""
    if session.get("admin") or not _eligible_chat(chat):
        return None
    attempt_id = chat["attempt_id"]
    with _lock:
        if kickoff_shown(attempt_id) or has_prompt(attempt_id):
            return None
        msg = db.add_message(attempt_id, "system", card_text(), {"kickoff": True})
    hub.publish(attempt_id, "message", msg)
    log.info("plan kickoff card posted for %s", attempt_id)
    return msg
