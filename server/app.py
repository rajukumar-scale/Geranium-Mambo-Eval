"""Original Conference Eval Portal: login gate, chat API, admin API and pages."""

import asyncio
import base64
import csv
import datetime
import hashlib
import hmac
import io
import json
import logging
import os
import re
import shutil
import time
import uuid

from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, blocks, broadcasts, status, boatrun, budget, config, cost, db, donedebug, fingerprint, handover, hub, notify, progress, proofs, spend, staleness, submissions, taskids, taxo_reader, workspace
from .runner import RUNNER

log = logging.getLogger("portal")
WEB = config.ROOT / "web"
app = FastAPI(title="Original Conference Eval Portal", docs_url=None, redoc_url=None, openapi_url=None)
# Changes whenever the web files do: an open chat tab compares it on every reconnect and reloads after a deploy.
WEB_BUILD = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(WEB.glob("*")) if p.is_file())).hexdigest()[:12]
NO_CACHE = {"Cache-Control": "no-cache"}  # browsers revalidate (cheap 304s), so a deploy shows up on the next load


class Static(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE)
        return response


app.mount("/static", Static(directory=WEB), name="static")

HELP = """**Commands**
- `/injection` review your injection SQL
- `/prompt` review your task prompt (needs persona, business function, competency, task skill and horizon)
- `/hardness` check how hard the task is for Opus (needs one unhinted run: attach its trajectory JSON)
- `/oe` review your Oracle Events
- `/rubric` review your rubric
- `/admin <message>` get a human admin's help
- `/help` show this list

Paste inputs straight into the chat, or attach files with 📎. You can send inputs on their own first; they're saved for this attempt and reused by every command. Messages without a command are answered as follow-ups, for example "why did criterion 4 fail?". Ask for help with any piece of your task (SQL for an injected message, a rubric row, wording for one part, feedback, brainstorming); it just won't write the whole task for you in one go.

Open **Guide** (top right) any time for the steps and for which screen to screenshot as proof."""


@app.on_event("startup")
async def startup():
    problems = config.check()
    if problems:
        raise RuntimeError("Portal configuration problems:\n- " + "\n- ".join(problems))
    db.conn()
    RUNNER.start()
    asyncio.create_task(submissions.loop())
    asyncio.create_task(status.loop())
    log.info("portal started: mode=%s base=%s", config.RUNNER_MODE, config.EVAL_BASE_DIR)


# ---- helpers ------------------------------------------------------------------

def client_ip(request):
    if config.TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


def login_throttled(ip, identifier, attempt_id):
    """10 failures per attempt or per identifier in 15 minutes. Per-IP only with a trusted proxy,
    since a TCP relay (e.g. boat.dev hosting) makes every visitor share one address."""
    since = time.time() - 900
    identifier = (identifier or "").strip()[:200]
    n = db.one("select count(*) n from logins where ok=0 and coalesce(reason,'')!='disabled' and created_at>? "
               "and (identifier=? or attempt_id=?)",
               (since, identifier, (attempt_id or "").strip()[:40] or "-"))["n"]
    if n >= 10:
        return True
    if config.TRUST_PROXY:
        return db.one("select count(*) n from logins where ok=0 and created_at>? and ip=?", (since, ip))["n"] >= 30
    return False


class Disabled(Exception):
    """The contributor was disabled by an admin: every user API answers 403 with disabled=true."""

    def __init__(self, block):
        self.block = block


@app.exception_handler(Disabled)
async def disabled_response(request, error):
    return JSONResponse({"detail": disabled_text(error.block), "disabled": True,
                         "reason": error.block["reason"] or ""}, status_code=403)


def disabled_text(block):
    reason = (block or {}).get("reason")
    return ("An admin has disabled your access to the Original Conference Eval Portal."
            + (f" Reason: {reason}" if reason else ""))


def current_user(request):
    session = auth.unsign(request.cookies.get(auth.USER_COOKIE, ""), config.SESSION_DAYS * 86400)
    chat = db.get_chat(session.get("attempt_id", "")) if session else None
    if not chat:
        raise HTTPException(401, "Please sign in again.")
    if session.get("admin"):
        if not admin_role(session["admin"]):  # that admin login was removed or renamed
            raise HTTPException(401, "Please sign in again.")
        return session  # blocks never apply to admins
    if session.get("user_id") and chat["user_id"] and session["user_id"] != chat["user_id"]:
        raise HTTPException(401, "This attempt has been re-assigned to another contributor. Sign in with your current attempt ID.")
    block = blocks.disabled(session.get("user_id") or chat["user_id"], chat["email"])
    if block:
        raise Disabled(block)
    return session


def chat_writable(session, chat):
    """A blocked chat is read-only for its owner (they can still type /admin); admins can always write.
    An archived chat (the attempt ID was re-served to someone else) is read-only for everyone."""
    if chat.get("archived_from"):
        raise HTTPException(403, "This chat is archived: the task was re-assigned to another contributor, so it can't run "
                                 "reviews any more. Its history and pass cards are kept here.")
    block = blocks.chat_blocked(chat["attempt_id"])
    if block and not session.get("admin"):
        reason = block["reason"]
        raise HTTPException(403, "An admin has blocked this chat, so you can't send messages or start reviews here."
                                 + (f" Reason: {reason}." if reason else "") + " Type /admin <message> to reach an admin.")


def admin_role(email):
    """owner = listed in ADMIN_EMAILS (can manage admins); admin = added from the admin page."""
    if email in config.ADMIN_EMAILS:
        return "owner"
    row = db.one("select password_hash from admins where email=?", (email,))
    return "admin" if row and row["password_hash"] else None


def admin_password_ok(email, password):
    """Owners in ADMIN_EMAILS use ADMIN_PASSWORD (or their own, once set); added admins use their own."""
    if not password:
        return False
    row = db.one("select password_hash from admins where email=?", (email,))
    if row and row["password_hash"] and auth.verify_password(password, row["password_hash"]):
        return True
    return email in config.ADMIN_EMAILS and bool(config.ADMIN_PASSWORD) and \
        hmac.compare_digest(password.encode(), config.ADMIN_PASSWORD.encode())


def current_admin(request, owner=False):
    session = auth.unsign(request.cookies.get(auth.ADMIN_COOKIE, ""), 12 * 3600)
    role = admin_role(session.get("email")) if session else None  # removal takes effect immediately
    if not role:
        raise HTTPException(401, "Admin sign-in required.")
    if owner and role != "owner":
        raise HTTPException(403, "Only the owner can manage admins.")
    return {**session, "role": role}


def for_viewer(session, value):
    """What this session may see: admins everything; contributors no task IDs (they see only attempt IDs)."""
    if session.get("admin"):
        return value
    chat = db.get_chat(session["attempt_id"])
    return taskids.hide(value, extra=[chat["task_id"]] if chat else [])


def viewer_is_admin(request):
    """Is an admin looking (an /admin sign-in, or an admin's chat-page sign-in)? For the public verify page."""
    try:
        current_admin(request)
        return True
    except HTTPException:
        session = auth.unsign(request.cookies.get(auth.USER_COOKIE, ""), config.SESSION_DAYS * 86400)
        return bool(session and session.get("admin") and admin_role(session["admin"]))


def set_cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax", secure=config.COOKIE_SECURE)


def run_view(run):
    if not run:
        return None
    keys = ("id", "kind", "command", "status", "error", "created_at", "started_at", "finished_at", "calls",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_estimate",
            "verdict", "score", "exec_status", "proof_code")
    view = {k: run.get(k) for k in keys}
    view["position"] = RUNNER.position(run["id"])
    view["continuation"] = bool(run.get("continuation"))
    view["milestones"] = json.loads(run.get("milestones") or "{}")
    view["progress"] = progress.calibration(run["command"]) if run.get("kind") == "review" else None
    view["activity"] = RUNNER.activity.get(run["id"], [])[-60:]
    return view


def chat_state(attempt_id):
    chat = db.get_chat(attempt_id)
    return {
        "attempt_id": attempt_id,
        "task_id": chat["task_id"],
        "email": chat["email"],
        "admin_open": bool(chat["admin_open"]),
        "pending_review": (db.get_run(chat["pending_review_run"]) or {}).get("command") if chat.get("pending_review_run") else None,
        "active_run": run_view(db.active_run(attempt_id)),
        "reports": workspace.list_reports(attempt_id),
        "inputs": workspace.saved_inputs(attempt_id),
        # The installed eval asks each task's date first; the chat page asks it with two buttons instead.
        "universe": {"asks": workspace.dated_release(), "date": workspace.universe_date(attempt_id),
                     "choices": {k: label for k, (_, label) in workspace.UNIVERSE_DATES.items()}},
        "limits": {
            "reviews_left": (max(0, config.DAILY_REVIEW_LIMIT - db.runs_today(attempt_id, "review"))
                             if config.DAILY_REVIEW_LIMIT else None),
            "followups_left": (max(0, config.DAILY_FOLLOWUP_LIMIT - db.runs_today(attempt_id, "followup"))
                               if config.DAILY_FOLLOWUP_LIMIT else None),
        },
        # each with "outdated": set when an input its review read has changed since (staleness.check)
        "proofs": staleness.annotate(attempt_id, [json.loads(r["payload"]) for r in
                   db.q("select payload from proofs where attempt_id=? order by created_at", (attempt_id,))]),
        "blocked": blocks.view(blocks.chat_blocked(attempt_id)),
        "disabled": blocks.view(blocks.disabled(chat["user_id"], chat["email"])),  # seen by admins only
    }


async def event_stream(request, key, hello, view=lambda payload: payload):
    q = hub.subscribe(key)
    try:
        yield hub.sse(view({"event": "hello", "attempt_id": None, "data": hello}))
        while not await request.is_disconnected():
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15)
                yield hub.sse(view(payload))
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        hub.unsubscribe(key, q)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ---- pages ------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers=NO_CACHE)


