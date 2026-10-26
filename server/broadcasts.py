"""Admin broadcasts: a message that pops up over a contributor's chat until they press Acknowledge.

The audience is fixed when the broadcast is sent: everyone who then had a chat that isn't archived (submitted,
released, with someone else or re-served, as on the admin page), or the chosen emails' existing chats, archived or not. People who first sign in later never see it. Tracked per person (platform user ID, else email),
so someone with several tasks acknowledges once. Admin sessions never get the pop-up and never acknowledge for
the contributor.
"""

import re
import time

from . import db, hub

TITLE_MAX = 200
BODY_MAX = 20000
_SUFFIX = re.compile(r"^(.*@[^+@]+)\+[^@]*$")  # "name@gmail.com+outlier" -> "name@gmail.com"


def person(chat):
    return chat.get("user_id") or "email:" + (chat.get("email") or "").strip().lower()


def base_email(email):
    email = (email or "").strip().lower()
    m = _SUFFIX.match(email)
    return m.group(1) if m else email


def _admin_logins():
    """Admin sign-in identities. Chats under exactly these (admin test chats) never count as contributors; real
    contributor accounts carry the platform's +suffix (name@x.com+outlier), so an admin who also tasks still counts."""
    return {(a["email"] or "").lower() for a in db.q("select email from admins")}


def _people(before):
    """person -> {email, attempts} for every contributor chat created before `before` (load-test and admin test
    chats excluded)."""
    admins, out = _admin_logins(), {}
    for chat in db.q("select attempt_id, user_id, email, created_at, last_activity from chats "
                     "where coalesce(task_id, '') != 'loadtest' and coalesce(created_at, 0) <= ? "
                     "order by coalesce(last_activity, created_at)", (before,)):
        email = (chat["email"] or "").strip().lower()
        if "@" not in email or email in admins:
            continue
        entry = out.setdefault(person(chat), {"email": chat["email"], "attempts": []})
        entry["email"] = chat["email"] or entry["email"]
        entry["attempts"].append(chat["attempt_id"])
    return out


def resolve(emails=None, before=None, active=None):
    """The audience: (people, unmatched emails). emails=None means everyone with a chat before `before` that is in
    `active` (the attempt IDs that aren't archived); a person qualifies through any one of their chats."""
    people = _people(before or time.time())
    if emails is None:
        if active is not None:
            people = {k: v for k, v in people.items() if any(a in active for a in v["attempts"])}
        return people, []
    wanted = {base_email(e) for e in emails if e.strip()}
    picked = {k: v for k, v in people.items() if base_email(v["email"]) in wanted or (v["email"] or "").lower() in wanted}
    found = {base_email(v["email"]) for v in picked.values()}
    return picked, sorted(wanted - found)


def create(title, body, emails, admin, active=None):
    now = time.time()
    people, unmatched = resolve(emails, now, active)
    if not people:
        raise ValueError("Nobody to send it to: none of those emails has a chat yet." if emails is not None
                         else "Nobody has a chat yet.")
    audience = "everyone" if emails is None else "emails"
    bid = db.ex("insert into broadcasts(title, body, audience, created_by, created_at) values (?,?,?,?,?)",
                (title, body, audience, admin, now))
    for key, entry in people.items():
        db.ex("insert or ignore into broadcast_targets(broadcast_id, person, email) values (?,?,?)",
              (bid, key, entry["email"]))
    payload = {"id": bid}
    for entry in people.values():
        for attempt in entry["attempts"]:
            hub.publish(attempt, "broadcast", payload)
    return {"id": bid, "people": len(people), "unmatched": unmatched}


def pending(chat):
    """Live broadcasts this chat's person hasn't acknowledged, oldest first."""
    return db.q("select b.id, b.title, b.body, b.created_at from broadcasts b "
                "join broadcast_targets t on t.broadcast_id = b.id "
                "where t.person = ? and t.acked_at is null and b.ended_at is null order by b.id", (person(chat),))


def ack(bid, chat):
    db.ex("update broadcast_targets set acked_at = ?, acked_attempt = ? where broadcast_id = ? and person = ? "
          "and acked_at is null", (time.time(), chat["attempt_id"], bid, person(chat)))


def end(bid, admin):
    db.ex("update broadcasts set ended_at = ?, ended_by = ? where id = ? and ended_at is null", (time.time(), admin, bid))


def admin_list(limit=50):
    out = []
    for b in db.q("select * from broadcasts order by id desc limit ?", (limit,)):
        targets = db.q("select email, acked_at from broadcast_targets where broadcast_id = ? order by email", (b["id"],))
        done = sorted((t for t in targets if t["acked_at"]), key=lambda t: t["acked_at"])
        out.append({**b, "people": len(targets), "acked": len(done),
                    "acked_by": [{"email": base_email(t["email"]), "at": t["acked_at"]} for t in done],
                    "waiting": [base_email(t["email"]) for t in targets if not t["acked_at"]]})
    return out
