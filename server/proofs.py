"""Verification codes for passing reports.

A screenshot alone is easy to fake, so every passing popup carries a code
that is derived from the run with a server secret and stored with the full
record. Anyone (or an LLM validator) can look the code up at /verify/<code>
and compare the stored record with the screenshot.
"""

import base64
import hashlib
import hmac
import json
import time

from . import config, db


def make_code(run_id, stage=None):
    """One code per (run, stage): a run can pass more than one stage (a skill's own gate)."""
    key = run_id if stage is None else f"{run_id}:{stage}"
    mac = hmac.new(config.PROOF_SECRET.encode(), key.encode(), hashlib.sha256).digest()
    raw = base64.b32encode(mac).decode().rstrip("=")[:12]
    return f"EVP-{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def mask_email(email):
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    domain = domain.split("+", 1)[0]
    return (local[:2] + "***" + local[-1:] if len(local) > 3 else local[:1] + "***") + "@" + domain


def create(run, chat, report, override=None, inputs=None):
    """override: {by, reason} for a pass an admin issues over a review that didn't pass. The record keeps the
    review's own verdict and score under admin_override, and every pass screen and /verify shows it.
    inputs: staleness.snapshot() of the saved inputs when the review finished, so the pass can later be shown as
    outdated if an input it read changes (staleness.check)."""
    code = make_code(run["id"], report["stage"] if report["stage"] != run.get("command") else None)
    models = run["models"] or {}
    totals = {k: sum(m.get(k, 0) for m in models.values()) for k in ("calls", "input", "output", "cache_read", "cache_creation")}
    payload = {
        "code": code,
        # An override over a PARTIAL NON-FAIL stays NON-FAIL; over a failing review it's a PASS (an OE NON-FAIL
        # counts as failing, config.passing, so its override is a PASS the taxonomy accepts).
        "verdict": report["verdict"] if not override or config.passing(report["stage"], report["verdict"]) else "PASS",
        "score": None if override else report["score"],
        "stage": report["stage"],
        # Set when the stage was checked inside another command's run; tokens and cost are that whole run's.
        "checked_during": run.get("command") if report["stage"] != run.get("command") else None,
        "execution_status": report["exec_status"],
        "attempt_id": run["attempt_id"],
        "task_id": chat["task_id"],
        "user_id": chat["user_id"],
        "user_email": mask_email(chat["email"]),
        "run_id": run["id"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "duration_seconds": round((run["finished_at"] or 0) - (run["started_at"] or 0)),
        "models": models,
        "totals": totals,
        "cost_usd_estimate": round(run.get("cost_estimate") or 0, 4),
        "report_file": report["file"],
        # The verification code shown on the pass: the graded input's fingerprint, same format as the taxonomy's.
        "fingerprint": report.get("fingerprint"),
        "report_sha256": report["sha256"],
        "report_hashes": report.get("hashes", {}),
        "report_fields": report.get("fields", {}),
        "issued_at": time.time(),
    }
    if inputs is not None:
        payload["inputs"] = inputs
    if report["stage"] in ("rubric", "stumping", "all-fail-validation", "reference-notes", "oe", "hardness"):
        # The prompt this review was graded with, shown on its pass screen (2026-11-11: two tasks submitted an older
        # prompt than the one their /oe and /rubric passed with; each field's own pass still matched its own field).
        from . import fingerprint  # here: fingerprint pulls in workspace lazily
        try:
            payload["prompt_fingerprint"] = fingerprint.for_attempt(run["attempt_id"], "prompt")
        except Exception:  # noqa: BLE001 - a pass is never refused over this note
            payload["prompt_fingerprint"] = None
    if override:
        payload["admin_override"] = {"by": override["by"], "reason": override["reason"], "at": time.time(),
                                     "original_verdict": report.get("verdict"), "original_score": report.get("score")}
    payload["signature"] = hmac.new(config.PROOF_SECRET.encode(),
                                    json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    db.ex("insert or replace into proofs(code, run_id, attempt_id, payload, created_at) values (?,?,?,?,?)",
          (code, run["id"], run["attempt_id"], json.dumps(payload), time.time()))
    if report["stage"] == run.get("command"):
        db.update("runs", "id", run["id"], proof_code=code)
    return payload


def lookup(code):
    row = db.one("select payload from proofs where code=?", ((code or "").strip().upper(),))
    if not row:
        return None
    payload = json.loads(row["payload"])
    body = {k: v for k, v in payload.items() if k != "signature"}
    good = hmac.new(config.PROOF_SECRET.encode(), json.dumps(body, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    payload["valid"] = hmac.compare_digest(good, payload.get("signature", ""))
    return payload