@app.get("/admin")
def admin_page():
    return FileResponse(WEB / "admin.html", headers=NO_CACHE)


@app.get("/admin/cost")
def cost_page():
    return FileResponse(WEB / "cost.html", headers=NO_CACHE)


# ---- status page (public, like status.claude.com; no names, emails or task IDs) ----

@app.get("/status")
@app.get("/status/history")
def status_page():
    return FileResponse(WEB / "status.html", headers=NO_CACHE)


@app.get("/api/status")
async def status_data():
    return JSONResponse(await status.current(), headers=NO_CACHE)


@app.get("/status/feed.rss")
async def status_feed(request: Request):
    base = (config.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    return PlainTextResponse(status.rss(await status.current(), base), media_type="application/rss+xml")


class IncidentBody(BaseModel):
    title: str = ""
    components: list[str] = []
    impact: str = "degraded"
    status: str = "Investigating"
    body: str


@app.get("/api/admin/status/incidents")
def admin_incidents(request: Request):
    current_admin(request)
    return {"incidents": status.manual_incidents(time.time() - 30 * 86400), "components": status.COMPONENTS,
            "statuses": status.MANUAL_STATUSES}


@app.post("/api/admin/status/incidents")
async def admin_post_incident(body: IncidentBody, request: Request):
    admin = current_admin(request)
    title, text = body.title.strip(), body.body.strip()
    comps = [c for c in body.components if c in status.NAMES]
    if not title or not text or not comps:
        raise HTTPException(400, "Write a title and a message, and pick at least one component.")
    if body.impact not in status.IMPACTS or body.status not in status.MANUAL_STATUSES[:-1]:
        raise HTTPException(400, "Unknown impact or status.")
    now = time.time()
    iid = db.ex("insert into status_incidents(title, components, impact, created_at, created_by) values (?,?,?,?,?)",
                (title[:200], json.dumps(comps), body.impact, now, admin["email"]))
    db.ex("insert into status_updates(incident_id, status, body, created_at, created_by) values (?,?,?,?,?)",
          (iid, body.status, text[:4000], now, admin["email"]))
    status.invalidate()
    asyncio.create_task(notify.slack(f"📣 Status incident posted by {admin['email']}: {title} ({body.status})"))
    return {"id": iid}


@app.post("/api/admin/status/incidents/{iid}/update")
async def admin_update_incident(iid: int, body: IncidentBody, request: Request):
    admin = current_admin(request)
    inc = db.one("select * from status_incidents where id=?", (iid,))
    if not inc:
        raise HTTPException(404, "No such incident.")
    if inc["resolved_at"]:
        raise HTTPException(409, "That incident is already resolved.")
    if body.status not in status.MANUAL_STATUSES or not body.body.strip():
        raise HTTPException(400, "Pick a status and write the update.")
    now = time.time()
    db.ex("insert into status_updates(incident_id, status, body, created_at, created_by) values (?,?,?,?,?)",
          (iid, body.status, body.body.strip()[:4000], now, admin["email"]))
    if body.impact in status.IMPACTS:
        db.update("status_incidents", "id", iid, impact=body.impact)
    if body.status == "Resolved":
        db.update("status_incidents", "id", iid, resolved_at=now)
        asyncio.create_task(notify.slack(f"✅ Status incident resolved by {admin['email']}: {inc['title']}"))
    status.invalidate()
    return {"ok": True}


@app.get("/verify")
@app.get("/verify/{code}")
def verify_page(code: str = ""):
    return FileResponse(WEB / "verify.html", headers=NO_CACHE)


@app.get("/healthz")
def healthz():
    return {"ok": True, "mode": config.RUNNER_MODE, "queued": RUNNER.queued(), "running": db.one("select count(*) n from runs where status='running'")["n"]}


# ---- user auth ----------------------------------------------------------------

class LoginBody(BaseModel):
    identifier: str
    attempt_id: str
    password: str = ""  # admins only: opens any attempt


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    ip = client_ip(request)
    if login_throttled(ip, body.identifier, body.attempt_id):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    typed = body.identifier.strip().lower()
    admin_email = typed if admin_role(typed) else None  # an admin username (atlasadmin) or admin email
    attempt_id = body.attempt_id.strip().lower()
    try:
        if admin_email and "@" not in typed:
            # A username isn't an attempt owner, so skip the ownership check; still look the attempt up for its task.
            rows = await auth.redash_attempt(attempt_id) if auth.ID_RE.match(attempt_id) else []
            rows = sorted(rows, key=lambda r: (r.get("KIND") or "attempt") == "task_id")  # owned rows first
            ok, reason, row = False, ("admin" if auth.ID_RE.match(attempt_id) else
                                      "The attempt ID should be 24 characters (0-9, a-f)."), (rows[0] if rows else None)
        else:
            ok, reason, row = await auth.check_login(body.identifier, body.attempt_id)
    except Exception as error:  # noqa: BLE001
        log.warning("login lookup failed: %s", error)
        if not admin_email:
            raise HTTPException(503, "We couldn't reach the attempt database. Please try again in a minute.")
        ok, reason, row = False, "lookup failed", None
    bypass = False
    if not ok and admin_email and auth.ID_RE.match(attempt_id):
        # Admins may open any attempt (not theirs, another project, or not in Redash yet), but only with
        # proof they are the admin: the admin password, or an admin session for this email in this browser.
        session = auth.unsign(request.cookies.get(auth.ADMIN_COOKIE, ""), 12 * 3600)
        if (session and session.get("email") == admin_email) or admin_password_ok(admin_email, body.password):
            ok, bypass, reason = True, True, "admin bypass"
        elif not body.password:
            return JSONResponse({"detail": "This is an admin account. Enter the admin password to open any attempt.",
                                 "need_password": True}, status_code=401)
        else:
            reason = "Wrong admin password."
    block = blocks.disabled(row.get("USER_ID"), row.get("EMAIL_RAW") or row.get("EMAIL_NORM")) if ok and not bypass else None
    if block:
        ok, reason = False, disabled_text(block)
    db.ex("insert into logins(ip, identifier, attempt_id, ok, reason, created_at) values (?,?,?,?,?,?)",
          (ip, body.identifier.strip()[:200], body.attempt_id.strip()[:40], int(ok), "disabled" if block else reason, time.time()))
    if not ok:
        raise HTTPException(403, reason)
    existing = db.get_chat(attempt_id)
    if not row:  # admin bypass on an attempt Redash doesn't have: keep the owner's details if we know them
        row = {"ATTEMPT_ID": attempt_id, "TASK_ID": existing["task_id"] if existing else "unknown",
               "PROJECT_ID": existing["project_id"] if existing else "", "USER_ID": existing["user_id"] if existing else "",
               "EMAIL_RAW": existing["email"] if existing else admin_email}
    attempt_id = row["ATTEMPT_ID"]
    if existing and not bypass and existing["user_id"] and row.get("USER_ID") and existing["user_id"] != row["USER_ID"]:
        # The platform re-served this claim to someone else: the previous holder's chat moves aside.
        try:
            handover.archive(attempt_id)
        except handover.Busy:
            raise HTTPException(409, "This task's previous holder still has a review running in it. Please try again in a few minutes.")
        log.info("attempt %s re-served: archived the previous holder's chat, fresh chat for the new one", attempt_id)
        existing = db.get_chat(attempt_id)
    chat = db.upsert_chat(attempt_id, row["TASK_ID"], row["PROJECT_ID"], row["USER_ID"], row["EMAIL_RAW"],
                          existing["session_id"] if existing else str(uuid.uuid4()))
    workspace.ensure_attempt(attempt_id)
    if not existing:
        db.add_message(attempt_id, "system", "Welcome! " + HELP)
    resp = JSONResponse({"ok": True, "attempt_id": attempt_id, **({"task_id": chat["task_id"]} if bypass else {})})
    payload = {"attempt_id": attempt_id, "user_id": row["USER_ID"], "iat": time.time()}
    if bypass:
        payload["admin"] = admin_email
    set_cookie(resp, auth.USER_COOKIE, auth.sign(payload), config.SESSION_DAYS * 86400)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.USER_COOKIE)
    return resp


# ---- user chat ----------------------------------------------------------------

@app.get("/api/me")
def me(request: Request):
    session = current_user(request)
    return for_viewer(session, {**chat_state(session["attempt_id"]), "admin": session.get("admin"),
                                "spend": spend.status(db.get_chat(session["attempt_id"])),
                                "review_minutes": {c: progress.calibration(c)["minutes"] for c in progress.DEFAULTS}})


class UniverseBody(BaseModel):
    date: str


@app.post("/api/universe")
async def set_universe(body: UniverseBody, request: Request):
    """Save the task's date (which universe the evals read) with the eval's own input tool, in the attempt's
    workspace, so it's exactly what the eval would have saved had it asked."""
    session = current_user(request)
    attempt_id = session["attempt_id"]
    chat_writable(session, db.get_chat(attempt_id))
    if body.date not in workspace.UNIVERSE_DATES:
        raise HTTPException(400, "Pick Oct 1, 2025 or Feb 14, 2026.")
    if not workspace.dated_release():
        raise HTTPException(409, "This eval version has one date only; nothing to pick.")
    if config.RUNNER_MODE != "docker":
        raise HTTPException(501, "Saving the date needs the docker runner.")
    if db.active_run(attempt_id):  # a review on a review box writes the attempt's inputs back when it ends
        raise HTTPException(409, "Wait for the running review to finish, then set the date.")
    cmd = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "--network", "none", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "-w", "/work", *workspace.docker_mounts(attempt_id),
           "--entrypoint", "python3", config.RUNNER_IMAGE, "Evals/tools/hg_store_input.py", "universe", body.date]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0 or workspace.universe_date(attempt_id) != body.date:
        log.warning("saving the task date for %s failed: %s", attempt_id, (err or out).decode(errors="replace")[-300:])
        raise HTTPException(500, "Couldn't save the date. Please try again or ask an admin with /admin.")
    label = workspace.UNIVERSE_DATES[body.date][1]
    m = db.add_message(attempt_id, "system", f"Task date set: **{label}**. Every review of this task reads that universe.",
                       {"universe": body.date})
    hub.publish(attempt_id, "message", m)
    log.info("task date for %s set to %s", attempt_id, body.date)
    return for_viewer(session, {"ok": True, "universe": chat_state(attempt_id)["universe"]})


