"""The /prompt fix helper (pilot, FIX_HELPER_PILOT, approved by Evan 2026-11-01; first built 2026-10-28 as "coach").

- The offer: when a /prompt review finishes FAIL and the attempt now has two or more /prompt FAILs (the 2nd FAIL and
  every later one), the chat gets one system message under the report (meta fix_help_offer: true) asking what the agent
  should end up doing and the one thing a careless agent would miss. The 1st FAIL gets the report only. If they answered
  an earlier offer on this attempt, the offer shows a one-line reminder of that answer and says they can reply
  "same idea" or update it.
- The note: the contributor's next plain message after the latest offer (not a command, not an answer to a review that
  is waiting for one, and only while no /prompt run has started since the offer) is sent to the model with a hidden note:
  server/prompts/fix_helper_rules.md plus their answer. "same idea" or a reply of a few words reuses the answer they gave
  an earlier offer. The answer used is saved on that user message (meta fix_help_answer) so later offers can reuse it.
  Only that one message carries the note: no GUARD change, so other chats and messages are untouched.

Never for a run an admin started, an admin sending in someone's chat, or an archived, blocked or load-test chat.

Why: in the 2026-10-27 drop-off analysis the third /prompt FAIL was where most contributors gave up. In the 2026-10-28
fix-helper test, every new problem a fix created came from a new general rule or broader wording; fixes that named
the item created none.
"""

import json
import logging
import re

from . import blocks, broadcasts, config, db, hub, plan

log = logging.getLogger("portal.fix_helper")

RULES_FILE = "fix_helper_rules.md"
QUESTIONS = ("- what should the agent end up doing?\n"
             "- what's the one thing it should catch that a careless agent would miss?\n\n"
             "I'll work only on what the report failed, starting from what you meant.")
OFFER = "This one didn't pass yet. Want help fixing it? Tell me in a few sentences:\n" + QUESTIONS
OFFER_AGAIN = ("This one didn't pass yet. Want help fixing it? Last time you told me: \"{reminder}\"\n\n"
               "Reply **same idea** to use that again, or tell me what changed:\n" + QUESTIONS)
REMINDER_CHARS = 200
SHORT_WORDS = 3  # a reply this short ("yes please", "same") isn't an answer of its own
# "same idea" (alone, or followed by an update); a bare "same" or "yes" is caught by SHORT_WORDS
_SAME = re.compile(r"^\s*(same\s+idea|same\s+as\s+(before|last\s+time)|ditto)\b[\s.,;:!-]*", re.I)
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~])")


def rules_text():
    return plan.read(RULES_FILE)


def in_pilot(chat):
    """FIX_HELPER_PILOT lists emails or Outlier IDs ("*" = everyone, empty = off), matched against the attempt's owner,
    like PLAN_PILOT (plan.in_pilot): never the admin login."""
    pilot = {x.lower() for x in config.FIX_HELPER_PILOT}
    if not pilot or not chat:
        return False
    if "*" in pilot:
        return True
    email = (chat.get("email") or "").strip().lower()
    who = {chat.get("user_id"), email, broadcasts.base_email(email)}
    return bool(pilot & {str(x).lower() for x in who if x})


def _eligible_chat(chat):
    return bool(chat and not chat.get("archived_from") and (chat.get("task_id") or "") != "loadtest"
                and in_pilot(chat) and not blocks.chat_blocked(chat["attempt_id"]))


def prompt_fails(attempt_id):
    """Finished /prompt reviews on this attempt whose verdict is FAIL (a review answered after a question counts once:
    only the run that saved the report has a verdict)."""
    row = db.one("select count(*) n from runs where attempt_id=? and kind='review' and command='prompt' "
                 "and status='done' and verdict='FAIL' and coalesce(loadtest, 0)=0", (attempt_id,))
    return row["n"] if row else 0


def _meta(row):
    meta = row.get("meta")
    return meta if isinstance(meta, dict) else json.loads(meta or "{}")


def saved_answer(attempt_id):
    """The answer the contributor gave the most recent offer they answered with one, or ""."""
    for row in db.q("select meta from messages where attempt_id=? and role='user' and meta like '%\"fix_help_answer\"%' "
                    "order by id desc limit 20", (attempt_id,)):
        answer = _meta(row).get("fix_help_answer")
        if answer:
            return answer
    return ""


def _reminder(answer):
    one_line = " ".join(answer.split())
    if len(one_line) > REMINDER_CHARS:
        one_line = one_line[:REMINDER_CHARS - 1].rstrip() + "…"
    return _MD_SPECIAL.sub(r"\\\1", one_line)


