"""A task claim the platform re-serves to someone else keeps its ID, so the same attempt ID can come back with a
new holder (seen 2026-10-16: 6ad07a27 was arnab's on Oct 15 and manan's the next day). The portal keys a chat by
attempt ID, so without this the new holder inherited the previous holder's chat, saved inputs, reports, spend and
pass cards, and the previous holder's login still opened the chat.

archive() moves the previous holder's part of a chat to an archived chat under a synthetic ID ("0000" + 20 hex:
never a real Mongo ID, which starts with its timestamp). The archive keeps their messages, runs, pass cards, /admin
requests, spend flag and chat block, plus a copy of the workspace; it is read-only, and admins still see it. The
real attempt ID is left clean for the new holder.
"""

import hashlib
import shutil
import time
import uuid

from . import db, workspace


CLEARED = ("This chat starts fresh. The task was assigned to someone else before you, and their messages, runs and "
           "saved inputs have been moved out of it. Paste or attach your inputs, or type `/help` for the commands.")


class Busy(Exception):
    """The previous holder still has a run going in this chat."""


def archive_id(attempt_id, user_id):
    return "0000" + hashlib.sha256(f"{attempt_id}:{user_id}:{time.time()}".encode()).hexdigest()[:20]


def archive(attempt_id, before=None, owner=None):
    """Move what happened in this chat before `before` (default: everything) to an archived chat for the previous
    holder, `owner` = (user_id, email) (default: the chat's current owner, right when a new holder signs in).
    Returns the archive ID, or None when there is nothing to move."""
    chat = db.get_chat(attempt_id)
    if not chat:
        return None
    cut = before or time.time() + 1
    active = db.active_run(attempt_id)
    if active and (active["created_at"] or 0) < cut:
        raise Busy(active["id"])
    if not db.one("select 1 from messages where attempt_id=? and created_at<? and role!='system' "
                  "union select 1 from runs where attempt_id=? and created_at<?", (attempt_id, cut, attempt_id, cut)):
        return None  # only the welcome message: nothing of theirs to keep
    owner_id, owner_email = owner or (chat["user_id"], chat["email"])
    aid = archive_id(attempt_id, owner_id)
    now = time.time()
    db.ex("insert into chats(attempt_id, task_id, project_id, user_id, email, session_id, session_started, admin_open, "
          "pending_review_run, created_at, last_activity, archived_from, archived_at) values (?,?,?,?,?,?,0,?,null,?,?,?,?)",
          (aid, chat["task_id"], chat["project_id"], owner_id, owner_email, chat["session_id"],
           chat["admin_open"] if before is None else 0, chat["created_at"], min(chat["last_activity"] or now, cut), attempt_id, now))
    moved_runs = [r["id"] for r in db.q("select id from runs where attempt_id=? and created_at<?", (attempt_id, cut))]
    for table in ("messages", "runs", "proofs", "admin_requests"):
        db.ex(f"update {table} set attempt_id=? where attempt_id=? and created_at<?", (aid, attempt_id, cut))
    flag = db.one("select * from attempt_spend_flags where attempt_id=?", (attempt_id,))
    if flag and (flag["flagged_at"] or 0) < cut:
        db.ex("update attempt_spend_flags set attempt_id=? where attempt_id=?", (aid, attempt_id))
    block = db.one("select * from blocks where kind='attempt' and key=?", (attempt_id,))
    if block and (block["created_at"] or 0) < cut:
        db.ex("update blocks set key=? where kind='attempt' and key=?", (aid, attempt_id))
    if db.one("select 1 from admin_requests where attempt_id=? and status='open'", (aid,)):
        db.update("chats", "attempt_id", aid, admin_open=1)

    # The workspace: the archive gets a full copy; the live one keeps only files written since `before`.
    src, dst = workspace.attempt_dir(attempt_id), workspace.attempt_dir(aid)
    if src.exists():
        workspace.thaw(attempt_id)  # the live chat keeps only its newer files below, so it needs them unpacked
        shutil.copytree(src, dst, symlinks=True)
        if before is None:
            shutil.rmtree(src, ignore_errors=True)
        else:
            for sub in ("task_inputs", "cache", "home"):
                for path in sorted((src / sub).rglob("*"), reverse=True) if (src / sub).exists() else []:
                    try:
                        if path.is_file() or path.is_symlink():
                            if sub == "home" or path.lstat().st_mtime < cut:
                                path.unlink()
                        elif path.is_dir() and not any(path.iterdir()):
                            path.rmdir()
                    except OSError:
                        pass
            for run_id in moved_runs:
                for f in (src / "logs").glob(f"{run_id}.*"):
                    f.unlink(missing_ok=True)
    workspace.ensure_attempt(attempt_id)

    if before is None:
        db.ex("delete from chats where attempt_id=?", (attempt_id,))
    else:  # the current holder keeps the chat row, with fresh assistant sessions and no inherited state
        still_open = bool(db.one("select 1 from admin_requests where attempt_id=? and status='open'", (attempt_id,)))
        pending = chat["pending_review_run"] if chat["pending_review_run"] not in moved_runs else None
        db.update("chats", "attempt_id", attempt_id, session_id=str(uuid.uuid4()), session_started=0,
                  session_guard=None, pending_review_run=pending, admin_open=int(still_open), created_at=cut)
    if not db.one("select 1 from messages where attempt_id=?", (attempt_id,)):
        db.add_message(attempt_id, "system", CLEARED)
    return aid