@app.get("/api/spend")
def my_spend(request: Request):
    """The top bar's spend figure: the attempt owner's total across all their attempts (live during a run)."""
    return spend.status(db.get_chat(current_user(request)["attempt_id"]))


def history_chats(session):
    """The signed-in person's chats: every attempt with their Outlier ID. An admin session sees every
    contributor's chat ("god mode"; admins can see them all on /admin anyway)."""
    if session.get("admin"):
        rows = db.q("select * from chats where coalesce(task_id, '') != 'loadtest' order by last_activity desc limit 500")
        if not any(r["attempt_id"] == session["attempt_id"] for r in rows):
            rows.append(db.get_chat(session["attempt_id"]))
        return sorted(rows, key=lambda r: r["last_activity"] or 0, reverse=True)
    rows = db.q("select * from chats where user_id=? and user_id!=''", (session.get("user_id") or "-",))
    if not any(r["attempt_id"] == session["attempt_id"] for r in rows):
        rows.append(db.get_chat(session["attempt_id"]))
    return sorted(rows, key=lambda r: r["last_activity"] or 0, reverse=True)


@app.get("/api/history")
def history(request: Request):
    session = current_user(request)
    god = bool(session.get("admin"))
    totals = spend.attempt_totals() if god else {}
    flagged = spend.flagged_attempts() if god else {}
    passes = {r["attempt_id"]: r["n"] for r in db.q("select attempt_id, count(*) n from proofs group by attempt_id")} if god else {}
    all_blocks = blocks.everything() if god else {}
    states = _chat_states() if god else {}
    out = []
    for chat in history_chats(session):
        last = db.one("select command, verdict, score from runs where attempt_id=? and kind='review' and verdict is not null "
                      "order by finished_at desc limit 1", (chat["attempt_id"],))
        first = db.one("select content from messages where attempt_id=? and role='user' order by id limit 1", (chat["attempt_id"],))
        out.append({"attempt_id": chat["attempt_id"], "task_id": chat["task_id"], "last_activity": chat["last_activity"],
                    "current": chat["attempt_id"] == session["attempt_id"], "last_review": last,
                    "preview": ((first or {}).get("content") or "")[:80],
                    "blocked": bool(blocks.chat_blocked(chat["attempt_id"])),
                    "email": chat["email"] if god else None, "archived_from": chat.get("archived_from"),
                    "running": bool(db.active_run(chat["attempt_id"])) if god else None})
        if god:
            out[-1].update(admin_open=bool(chat["admin_open"]), passes=passes.get(chat["attempt_id"], 0),
                           spend_usd=(totals.get(chat["attempt_id"]) or {}).get("usd", 0.0),
                           over=chat["attempt_id"] in flagged,
                           archived=(states.get(chat["attempt_id"]) or {}).get("archived"),
                           idle_days=(states.get(chat["attempt_id"]) or {}).get("idle_days"),
                           disabled=any((blocks.PERSON, k) in all_blocks for k in blocks.person_keys(chat["user_id"], chat["email"])))
    return for_viewer(session, out)


class SwitchBody(BaseModel):
    attempt_id: str


# ---- switching between the chat page and /admin (admins only) --------------------
ADMIN_SESSION_SEC = 12 * 3600  # how long an /admin sign-in lasts


class OpenChatBody(BaseModel):
    attempt_id: str = ""


@app.post("/api/admin/open-chat")
def admin_open_chat(body: OpenChatBody, request: Request):
    """From /admin: open a chat on the chat page as this admin (the selected one, else the latest)."""
    admin = current_admin(request)
    chat = db.get_chat(body.attempt_id.strip().lower()) if body.attempt_id else \
        db.one("select * from chats where coalesce(task_id, '') != 'loadtest' order by last_activity desc limit 1")
    if not chat:
        raise HTTPException(404, "There's no chat to open yet.")
    resp = JSONResponse({"ok": True, "attempt_id": chat["attempt_id"]})
    # Lasts no longer than an /admin sign-in, so switching never stretches admin access.
    set_cookie(resp, auth.USER_COOKIE, auth.sign({"attempt_id": chat["attempt_id"], "user_id": chat["user_id"],
                                                  "admin": admin["email"], "iat": time.time()}), ADMIN_SESSION_SEC)
    return resp


@app.post("/api/me/admin-page")
def chat_to_admin(request: Request):
    """From the chat page (signed in as an admin): open /admin without signing in again, if that chat-page
    sign-in is recent enough for an /admin session."""
    session = current_user(request)
    if not session.get("admin"):
        raise HTTPException(403, "Only admins can open the admin page.")
    left = ADMIN_SESSION_SEC - (time.time() - session.get("iat", 0))
    if left <= 0:
        raise HTTPException(401, "Sign in on the admin page.")
    resp = JSONResponse({"ok": True})
    set_cookie(resp, auth.ADMIN_COOKIE, auth.sign({"email": session["admin"], "iat": session["iat"]}), int(left))
    return resp


@app.post("/api/switch")
def switch(body: SwitchBody, request: Request):
    session = current_user(request)
    chat = next((c for c in history_chats(session) if c["attempt_id"] == body.attempt_id), None)
    if not chat:
        raise HTTPException(403, "That chat isn't one of yours. Sign in with its attempt ID instead.")
    payload = {"attempt_id": chat["attempt_id"], "user_id": session.get("user_id") if session.get("admin") else chat["user_id"],
               "iat": time.time()}
    if session.get("admin"):
        payload["admin"] = session["admin"]
    resp = JSONResponse({"ok": True})
    set_cookie(resp, auth.USER_COOKIE, auth.sign(payload), config.SESSION_DAYS * 86400)
    return resp


@app.get("/api/inputs/{key}")
def get_input(key: str, request: Request):
    session = current_user(request)
    found = workspace.read_input(session["attempt_id"], key)
    if not found:
        raise HTTPException(404, "That input isn't saved for this attempt.")
    return for_viewer(session, found)


@app.get("/api/inputs/{key}/file")
def get_input_file(key: str, request: Request, i: int = 0):
    """One saved file in full, byte for byte (the view above stops at 15 MB). Task IDs are still hidden
    from contributors; none of the saved SQLs, prompts, OEs or rubrics contained one on 2026-10-17."""
    session = current_user(request)
    path = workspace.input_file(session["attempt_id"], key, i)
    if not path:
        raise HTTPException(404, "That file isn't saved for this attempt.")
    data = path.read_bytes()
    if not session.get("admin") and b"\0" not in data[:4096]:
        data = for_viewer(session, data.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
    return Response(data, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{re.sub(r"[^A-Za-z0-9._-]", "_", path.name)}"',
                             **NO_CACHE})


@app.get("/api/messages")
def get_messages(request: Request, after: int = 0):
    session = current_user(request)
    attempt_id = session["attempt_id"]
    return for_viewer(session, staleness.annotate_messages(attempt_id, db.messages(attempt_id, after)))


@app.get("/api/events")
async def events(request: Request):
    session = current_user(request)
    attempt_id = session["attempt_id"]
    hello = {"active_run": run_view(db.active_run(attempt_id)), "build": WEB_BUILD}
    return StreamingResponse(event_stream(request, attempt_id, hello, lambda payload: for_viewer(session, payload)),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/upload")
async def upload(request: Request, files: list[UploadFile] = File(...)):
    session = current_user(request)
    attempt_id = session["attempt_id"]
    chat_writable(session, db.get_chat(attempt_id))
    saved = []
    folder = workspace.uploads_dir(attempt_id)
    for item in files[:10]:
        data = await item.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
        if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"{item.filename} is larger than {config.MAX_UPLOAD_MB} MB.")
        name = workspace.safe_filename(item.filename)
        target = folder / name
        stem, suffix, n = target.stem, target.suffix, 2
        while target.exists():
            target = folder / f"{stem} ({n}){suffix}"
            n += 1
        target.write_bytes(data)
        saved.append({"name": target.name, "bytes": len(data)})
    return {"files": saved}


class SendBody(BaseModel):
    text: str = ""
    files: list[str] = []