def _already_posted(attempt_id, run_id):
    for row in db.q("select meta from messages where attempt_id=? and role='system' order by id desc limit 30", (attempt_id,)):
        meta = _meta(row)
        if meta.get("run_id") == run_id and meta.get("fix_help_offer"):
            return True
    return False


def after_review(chat, run, status, verdict):
    """Post the offer after a /prompt review that finished FAIL. Returns the posted message, or None.
    Runner._complete calls it after the report and pass card, inside a try: it must never break finishing a run."""
    if not run or run.get("kind") != "review" or run.get("command") != "prompt" or status != "done" or not verdict:
        return None
    if run.get("actor_admin") or run.get("loadtest") or not _eligible_chat(chat):
        return None
    attempt_id = chat["attempt_id"]
    if _already_posted(attempt_id, run["id"]):
        return None
    if verdict != "FAIL" or prompt_fails(attempt_id) < 2:
        return None
    earlier = saved_answer(attempt_id)
    text = OFFER_AGAIN.format(reminder=_reminder(earlier)) if earlier else OFFER
    meta = {"run_id": run["id"], "fix_help_offer": True}
    msg = db.add_message(attempt_id, "system", text, meta)
    hub.publish(attempt_id, "message", msg)
    log.info("fix helper offer posted for %s", attempt_id)
    return msg


def live_offer(attempt_id):
    """The latest offer, if it still waits for its answer: no plain message has answered it and no /prompt run has
    started since it was posted. Else None."""
    offer = None
    for row in db.q("select id, meta, created_at from messages where attempt_id=? and role='system' "
                    "and meta like '%\"fix_help_offer\"%' order by id desc limit 5", (attempt_id,)):
        if _meta(row).get("fix_help_offer"):
            offer = row
            break
    if not offer:
        return None
    if db.one("select 1 from messages where attempt_id=? and role='user' and id>? and meta like '%\"fix_help_offer_id\"%' "
              "limit 1", (attempt_id, offer["id"])):
        return None
    if db.one("select 1 from runs where attempt_id=? and command='prompt' and created_at>? limit 1",
              (attempt_id, offer["created_at"])):
        return None
    return offer


def _words(text):
    return len(re.findall(r"\w+", text))


def answer_note(session, chat, text):
    """For a plain message that answers the live offer: (note, meta). The note goes before the message in the prompt
    (it ends by introducing the message); meta goes on the saved user message. Otherwise ("", {}).
    send() calls it before saving the message, and only when the message is a normal follow-up (no command, no review
    waiting for an answer)."""
    text = (text or "").strip()
    if session.get("admin") or not text or text.startswith("/") or not _eligible_chat(chat):
        return "", {}
    attempt_id = chat["attempt_id"]
    offer = live_offer(attempt_id)
    if not offer:
        return "", {}
    earlier = saved_answer(attempt_id)
    same = _SAME.match(text)
    rest = text[same.end():].strip() if same else text
    ask_first = False
    if earlier and (not rest or (same and _words(rest) <= SHORT_WORDS) or _words(text) <= SHORT_WORDS):
        answer = stored = earlier
        source = ("Their answer, which they gave an earlier offer on this attempt (their message now, after this note, "
                  "asks to use it again):")
        lead = "Their message now:"
    elif earlier and same:
        answer, stored = earlier, f"{earlier}\n\nUpdate: {rest}"
        source = ("Their answer, which they gave an earlier offer on this attempt (their message now, after this note, "
                  "keeps it and adds an update):")
        lead = "Their message now, with the update:"
    else:
        answer = None  # the message itself is the answer
        ask_first = _words(text) <= SHORT_WORDS
        stored = None if ask_first else text
        source = lead = None
    parts = ["(Portal note, hidden from them: after this attempt's latest /prompt FAIL the portal offered fix help and "
             "asked them what the agent should end up doing and the one thing it should catch that a careless agent "
             "would miss. Their message after this note replies to that offer.)",
             rules_text()]
    if answer is not None:
        parts.append(f"{source}\n<<<\n{answer}\n>>>")
    if ask_first:
        parts.append("They haven't said yet what they meant: before suggesting fixes, ask them the two questions in "
                     "one short message.")
    # The note ends with the answer: their message itself, or the earlier answer and then their message now.
    parts.append(f"(End of portal note.) {lead}" if lead else "Their answer, which is their message in this chat:")
    note = "\n\n".join(parts) + "\n\n"
    meta = {"fix_help_offer_id": offer["id"], "fix_help_answer": stored, "fix_help_reused": bool(answer is not None)}
    log.info("fix helper note attached for %s (offer %s, %s)", attempt_id, offer["id"],
             "earlier answer" if answer is not None else "new answer")
    return note, meta
