"""Admin blocks: a disabled contributor can't use the portal at all; a blocked chat is read-only for its owner."""

import time

from . import auth, db

PERSON, ATTEMPT = "person", "attempt"


def person_keys(user_id, email):
    """A contributor is matched by Outlier ID or by email (name@outlier.ai, without the +outlier suffix)."""
    keys = [user_id] if user_id else []
    if email:
        keys.append("email:" + auth.normalize_email(email))
    return keys


def disabled(user_id, email):
    keys = person_keys(user_id, email)
    if not keys:
        return None
    marks = ",".join("?" * len(keys))
    return db.one(f"select * from blocks where kind='{PERSON}' and key in ({marks}) order by created_at limit 1", keys)


def chat_blocked(attempt_id):
    return db.one(f"select * from blocks where kind='{ATTEMPT}' and key=?", (attempt_id,))


def resolve_person(identifier):
    """Outlier ID or email typed by an admin -> (user_id, email, label). Uses a known chat when there is one."""
    typed = (identifier or "").strip().lower()
    if auth.ID_RE.match(typed):
        chat = db.one("select user_id, email from chats where user_id=? order by last_activity desc limit 1", (typed,))
        return typed, chat["email"] if chat else None, chat["email"] if chat else typed
    if "@" in typed:
        norm = auth.normalize_email(typed)
        for chat in db.q("select user_id, email from chats where email like ? order by last_activity desc", (norm + "%",)):
            if auth.normalize_email(chat["email"] or "") == norm and chat["user_id"]:
                return chat["user_id"], chat["email"], chat["email"]
        return None, norm, norm
    return None, None, None


def add(kind, key, label, reason, by):
    db.ex("insert or replace into blocks(kind, key, label, reason, created_by, created_at) values (?,?,?,?,?,?)",
          (kind, key, label, (reason or "").strip()[:500], by, time.time()))


def remove(kind, key):
    db.ex("delete from blocks where kind=? and key=?", (kind, key))


def view(row):
    return {"kind": row["kind"], "key": row["key"], "label": row["label"], "reason": row["reason"] or "",
            "by": row["created_by"], "at": row["created_at"]} if row else None


def everything():
    return {(r["kind"], r["key"]): r for r in db.q("select * from blocks")}


def person_attempts(user_id, email):
    """Every chat that belongs to this contributor."""
    chats = db.q("select attempt_id, user_id, email from chats")
    keys = set(person_keys(user_id, email))
    return [c["attempt_id"] for c in chats if keys & set(person_keys(c["user_id"], c["email"]))]