@app.post("/api/send")
async def send(body: SendBody, request: Request):
    session = current_user(request)
    attempt_id = session["attempt_id"]
    chat = db.get_chat(attempt_id)
    if not session.get("admin") and broadcasts.pending(chat):
        # The pop-up is the UI's job; this also stops a tab opened before it existed, or one that missed it.
        hub.publish(attempt_id, "broadcast", {"id": None})
        raise HTTPException(409, "There's an announcement from the admins waiting for you. Reload this page "
                                 "(Ctrl+R or Cmd+R), read it and press Acknowledge, then send your message again.")
    text = body.text.strip()
    files = []
    folder = workspace.uploads_dir(attempt_id)
    for name in body.files[:10]:
        clean = workspace.safe_filename(name)
        if not (folder / clean).is_file():
            raise HTTPException(400, f"Attachment {name} wasn't uploaded.")
        files.append(clean)
    if not text and not files:
        raise HTTPException(400, "Type a message or attach a file.")
    if len(text) > 400_000:
        raise HTTPException(413, "That message is too long; attach it as a file instead.")

    command, rest = None, text
    if text.startswith("/"):
        first, _, rest = text.partition(" ")
        if "\n" in first:
            first, _, tail = first.partition("\n")
            rest = tail + (" " + rest if rest else "")
        command = first[1:].lower()

    if command == "help":
        user_msg = db.add_message(attempt_id, "user", text)
        hub.publish(attempt_id, "message", user_msg)
        msg = db.add_message(attempt_id, "system", HELP)
        hub.publish(attempt_id, "message", msg)
        return {"ok": True}

    if command == "admin":
        note = rest.strip()
        if not note:
            raise HTTPException(400, "Add a short message after /admin so the admins know what you need.")
        user_msg = db.add_message(attempt_id, "user", text, {"command": "admin"})
        hub.publish(attempt_id, "message", user_msg)
        db.ex("insert into admin_requests(attempt_id, message, status, created_at) values (?,?,?,?)",
              (attempt_id, note, "open", time.time()))
        db.update("chats", "attempt_id", attempt_id, admin_open=1)
        if config.SLACK_ALERT_DETAIL:
            alert = (f":rotating_light: *Eval portal /admin request*\n"
                     f"Attempt `{attempt_id}` · task `{chat['task_id']}` · {chat['email']}\n"
                     f"> {note[:1500]}\nOpen chat: {notify.admin_link(attempt_id)}")
        else:  # no email or message text: the alert channel may be readable by contributors
            alert = (f":rotating_light: New /admin request in the eval portal (attempt `{attempt_id[:8]}…`). "
                     f"Open it on the admin page: {notify.admin_link(attempt_id)}")
        sent = await notify.slack(alert)
        reply = ("Thanks, the admins have been notified and will reply here. You can keep using the eval meanwhile."
                 if sent else "Your request is saved and the admins will see it in their dashboard. (Slack notifications "
                              "aren't set up yet, so a reply may take longer.)")
        msg = db.add_message(attempt_id, "system", reply)
        hub.publish(attempt_id, "message", msg)
        hub.publish(attempt_id, "admin_request", {"attempt_id": attempt_id, "message": note})
        return {"ok": True}

    chat_writable(session, chat)
    if command is not None and command not in config.REVIEW_COMMANDS:
        raise HTTPException(400, f"Unknown command /{command}. Available: /injection, /prompt, /hardness, /oe, /rubric, /admin, /help.")

    kind = "review" if command else "followup"
    pending = db.get_run(chat["pending_review_run"]) if chat.get("pending_review_run") and not command else None
    if db.active_run(attempt_id):
        raise HTTPException(409, "A run is already in progress for this attempt. Wait for it to finish or cancel it.")
    limit = config.DAILY_REVIEW_LIMIT if kind == "review" else config.DAILY_FOLLOWUP_LIMIT
    if limit and db.runs_today(attempt_id, kind) >= limit:  # an answer to a review's question counts as a follow-up
        raise HTTPException(429, f"You've reached today's limit of {limit} {'reviews' if kind == 'review' else 'messages'} "
                                 "for this attempt. Type /admin if you need more.")

    user_msg = db.add_message(attempt_id, "user", text, {"files": files, "command": command})
    hub.publish(attempt_id, "message", user_msg)
    prompt = (f"/{command} {rest}".strip() if command else text) or "(see attached files)"
    if pending:
        # Not every message sent while a review waits is its answer: "revise the injection for me" was treated as
        # review input, and the review's grade-don't-author rules then refused the help.
        prompt = (f"(Portal note: your /{pending['command']} review is paused, waiting for something from them. If this "
                  "message supplies what the review asked for, save it and continue the review. If it is a question or a "
                  "request for help instead, for example to revise their SQL, write or fix a rubric row or an OE step, or "
                  "reword a sentence, do that under the portal's helping rules (writing and revising pieces of their work "
                  f"is allowed), then say in one line what the review still needs.)\n\n{prompt}")
    if files:
        listing = "\n".join(f"- Evals/task_inputs/_uploads/{name}" for name in files)
        prompt += f"\n\n[Files attached to this message, already saved in the workspace:\n{listing}\n]"
    run_id = uuid.uuid4().hex
    (workspace.ensure_attempt(attempt_id) / "logs" / f"{run_id}.prompt.txt").write_text(prompt)
    if pending:
        db.ex("insert into runs(id, attempt_id, kind, command, continuation, session_id, status, created_at)"
              " values (?,?,?,?,?,?,?,?)", (run_id, attempt_id, "review", pending["command"], 1,
                                            pending["session_id"], "queued", time.time()))
    else:
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at) values (?,?,?,?,?,?)",
              (run_id, attempt_id, kind, command, "queued", time.time()))
    admin = session.get("admin")
    db.update("runs", "id", run_id, task_id=chat["task_id"], actor_admin=int(bool(admin)),
              actor_email=admin or chat["email"], actor_user_id=None if admin else (session.get("user_id") or chat["user_id"]))
    if command:
        db.update("chats", "attempt_id", attempt_id, pending_review_run=None)
    RUNNER.submit(run_id)
    return for_viewer(session, {"ok": True, "run": run_view(db.get_run(run_id))})


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    attempt_id = current_user(request)["attempt_id"]
    run = db.get_run(run_id)
    if not run or run["attempt_id"] != attempt_id:
        raise HTTPException(404, "No such run.")
    return {"ok": await RUNNER.cancel(run_id)}


@app.get("/api/reports/{name}")
def report(name: str, request: Request):
    session = current_user(request)
    attempt_id = session["attempt_id"]
    if name not in workspace.STAGE_BY_FILE:
        raise HTTPException(404, "No such report.")
    text = workspace.read_report(attempt_id, name)
    if text is None:
        raise HTTPException(404, "That report hasn't been written yet.")
    return PlainTextResponse(for_viewer(session, text), media_type="text/markdown; charset=utf-8")


# ---- verification (public) ------------------------------------------------------

@app.get("/api/verify/{code}")
def verify(code: str, request: Request):
    payload = proofs.lookup(code)
    if not payload:
        raise HTTPException(404, "Unknown verification code.")
    payload["outdated"] = staleness.check(payload)  # after the signature check: computed now, not part of the record
    return payload if viewer_is_admin(request) else taskids.hide(payload)


# ---- pass image: read back like the taxonomy, copy what passed ------------------------

def proof_for_viewer(code, request):
    """A pass the caller may act on: their own chat's (or any, for an admin)."""
    payload = proofs.lookup(code)
    if not payload or not payload.get("valid"):
        raise HTTPException(404, "Unknown pass.")
    try:
        session = current_user(request)
        if session.get("admin") or session["attempt_id"] == payload["attempt_id"]:
            return payload
    except (HTTPException, Disabled):
        pass
    try:
        current_admin(request)
        return payload
    except HTTPException:
        raise HTTPException(404, "Unknown pass.")


class PassImageBody(BaseModel):
    image: str  # data:image/png;base64,...


READER_CALLS = {}  # attempt id -> recent reader-check times (in memory; a restart just resets the hourly cap)


@app.post("/api/proofs/{code}/reader-check")
async def proof_reader_check(code: str, body: PassImageBody, request: Request):
    """Read the portal's own pass image with the taxonomy's reader (same model, same prompt) before the
    contributor downloads it, so the image they upload is one the taxonomy reads correctly."""
    payload = proof_for_viewer(code, request)
    now = time.time()
    recent = [t for t in READER_CALLS.get(payload["attempt_id"], []) if now - t < 3600]
    if len(recent) >= config.READER_CHECKS_PER_HOUR:
        raise HTTPException(429, "Too many pass image checks this hour. Try again later.")
    READER_CALLS[payload["attempt_id"]] = recent + [now]
    head, _, data = body.image.partition(",")
    try:
        png = base64.b64decode(data, validate=True)
    except ValueError:
        raise HTTPException(400, "That isn't an image.")
    if head != "data:image/png;base64" or not png.startswith(b"\x89PNG") or len(png) > 6_000_000:
        raise HTTPException(400, "That isn't a pass image.")
    try:
        result = await taxo_reader.check(payload, png)
    except Exception as exc:  # the reader is down or unreachable: the image is still the portal's own
        log.warning("reader check failed for %s: %s", code, exc)
        return {"ok": None, "error": "The check couldn't run right now."}
    log.info("reader check %s %s: ok=%s read=%r", payload["attempt_id"], payload["stage"], result["ok"], result["read"])
    return result


@app.get("/api/proofs/{code}/input")
def proof_input(code: str, request: Request):
    """The exact text this pass graded, to paste into the taxonomy, while the saved input is still that version."""
    payload = proof_for_viewer(code, request)
    stage = payload["stage"]
    if stage not in ("injection", "prompt", "oe"):
        raise HTTPException(404, "Only the injection, prompt and oracle events can be copied.")
    if fingerprint.for_attempt(payload["attempt_id"], stage) != payload.get("fingerprint"):
        raise HTTPException(409, f"Your saved {stage} has changed since this pass, so this pass no longer matches it. "
                                 f"Run /{stage} again on the version you want to submit.")
    entry = workspace.read_input(payload["attempt_id"], stage, limit=200_000_000)
    text = "\n".join(f["text"] for f in (entry or {}).get("files", []) if f.get("text") is not None)
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


# ---- admin -------------------------------------------------------------------

class AdminLogin(BaseModel):
    email: str
    password: str


@app.post("/api/admin/login")
def admin_login(body: AdminLogin, request: Request):
    ip = client_ip(request)
    if login_throttled(ip, "admin:" + body.email.strip().lower(), None):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    email = body.email.strip().lower()
    row = db.one("select password_hash from admins where email=?", (email,))
    ok = admin_password_ok(email, body.password)
    if ok and row:
        db.update("admins", "email", email, last_login=time.time())
    db.ex("insert into logins(ip, identifier, attempt_id, ok, reason, created_at) values (?,?,?,?,?,?)",
          (ip, "admin:" + email[:200], None, int(ok), "admin", time.time()))
    if not ok:
        raise HTTPException(403, "Wrong username or password.")
    resp = JSONResponse({"ok": True})
    set_cookie(resp, auth.ADMIN_COOKIE, auth.sign({"email": email, "iat": time.time()}), 12 * 3600)
    return resp


@app.post("/api/admin/logout")
def admin_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.ADMIN_COOKIE)
    return resp


