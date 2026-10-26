"""Signed session cookies and the Redash attempt check used at login."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

import httpx

from . import config

ID_RE = re.compile(r"^[0-9a-f]{24}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USER_COOKIE = "evp_user"
ADMIN_COOKIE = "evp_admin"

_gate_cache = {}  # attempt_id -> (expires, rows)


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload):
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    mac = _b64(hmac.new(config.SESSION_SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def unsign(token, max_age):
    try:
        body, mac = token.split(".", 1)
        good = _b64(hmac.new(config.SESSION_SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, AttributeError):
        return None
    if time.time() - payload.get("iat", 0) > max_age:
        return None
    return payload


def normalize_email(email):
    """Users are stored as name@outlier.ai+outlier; people type name@outlier.ai."""
    email = email.strip().lower()
    return re.sub(r"(@[^+]+)\+.*$", r"\1", email)


class GateError(Exception):
    pass


async def redash_rows(query_id, parameters=None, max_age=0):
    """Run a saved Redash query and return its rows (max_age: reuse a result up to that many seconds old)."""
    # Redash's WAF rejects Python's default user agent with a 403.
    headers = {"Authorization": f"Key {config.REDASH_API_KEY}", "User-Agent": "eval-portal/1.0"}
    async with httpx.AsyncClient(base_url=config.REDASH_URL, headers=headers, timeout=60) as client:
        resp = await client.post(f"/api/queries/{query_id}/results",
                                 json={"parameters": parameters or {}, "max_age": max_age})
        resp.raise_for_status()
        data = resp.json()
        deadline = time.time() + 90
        while "job" in data:
            job = data["job"]
            if job["status"] == 3:
                data = (await client.get(f"/api/query_results/{job['query_result_id']}")).json()
                break
            if job["status"] in (4, 5):
                raise GateError(f"Redash job failed: {job.get('error')}")
            if time.time() > deadline:
                raise GateError("Redash lookup timed out")
            await asyncio.sleep(1)
            data = (await client.get(f"/api/jobs/{job['id']}")).json()
    return data["query_result"]["data"]["rows"]


async def redash_attempt(attempt_id):
    cached = _gate_cache.get(attempt_id)
    if cached and cached[0] > time.time():
        return cached[1]
    rows = await redash_rows(config.REDASH_LOGIN_QUERY_ID, {"attempt_id": attempt_id})
    # A miss is remembered only briefly: the warehouse catches up, and people retry after fixing a typo.
    _gate_cache[attempt_id] = (time.time() + (600 if rows else 60), rows)
    return rows


async def check_login(identifier, attempt_id):
    """Return (ok, reason, row). identifier = email or Outlier user id."""
    identifier = (identifier or "").strip()
    attempt_id = (attempt_id or "").strip().lower()
    if not ID_RE.match(attempt_id):
        return False, "The attempt ID should be 24 characters (0-9, a-f).", None
    is_email = "@" in identifier
    if is_email and not EMAIL_RE.match(identifier):
        return False, "That email doesn't look right.", None
    if not is_email and not ID_RE.match(identifier.lower()):
        return False, "Enter your email or your 24-character Outlier ID.", None
    rows = await redash_attempt(attempt_id)
    # kind: "attempt" (submitted), "claim" (a task they are still working on) or "task_id" (no owner).
    owned = [r for r in rows if (r.get("KIND") or "attempt") in ("attempt", "claim")]
    if not owned:
        if rows:
            return False, ("That's a task ID. Enter your attempt ID instead (the ID of your own attempt "
                           "or claim on this task)."), None
        return False, ("We couldn't find that attempt. Check the ID; if you only just started the task, "
                       "it can take a little while to appear."), None
    if config.ALLOWED_PROJECT_IDS and not any(r.get("PROJECT_ID") in config.ALLOWED_PROJECT_IDS for r in owned):
        return False, "That attempt isn't part of a project this eval covers.", None
    typed = identifier.lower()
    for row in owned:
        if config.ALLOWED_PROJECT_IDS and row.get("PROJECT_ID") not in config.ALLOWED_PROJECT_IDS:
            continue
        if is_email:
            match = typed in ((row.get("EMAIL_RAW") or ""), (row.get("EMAIL_NORM") or "")) or \
                normalize_email(typed) == (row.get("EMAIL_NORM") or "")
        else:
            match = typed == (row.get("USER_ID") or "").lower()
        if match:
            return True, "ok", row
    return False, "That attempt doesn't belong to this account.", None


# ---- admin passwords (owners in ADMIN_EMAILS use ADMIN_PASSWORD; added admins set their own) ----

def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 300_000)
    return f"pbkdf2${300_000}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        _, iterations, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
        return hmac.compare_digest(_b64(check), digest)
    except (ValueError, AttributeError):
        return False


def new_invite():
    token = secrets.token_urlsafe(24)
    return token, hashlib.sha256(token.encode()).hexdigest()


def invite_hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()