# Which review step a saved input belongs to: saving one after its review means they're back working on that step.
INPUT_STEP = {"injection": "injection", "task-details": "prompt", "prompt": "prompt", "dynamic-context": "prompt",
              "human-time": "prompt", "error-async": "prompt", "opus": "hardness", "haiku": "hardness", "oe": "oe", "rubric": "rubric"}


def _input_edits(attempt):
    """(when, step) for each saved input file, from its last-modified time (every save rewrites the file)."""
    root = workspace.attempt_dir(attempt) / "task_inputs"
    try:
        inputs = json.loads((root / "inputs.json").read_text()).get("inputs", {})
    except (OSError, ValueError):
        return []
    edits = []
    for key, entry in inputs.items():
        step = INPUT_STEP.get(key)
        for f in (entry.get("files") or []) if step else []:
            path = root / str(f.get("path", "")).split("task_inputs/", 1)[-1]
            if path.is_file():
                edits.append((path.stat().st_mtime, step))
    return edits


ARCHIVE_REASONS = ("submitted", "released", "with someone else")
TASK_ID_RE = re.compile(r"^[0-9a-f]{24}$")


def attempt_states(chats):
    """Each chat's place on the admin page: {latest, last_work, running, archived, idle_days} by attempt ID.

    latest = the attempt's latest activity: the review it ran most recently (on it / running / passed), or the step
    whose input it saved after that (inputs saved before any review don't place it). Reviews an admin ran don't count.
    last_work = when that was (now while a review runs; the last message if there's no review yet).

    archived = why the chat is finished (it stays listed, under Archived), or None:
    - "submitted": the contributor submitted the task after their last work, whatever the last verdict;
    - "with someone else": the task is claimed by, or was submitted by, another contributor since; or the attempt
      ID was re-served and this is the earlier holder's chat;
    - "released": no claim and no submission since their last work (only on fresh platform data, and only after
      RELEASE_GRACE_HOURS without work, since the claim data lags a little).
    A chat with a review running or an open /admin request is never archived, and neither is one whose contributor
    holds the task now (e.g. it came back to them for rework). Computed on every call, so it undoes itself.
    idle_days = days since the last work, for a chat that isn't archived, has nothing running and no open /admin
    request, and hasn't been worked on for IDLE_DAYS (they still hold the task, they just stopped)."""
    chats = list(chats)
    wanted = {c["attempt_id"] for c in chats}
    runs = {}
    for r in db.q("select id, attempt_id, command, status, created_at, finished_at from runs where kind='review' and command is not null "
                  "and coalesce(loadtest, 0)=0 and coalesce(actor_admin, 0)=0"):
        if r["attempt_id"] in wanted:
            runs.setdefault(r["attempt_id"], []).append(r)
    busy = {r["attempt_id"] for r in db.q("select distinct attempt_id from runs where status in ('queued','running')")}
    passing_runs, rubric_at = set(), {}
    for p in db.q("select p.attempt_id, p.run_id, p.payload, p.created_at from proofs p join runs r on r.id = p.run_id where coalesce(r.actor_admin, 0)=0"):
        stage = json.loads(p["payload"]).get("stage")
        passing_runs.add((p["run_id"], stage))
        if stage == "rubric":
            rubric_at[p["attempt_id"]] = max(rubric_at.get(p["attempt_id"], 0), p["created_at"])
    now, fresh = time.time(), submissions.fresh()
    out = {}
    for chat in chats:
        attempt, task, user = chat["attempt_id"], chat["task_id"], chat["user_id"]
        events = [(float("inf"), r["command"], "running") if r["status"] in ("queued", "running")
                  else (r["finished_at"] or r["created_at"] or 0, r["command"], "passed" if (r["id"], r["command"]) in passing_runs else "on it")
                  for r in runs.get(attempt, [])]
        if events:
            events += [(when, step, "on it") for when, step in _input_edits(attempt)]
        latest = max(events, key=lambda e: e[0]) if events else None
        running = attempt in busy
        last_work = now if running else max((e[0] for e in events), default=0) or chat["last_activity"] or 0
        submitted = submissions.submitted_at(task, user)
        archived = None
        if running or chat.get("admin_open"):
            pass
        elif chat.get("archived_from"):
            archived = "with someone else"
        elif submissions.holds_claim(task, user):
            pass
        elif (submitted and submitted >= last_work) or (latest and latest[1:] == ("rubric", "passed")
                                                        and submissions.submitted_after(task, user, rubric_at.get(attempt))):
            archived = "submitted"
        elif submissions.taken_by_other(task, user, chat["last_activity"]):
            archived = "with someone else"
        elif fresh and TASK_ID_RE.match(task or "") and now - last_work > config.RELEASE_GRACE_HOURS * 3600:
            archived = "released"
        quiet = (now - last_work) / 86400
        out[attempt] = {"latest": latest, "last_work": last_work, "running": running, "archived": archived,
                        "idle_days": round(quiet, 1) if not (archived or running or chat.get("admin_open")) and quiet >= config.IDLE_DAYS else None}
    return out


def _chat_states():
    return attempt_states(db.q("select attempt_id, task_id, user_id, email, last_activity, admin_open, archived_from from chats"))


def step_funnel(states=None):
    """Where contributors are now: each person counted once, at the furthest step among their active chats (ties go
    to the most recent chat), each chat at its latest activity (see attempt_states). So "passed /rubric" means the
    rubric pass is the last thing they did; rerunning or editing an earlier step afterwards puts them back at that
    step. Someone with no active chat is counted once in the Idle row (they still hold a task but stopped working on
    it; parts = the step they stopped at), or else in the Archived row (all their tasks are finished), at their latest
    such chat. Chats owned by an admin account (or with no owner) are left out."""
    order = list(config.REVIEW_COMMANDS)
    admins = set(config.ADMIN_EMAILS) | {r["email"] for r in db.q("select email from admins")}
    chats = {c["attempt_id"]: c for c in db.q("select attempt_id, task_id, user_id, email, last_activity from chats where coalesce(task_id, '') != 'loadtest'")
             if (c["user_id"] or c["email"]) and (c["email"] or "").lower() not in admins}
    states = states if states is not None else _chat_states()
    rank = {"on it": 0, "running": 1, "passed": 2}
    people, idle, archived = {}, {}, {}
    person = lambda chat: chat["user_id"] or (chat["email"] or "").lower()
    for attempt, chat in chats.items():
        st = states.get(attempt) or attempt_states([chat])[attempt]
        who = person(chat) or attempt
        latest = st["latest"]
        step = latest[1] if latest and latest[1] in order else "none"
        if st["archived"] or st["idle_days"] is not None:
            pile, state = (archived, st["archived"]) if st["archived"] else (idle, step)
            if who not in pile or st["last_work"] > pile[who]["last_work"]:
                pile[who] = {"state": state, "last_work": st["last_work"], "email": chat["email"], "attempt": attempt}
            continue
        where = (order.index(latest[1]), rank[latest[2]], latest[2]) if step != "none" else (-1, 0, None)
        key = where[:2] + (st["last_work"],)
        if who not in people or key > people[who]["key"]:
            people[who] = {"where": where, "key": key, "email": chat["email"], "attempt": attempt}
    rows = [{"step": None, "total": 0, "emails": [], "attempts": []}] + \
        [{"step": s, "total": 0, "on it": 0, "running": 0, "passed": 0, "emails": [], "attempts": []} for s in order]
    for person in people.values():
        i, _, state = person["where"]
        row = rows[i + 1]
        row["total"] += 1
        if state:
            row[state] += 1
        row["emails"].append(person["email"] or "unknown")
        row["attempts"].append({"attempt": person["attempt"], "state": state})  # clicking the row lists these chats
        if state == "passed":
            row.setdefault("passed_attempts", []).append(person["attempt"])
    stopped = {"step": "idle", "total": 0, **{s: 0 for s in ["none", *order]}, "emails": [], "attempts": []}
    gone = {"step": "archived", "total": 0, **{r: 0 for r in ARCHIVE_REASONS}, "emails": [], "attempts": []}
    counted = set(people)
    for row, pile in ((stopped, idle), (gone, archived)):  # each person once: active first, then idle, then archived
        for who, a in pile.items():
            if who in counted:
                continue
            counted.add(who)
            row["total"] += 1
            row[a["state"]] += 1
            row["emails"].append(a["email"] or "unknown")
            row["attempts"].append({"attempt": a["attempt"], "state": a["state"]})
    rows += [stopped, gone]
    for row in rows:
        row["emails"] = sorted(row["emails"])[:40]
    return {"people": len(people), "rows": rows, "submissions_checked_at": submissions.state["checked_at"],
            "idle_days": config.IDLE_DAYS}


@app.get("/api/admin/rubric-done")
async def admin_rubric_done(request: Request, refresh: int = 0):
    """The "done, not submitted" debugger: what happened since each of those people passed /rubric."""
    current_admin(request)
    rubric = next((r for r in step_funnel()["rows"] if r["step"] == "rubric"), {})
    return await donedebug.diagnose(rubric.get("passed_attempts", []), force=bool(refresh))


@app.get("/api/admin/chats")
def admin_chats(request: Request):
    me_admin = current_admin(request)
    rows = db.q("""select c.*, (select content from messages m where m.attempt_id=c.attempt_id and m.role in ('user','admin')
                   order by id desc limit 1) as last_text,
                   (select status from runs r where r.attempt_id=c.attempt_id order by created_at desc limit 1) as last_run_status,
                   (select count(*) from runs r where r.attempt_id=c.attempt_id) as run_count,
                   (select coalesce(sum(cost_estimate),0) from runs r where r.attempt_id=c.attempt_id) as cost,
                   (select count(*) from runs r where r.attempt_id=c.attempt_id and r.status in ('queued','running')) as active_runs,
                   (select count(*) from proofs p where p.attempt_id=c.attempt_id) as passes,
                   (select r.command || ' ' || r.verdict from runs r where r.attempt_id=c.attempt_id and r.kind='review'
                    and r.verdict is not null order by r.finished_at desc limit 1) as last_review
                   from chats c order by c.admin_open desc, c.last_activity desc limit 500""")
    flagged = spend.flagged_attempts()
    totals = spend.attempt_totals()
    all_blocks = blocks.everything()
    states = _chat_states()
    for r in rows:
        st = states.get(r["attempt_id"]) or {}
        r["archived"], r["idle_days"] = st.get("archived"), st.get("idle_days")
        r["spend_usd"] = (totals.get(r["attempt_id"]) or {}).get("usd", 0.0)  # what the per-attempt limit counts
        r["last_text"] = (r["last_text"] or "")[:160]
        r["blocked"] = (blocks.ATTEMPT, r["attempt_id"]) in all_blocks
        r["disabled"] = any((blocks.PERSON, k) in all_blocks for k in blocks.person_keys(r["user_id"], r["email"]))
        hit = flagged.get(r["attempt_id"])
        r["spend_flag"] = {"spend_usd": hit["spend_usd"], "flagged_at": hit["flagged_at"]} if hit else None
    since = time.time() - 86400
    stats = db.one("select count(*) runs, coalesce(sum(cost_estimate),0) cost, coalesce(sum(calls),0) calls "
                   "from runs where created_at>?", (since,))
    return {"chats": rows, "stats_24h": stats, "queued": RUNNER.queued(), "running": db.one("select count(*) n from runs where status='running'")["n"],
            "slack": notify.configured(), "me": {"email": me_admin["email"], "role": me_admin["role"]},
            "spend_limit": config.CB_SPEND_LIMIT_USD, "flagged": len(flagged), "steps": step_funnel(states)}


SPEND_COLUMNS = ("key", "users", "emails", "tasks", "attempts", "runs", "reviews", "followups", "admin_runs", "calls",
                 "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "spend_usd",
                 "first_run", "last_run", "limit_spend_usd", "flagged", "disabled")


@app.get("/api/admin/spend")
def admin_spend(request: Request, group: str = "user", days: int = 30, format: str = "json"):
    current_admin(request)
    if group not in ("user", "task", "attempt", "day"):
        raise HTTPException(400, "group must be user, task, attempt or day")
    since = time.time() - days * 86400 if days > 0 else 0
    runs = db.q("""select r.id, r.attempt_id, r.kind, r.created_at, r.calls, r.input_tokens, r.output_tokens,
                   r.cache_read_tokens, r.cache_creation_tokens, r.cost_estimate, r.models, r.actor_admin,
                   coalesce(r.task_id, c.task_id) task_id, coalesce(r.actor_email, c.email) email,
                   case when r.actor_admin=1 then null else coalesce(r.actor_user_id, c.user_id) end user_id
                   from runs r left join chats c on c.attempt_id=r.attempt_id where r.created_at>=?""", (since,))
    groups = {}
    for r in runs:
        day = datetime.datetime.fromtimestamp(r["created_at"], datetime.timezone.utc).strftime("%Y-%m-%d")
        key = {"user": r["user_id"] or r["email"] or "unknown", "task": r["task_id"] or "unknown",
               "attempt": r["attempt_id"], "day": day}[group]
        g = groups.setdefault(key, {"key": key, "users": set(), "emails": set(), "tasks": set(), "attempts": set(),
                                    "runs": 0, "reviews": 0, "followups": 0, "admin_runs": 0, "calls": 0,
                                    "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                                    "cache_creation_tokens": 0, "spend_usd": 0.0, "first_run": r["created_at"],
                                    "last_run": r["created_at"]})
        for field, value in (("users", r["user_id"]), ("emails", r["email"]), ("tasks", r["task_id"]),
                             ("attempts", r["attempt_id"])):
            if value:
                g[field].add(value)
        g["runs"] += 1
        g["reviews" if r["kind"] == "review" else "followups"] += 1
        g["admin_runs"] += r["actor_admin"] or 0
        for field in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
            g[field] += r[field] or 0
        g["spend_usd"] += spend.run_spend(r)
        g["first_run"] = min(g["first_run"], r["created_at"])
        g["last_run"] = max(g["last_run"], r["created_at"])
    rows = sorted(groups.values(), key=lambda g: (g["key"] if group == "day" else -g["spend_usd"]))
    if group == "day":
        rows.reverse()
    # The limit is per attempt and counts the attempt's whole life, whatever period is shown:
    # By attempt shows that attempt's total, By user the person's highest attempt.
    flagged = spend.flagged_attempts()
    totals = spend.attempt_totals() if group in ("user", "attempt") else {}
    for g in rows:
        for field in ("users", "emails", "tasks", "attempts"):
            g[field] = sorted(g[field])
        g["spend_usd"] = round(g["spend_usd"], 4)
        g["limit_spend_usd"], g["flagged"], g["disabled"] = "", "", ""
        if group == "attempt" and g["key"] in totals:
            g["limit_spend_usd"] = totals[g["key"]]["usd"]
            g["flagged"] = "yes" if g["key"] in flagged else ""
        elif group == "user" and g["key"] != "unknown" and g["admin_runs"] < g["runs"]:  # admins have no limit
            user_id, email = (None, g["key"]) if "@" in g["key"] else (g["key"], None)
            mine = [a for a, t in totals.items() if (t["user_id"] or t["email"]) == g["key"]]
            g["limit_spend_usd"] = max((totals[a]["usd"] for a in mine), default=0.0)
            g["flagged"] = str(sum(a in flagged for a in mine) or "")
            g["disabled"] = "yes" if blocks.disabled(user_id, email) else ""
    total = {"runs": sum(g["runs"] for g in rows), "spend_usd": round(sum(g["spend_usd"] for g in rows), 4),
             "calls": sum(g["calls"] for g in rows)}
    if format != "csv":
        return {"group": group, "days": days, "rows": rows, "total": total, "limit": config.CB_SPEND_LIMIT_USD}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(SPEND_COLUMNS)
    for g in rows:
        writer.writerow([";".join(g[c]) if isinstance(g[c], list) else
                         (datetime.datetime.fromtimestamp(g[c], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                          if c in ("first_run", "last_run") else g[c]) for c in SPEND_COLUMNS])
    name = f"eval-portal-spend-by-{group}-{'all' if days <= 0 else f'{days}d'}.csv"
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/admin/chat/{attempt_id}")
def admin_chat(attempt_id: str, request: Request):
    current_admin(request)
    if not db.get_chat(attempt_id):
        raise HTTPException(404, "No such chat.")
    state = chat_state(attempt_id)
    state["messages"] = staleness.annotate_messages(attempt_id, db.messages(attempt_id))
    state["runs"] = [run_view(r) | {"models": json.loads(r["models"] or "{}")} for r in
                     db.q("select * from runs where attempt_id=? order by created_at desc limit 100", (attempt_id,))]
    state["admin_requests"] = db.q("select * from admin_requests where attempt_id=? order by id desc", (attempt_id,))
    state["spend"] = spend.status(db.get_chat(attempt_id))
    state["archived_from"] = db.get_chat(attempt_id).get("archived_from")
    state["broadcasts_waiting"] = [{"id": b["id"], "title": b["title"], "created_at": b["created_at"]}
                                   for b in broadcasts.pending(db.get_chat(attempt_id))]
    state["override_options"] = override_options(attempt_id)
    return state


def _latest_reports(attempt_id):
    """stage -> the newest finished review report for it (the report message's meta, with its run_id)."""
    latest = {}
    for m in db.q("select meta from messages where attempt_id=? and role='report' order by id desc", (attempt_id,)):
        meta = json.loads(m["meta"] or "{}")
        if meta.get("stage") in config.REVIEW_COMMANDS and meta.get("run_id") and meta["stage"] not in latest:
            latest[meta["stage"]] = meta
    return latest


def override_options(attempt_id):
    """Stages whose newest review got no pass card: it didn't pass, or it passed but PARTIAL (no automatic card).
    That's what an admin can issue a pass for. same_input = the saved input still matches what that review graded
    (a pass needs that)."""
    out = []
    for stage, meta in _latest_reports(attempt_id).items():
        run = db.get_run(meta["run_id"])
        if not run or run["status"] != "done":
            continue
        if meta.get("verdict") in config.PASSING_VERDICTS and meta.get("exec_status") == "COMPLETE":
            continue
        if db.one("select 1 from proofs where run_id=? and payload like ?", (run["id"], f'%"stage": "{stage}"%')):
            continue
        out.append({"run_id": run["id"], "stage": stage, "verdict": meta.get("verdict"), "score": meta.get("score"),
                    "finished_at": run["finished_at"],
                    "same_input": fingerprint.for_attempt(attempt_id, stage) == meta.get("fingerprint")})
    return sorted(out, key=lambda o: config.REVIEW_COMMANDS.index(o["stage"]))


class OverrideBody(BaseModel):
    run_id: str
    stage: str
    reason: str


@app.post("/api/admin/chat/{attempt_id}/override-pass")
def admin_override_pass(attempt_id: str, body: OverrideBody, request: Request):
    """Issue a pass card over a review that didn't pass. Signed like any pass, but marked as an admin override
    (who, why, and the review's own verdict) on the pass screen and on /verify."""
    admin = current_admin(request)
    chat = db.get_chat(attempt_id)
    if not chat:
        raise HTTPException(404, "No such chat.")
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(400, "Write the reason; it's shown on the pass card.")
    if db.active_run(attempt_id):
        raise HTTPException(409, "A run is going on this chat. Wait for it to finish.")
    option = next((o for o in override_options(attempt_id) if o["run_id"] == body.run_id and o["stage"] == body.stage), None)
    if not option:
        raise HTTPException(409, "That review isn't the newest non-passing one for its stage any more, or it already has a pass. Reload.")
    if not option["same_input"]:
        raise HTTPException(409, f"The saved {body.stage} input changed after that review, so the card's fingerprint wouldn't "
                                 "match. Run the review on the current version first.")
    meta = _latest_reports(attempt_id)[body.stage]
    run = db.get_run(body.run_id)
    payload = proofs.create(run, chat, meta, override={"by": admin["email"], "reason": reason})
    msg = db.add_message(attempt_id, "proof", f"{payload['stage']} {payload['verdict']}", payload)
    hub.publish(attempt_id, "message", msg)
    hub.publish(attempt_id, "proof", payload)
    note = db.add_message(attempt_id, "admin", f"An admin issued a /{body.stage} pass for this version. "
                                               "Press **Show pass screen** on the card above to open it.", {"admin": admin["email"]})
    hub.publish(attempt_id, "message", note)
    log.info("admin %s issued an override %s pass on %s (run %s): %s", admin["email"], body.stage, attempt_id, body.run_id, reason)
    return {"code": payload["code"], "fingerprint": payload["fingerprint"]}


class AdminReply(BaseModel):
    text: str


@app.post("/api/admin/chat/{attempt_id}/reply")
def admin_reply(attempt_id: str, body: AdminReply, request: Request):
    admin = current_admin(request)
    if not db.get_chat(attempt_id) or not body.text.strip():
        raise HTTPException(400, "Nothing to send.")
    msg = db.add_message(attempt_id, "admin", body.text.strip(), {"admin": admin["email"]})
    hub.publish(attempt_id, "message", msg)
    return {"ok": True}


@app.post("/api/admin/chat/{attempt_id}/resolve")
def admin_resolve(attempt_id: str, request: Request):
    admin = current_admin(request)
    db.update("chats", "attempt_id", attempt_id, admin_open=0)
    db.ex("update admin_requests set status='resolved', resolved_at=?, resolved_by=? where attempt_id=? and status='open'",
          (time.time(), admin["email"], attempt_id))
    msg = db.add_message(attempt_id, "system", "An admin marked your request as resolved.")
    hub.publish(attempt_id, "message", msg)
    return {"ok": True}


@app.post("/api/admin/runs/{run_id}/cancel")
async def admin_cancel(run_id: str, request: Request):
    current_admin(request)
    return {"ok": await RUNNER.cancel(run_id)}


class BlockBody(BaseModel):
    kind: str             # "attempt" = block one chat; "person" = disable the contributor everywhere
    attempt_id: str = ""  # the chat (for "person": its owner)
    identifier: str = ""  # "person" only, instead of attempt_id: an Outlier ID or email
    reason: str = ""
    stop_runs: bool = True


def _note(attempt_id, text, warn=False):
    msg = db.add_message(attempt_id, "system", text, {"access": True, "warn": warn})
    hub.publish(attempt_id, "message", msg)


@app.post("/api/admin/block")
async def admin_block(body: BlockBody, request: Request):
    admin = current_admin(request)
    reason = body.reason.strip()
    chat = db.get_chat(body.attempt_id.strip().lower()) if body.attempt_id.strip() else None
    if body.attempt_id.strip() and not chat:
        raise HTTPException(404, "No such chat.")
    if body.kind == blocks.ATTEMPT:
        if not chat:
            raise HTTPException(400, "Pick the chat to block.")
        blocks.add(blocks.ATTEMPT, chat["attempt_id"], chat["email"], reason, admin["email"])
        attempts = [chat["attempt_id"]]
        _note(chat["attempt_id"], "🚫 **An admin blocked this chat.** You can still read it, but you can't send messages "
                                  "or start reviews here." + (f" Reason: {reason}." if reason else "")
                                  + " Type `/admin <message>` if you have questions.", warn=True)
    elif body.kind == blocks.PERSON:
        user_id, email, label = ((chat["user_id"], chat["email"], chat["email"]) if chat
                                 else blocks.resolve_person(body.identifier))
        if not user_id and not email:
            raise HTTPException(400, "Enter their Outlier ID (24 characters) or their email.")
        key = user_id or "email:" + auth.normalize_email(email)
        blocks.add(blocks.PERSON, key, label, reason, admin["email"])
        attempts = blocks.person_attempts(user_id, email)
        if chat:
            _note(chat["attempt_id"], "🚫 An admin disabled this contributor's access to the portal."
                                      + (f" Reason: {reason}." if reason else ""), warn=True)
    else:
        raise HTTPException(400, "kind must be attempt or person")
    stopped = 0
    if body.stop_runs:
        for attempt in attempts:  # only their own runs; a review an admin started keeps going
            for run in db.q("select id from runs where attempt_id=? and status in ('queued','running') "
                            "and coalesce(actor_admin,0)=0", (attempt,)):
                stopped += bool(await RUNNER.cancel(run["id"]))
    for attempt in attempts:
        hub.publish(attempt, "access", {"kind": body.kind})  # open tabs re-check right away
    log.warning("%s %s %s (reason: %s; %d runs stopped)", admin["email"],
                "blocked chat" if body.kind == blocks.ATTEMPT else "disabled", attempts if body.kind == blocks.ATTEMPT
                else (user_id or email), reason or "-", stopped)
    return {"ok": True, "stopped": stopped, "attempts": len(attempts)}


class UnblockBody(BaseModel):
    kind: str
    key: str
    attempt_id: str = ""  # the chat the admin did it from, for the note


@app.post("/api/admin/unblock")
def admin_unblock(body: UnblockBody, request: Request):
    admin = current_admin(request)
    row = db.one("select * from blocks where kind=? and key=?", (body.kind, body.key))
    if not row:
        raise HTTPException(404, "That block isn't there any more.")
    blocks.remove(body.kind, body.key)
    if body.kind == blocks.ATTEMPT:
        attempts = [body.key]
        _note(body.key, "✅ An admin unblocked this chat. You can use it again.")
    else:
        email_key = body.key.startswith("email:")
        attempts = blocks.person_attempts(None if email_key else body.key,
                                          body.key[6:] if email_key else (row["label"] if "@" in (row["label"] or "") else None))
        if body.attempt_id and db.get_chat(body.attempt_id):
            _note(body.attempt_id, "✅ An admin re-enabled this contributor's access to the portal.")
    for attempt in attempts:
        hub.publish(attempt, "access", {"kind": body.kind})
    log.warning("%s removed %s block %s", admin["email"], body.kind, body.key)
    return {"ok": True}


@app.get("/api/admin/cost")
async def admin_cost(request: Request, days: int = 14, refresh: int = 0):
    """The Cost page: the key's budget plus spend per day, per submitted task and per contributor."""
    current_admin(request)
    key, report = await asyncio.gather(budget.status(force=bool(refresh)), cost.report(days, force=bool(refresh)))
    return {"budget": key, "cost": report}


@app.get("/api/admin/budget")
async def admin_budget(request: Request, refresh: int = 0):
    """The LiteLLM key's budget: cycle spend vs cap, burn rate and when it runs out at that pace."""
    current_admin(request)
    return await budget.status(force=bool(refresh)) or {"off": True}


@app.get("/api/admin/blocks")
def admin_blocks(request: Request):
    current_admin(request)
    rows = db.q("select * from blocks order by created_at desc")
    out = []
    for r in rows:
        item = blocks.view(r)
        if r["kind"] == blocks.PERSON:
            email_key = r["key"].startswith("email:")
            item["attempts"] = blocks.person_attempts(None if email_key else r["key"],
                                                      r["key"][6:] if email_key else r["label"] if "@" in (r["label"] or "") else None)
        else:
            chat = db.get_chat(r["key"]) or {}
            item["attempts"] = [r["key"]]
            item["task_id"] = chat.get("task_id")
        out.append(item)
    return {"blocks": out}


@app.get("/api/admin/runs/{run_id}/log")
def admin_run_log(run_id: str, request: Request):
    current_admin(request)
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "No such run.")
    path = workspace.attempt_dir(run["attempt_id"]) / "logs" / f"{run_id}.jsonl"
    if not path.is_file():
        raise HTTPException(404, "No log for this run.")
    return FileResponse(path, media_type="application/x-ndjson", filename=f"run-{run_id}.jsonl")


@app.get("/api/admin/reports/{attempt_id}/{name}")
def admin_report(attempt_id: str, name: str, request: Request):
    current_admin(request)
    if name not in workspace.STAGE_BY_FILE:
        raise HTTPException(404, "No such report.")
    text = workspace.read_report(attempt_id, name)
    if text is None:
        raise HTTPException(404, "Not written yet.")
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@app.get("/api/admin/events")
async def admin_events(request: Request):
    current_admin(request)
    return StreamingResponse(event_stream(request, hub.ADMIN, {}), media_type="text/event-stream", headers=SSE_HEADERS)


# ---- admin management (owner only) ------------------------------------------------

# ---- broadcasts: an admin message that pops up for contributors until they acknowledge it ----

@app.get("/api/broadcasts")
def my_broadcasts(request: Request):
    session = current_user(request)
    if session.get("admin"):  # an admin viewing a contributor's chat never sees or acknowledges their pop-ups,
        # but is told which ones are still waiting for that contributor
        waiting = broadcasts.pending(db.get_chat(session["attempt_id"]))
        return {"broadcasts": [], "waiting": [{"id": b["id"], "title": b["title"], "created_at": b["created_at"]} for b in waiting]}
    return for_viewer(session, {"broadcasts": broadcasts.pending(db.get_chat(session["attempt_id"]))})


@app.post("/api/broadcasts/{bid}/ack")
def ack_broadcast(bid: int, request: Request):
    session = current_user(request)
    if session.get("admin"):
        raise HTTPException(403, "Admins can't acknowledge for a contributor.")
    broadcasts.ack(bid, db.get_chat(session["attempt_id"]))
    hub.publish(session["attempt_id"], "broadcast", {"id": bid, "acked": True})  # refreshes an admin's "not yet" note
    return {"ok": True}


class BroadcastBody(BaseModel):
    title: str
    body: str
    audience: str = "everyone"  # "everyone" or "emails"
    emails: list[str] = []
    dry_run: bool = False


@app.get("/api/admin/broadcasts")
def admin_broadcasts(request: Request):
    current_admin(request)
    return {"broadcasts": broadcasts.admin_list()}


@app.post("/api/admin/broadcasts")
def admin_send_broadcast(body: BroadcastBody, request: Request):
    admin = current_admin(request)
    title, text = body.title.strip(), body.body.strip()
    if not title or not text:
        raise HTTPException(400, "Write a title and a message.")
    if len(title) > broadcasts.TITLE_MAX or len(text) > broadcasts.BODY_MAX:
        raise HTTPException(400, f"Keep the title under {broadcasts.TITLE_MAX} and the message under {broadcasts.BODY_MAX} characters.")
    if body.audience not in ("everyone", "emails"):
        raise HTTPException(400, "Pick everyone or specific emails.")
    emails = None
    if body.audience == "emails":
        emails = [e for e in (x.strip() for x in body.emails) if e]
        if not emails:
            raise HTTPException(400, "Add at least one email.")
    active = None
    if emails is None:  # "everyone" = people with a chat that isn't archived (same rule as the admin page)
        active = {a for a, st in _chat_states().items() if not st.get("archived")}
    if body.dry_run:
        people, unmatched = broadcasts.resolve(emails, active=active)
        return {"people": len(people), "unmatched": unmatched}
    try:
        return broadcasts.create(title, text, emails, admin["email"], active)
    except ValueError as err:
        raise HTTPException(400, str(err))


@app.post("/api/admin/broadcasts/{bid}/end")
def admin_end_broadcast(bid: int, request: Request):
    admin = current_admin(request)
    broadcasts.end(bid, admin["email"])
    return {"ok": True}


class LoadTestBody(BaseModel):
    count: int = 50
    seconds: int = 360


@app.post("/api/admin/loadtest")
def loadtest_start(body: LoadTestBody, request: Request):
    """Start `count` stand-in reviews at once on real boxes (canned model replies, no model spend)."""
    current_admin(request, owner=True)
    if not config.LOADTEST_ALLOWED:
        raise HTTPException(403, "Load testing is off (LOADTEST_ALLOWED=0).")
    count, seconds = max(1, min(body.count, 60)), max(30, min(body.seconds, 1800))
    attempts = []
    for _ in range(count):
        attempt_id = "10ad7e57" + uuid.uuid4().hex[:16]
        db.upsert_chat(attempt_id, "loadtest", "", "", "loadtest@example.invalid", str(uuid.uuid4()))
        workspace.ensure_attempt(attempt_id)
        run_id = uuid.uuid4().hex
        (workspace.attempt_dir(attempt_id) / "logs" / f"{run_id}.prompt.txt").write_text(f"{seconds}\n")
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, task_id, actor_email, loadtest)"
              " values (?,?,?,?,?,?,?,?,1)", (run_id, attempt_id, "review", "injection", "queued", time.time(),
                                            "loadtest", "loadtest@example.invalid"))
        RUNNER.submit(run_id)
        attempts.append(attempt_id)
    log.info("load test: %d stand-in reviews of %ds queued", count, seconds)
    return {"attempts": attempts}


@app.post("/api/admin/loadtest/cleanup")
def loadtest_cleanup(request: Request):
    current_admin(request, owner=True)
    ids = [r["attempt_id"] for r in db.q("select attempt_id from chats where task_id='loadtest'")]
    if any(db.active_run(a) for a in ids):
        raise HTTPException(409, "Load-test runs are still active.")
    for table in ("messages", "runs", "proofs", "admin_requests", "logins"):
        db.ex(f"delete from {table} where attempt_id in (select attempt_id from chats where task_id='loadtest')")
    db.ex("delete from llm_calls where run_id not in (select id from runs)")
    db.ex("delete from chats where task_id='loadtest'")
    for a in ids:
        shutil.rmtree(workspace.attempt_dir(a), ignore_errors=True)
    return {"removed": len(ids)}


# ---- eval files: which zips are in use, which changed at the links, and the update job ----------

UPDATE_DIR = config.DATA_DIR / "update"
UPDATE_UNIT = "eval-portal-update.service"


async def _update_job():
    try:
        job = json.loads((UPDATE_DIR / "status.json").read_text())
    except (OSError, ValueError):
        job = {}
    try:
        lines = (UPDATE_DIR / "update.log").read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    try:
        proc = await asyncio.create_subprocess_exec("systemctl", "is-active", UPDATE_UNIT, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        active = (await proc.communicate())[0].decode().strip() in ("active", "activating")
    except OSError:  # no systemd (local development)
        active = False
    if job.get("state") == "running" and not active:
        job["state"], job["error"] = "failed", job.get("error") or "The update job stopped unexpectedly."
    job["active"] = active
    job["step"] = next((ln[5:] for ln in reversed(lines) if ln.startswith("STEP ")), "")
    job["log"] = "\n".join(lines[-60:])
    return job


async def _remote_meta(client, name):
    try:
        r = await client.head(f"{config.EVAL_ZIP_URL}/{name}.zip", follow_redirects=True)
        size = r.headers.get("content-length", "")
        return {"etag": r.headers.get("etag", "").strip('"'), "last_modified": r.headers.get("last-modified"),
                "bytes": int(size) if size.isdigit() else None, "ok": r.status_code == 200}
    except httpx.HTTPError as error:
        return {"ok": False, "error": str(error)[:120]}


@app.get("/api/admin/evalfiles")
async def evalfiles(request: Request):
    me = current_admin(request)
    base = Path(os.path.realpath(config.EVAL_BASE_DIR))
    try:
        installed = json.loads((base / "SOURCE.json").read_text()).get("files", {})
    except (OSError, ValueError):
        installed = {}
    async with httpx.AsyncClient(timeout=15) as client:
        remote = await asyncio.gather(*(_remote_meta(client, n) for n in config.EVAL_FILES))
    files = []
    for name, rem in zip(config.EVAL_FILES, remote):
        have = installed.get(name)
        changed = bool(have and rem.get("etag") and have.get("etag") and rem["etag"] != have["etag"])
        files.append({"name": name, "zip": f"{name}.zip", "universe": "HarmonyGames_" in name,
                      "remote": rem, "installed": have, "changed": changed})
    return {"release": base.name, "snapshot": boatrun.active_snapshot(), "files": files,
            "job": await _update_job(), "can_update": me["role"] == "owner"}


class EvalUpdateBody(BaseModel):
    files: list[str]


@app.post("/api/admin/evalfiles/update")
async def evalfiles_update(body: EvalUpdateBody, request: Request):
    me = current_admin(request, owner=True)
    files = [f for f in config.EVAL_FILES if f in body.files]
    if not files:
        raise HTTPException(400, "Choose at least one file to update.")
    if (await _update_job()).get("active"):
        raise HTTPException(409, "An update is already running.")
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    (UPDATE_DIR / "request.json").write_text(json.dumps({"files": files, "by": me["email"], "at": time.time()}))
    proc = await asyncio.create_subprocess_exec("sudo", "-n", "/usr/bin/systemctl", "start", "--no-block", UPDATE_UNIT,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(500, f"Couldn't start the update: {(err or out).decode(errors='replace')[-300:]}")
    log.info("eval files update started by %s: %s", me["email"], files)
    return {"ok": True, "files": files}


@app.get("/admin/invite/{token}")
def invite_page(token: str):
    return FileResponse(WEB / "admin.html", headers=NO_CACHE)


@app.get("/api/admin/admins")
def list_admins(request: Request):
    current_admin(request, owner=True)
    rows = db.q("select email, role, created_by, created_at, last_login, invite_expires,"
                " password_hash is not null as active from admins order by created_at")
    owners = [{"email": e, "role": "owner", "active": True} for e in config.ADMIN_EMAILS]
    return {"admins": owners + [r for r in rows if r["email"] not in config.ADMIN_EMAILS]}


class AdminInvite(BaseModel):
    email: str


@app.post("/api/admin/admins")
def invite_admin(body: AdminInvite, request: Request):
    """Create an admin (or a fresh set-password link for one). The link is shown once; share it yourself."""
    owner = current_admin(request, owner=True)
    email = body.email.strip().lower()
    if not auth.EMAIL_RE.match(email) or email in config.ADMIN_EMAILS:
        raise HTTPException(400, "Enter a valid email that isn't already an owner.")
    token, token_hash = auth.new_invite()
    expires = time.time() + 72 * 3600
    if db.one("select 1 from admins where email=?", (email,)):
        db.update("admins", "email", email, invite_hash=token_hash, invite_expires=expires)
    else:
        db.ex("insert into admins(email, role, invite_hash, invite_expires, created_by, created_at) values (?,?,?,?,?,?)",
              (email, "admin", token_hash, expires, owner["email"], time.time()))
    return {"ok": True, "invite_url": f"{config.PUBLIC_BASE_URL}/admin/invite/{token}", "expires": expires}


@app.delete("/api/admin/admins/{email}")
def remove_admin(email: str, request: Request):
    current_admin(request, owner=True)
    db.ex("delete from admins where email=?", (email.strip().lower(),))
    return {"ok": True}


class InviteAccept(BaseModel):
    token: str
    password: str


@app.post("/api/admin/accept")
def accept_invite(body: InviteAccept):
    if len(body.password) < 10:
        raise HTTPException(400, "Use at least 10 characters.")
    row = db.one("select * from admins where invite_hash=?", (auth.invite_hash(body.token),))
    if not row or (row["invite_expires"] or 0) < time.time():
        raise HTTPException(403, "This link is invalid or has expired. Ask the owner for a new one.")
    db.update("admins", "email", row["email"], password_hash=auth.hash_password(body.password),
              invite_hash=None, invite_expires=None, last_login=time.time())
    resp = JSONResponse({"ok": True, "email": row["email"]})
    set_cookie(resp, auth.ADMIN_COOKIE, auth.sign({"email": row["email"], "iat": time.time()}), 12 * 3600)
    return resp
