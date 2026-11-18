"""Outdated passes.

A pass covers the inputs its review read, not just the one input its verification code fingerprints. A /prompt
PASS was graded against the injection that was saved at the time; if the contributor later changes the injection,
the prompt fingerprint still matches the taxonomy, but no review has looked at that prompt with the new data
(task 6ad83eddd81b65c3e82bdbe6, 2026-10-24: injection v2 added a correction DM after the /prompt PASS, and the old
pass was submitted).

So each new pass stores a snapshot of every saved input (proofs.create, field "inputs"), and here we compare the
inputs that pass's stage depends on with what is saved now. Passes issued before this existed have no snapshot
and are never called outdated (unknown, not stale).

Which inputs each stage reads mirrors the eval's own review-context filters:
  prompt   Evals/tools/hg_prompt_checks.py  INPUTS (+ reference:*)
  oe       Evals/tools/hg_oe_review.py      INPUT_KEYS (+ reference:*)
  rubric   Evals/tools/hg_rubric_loop.py    INPUT_KEYS (+ reference:*, source:*, extra:*; not the baseline runs)
  injection and hardness have no context helper; from their skills: the SQL (+ the date), and the prompt, SQL and
  reference files (the run file is left out, so saving a second run doesn't flag a hardness pass).
If the eval changes those lists, change these too.
"""

import hashlib
import json

from . import config, db, workspace

INJECTION_KEYS = {"injection"}
PROMPT_KEYS = {"task-details", "prompt", "injection"}
RUBRIC_KEYS = {"task-details", "prompt", "injection", "rubric"}
STUMPING_KEYS = {"task-details", "prompt", "injection", "rubric", "runs-base", "grading-base", "stumping-check"}
ALL_FAIL_KEYS = {"task-details", "prompt", "injection", "rubric", "runs-base", "grading-base", "runs-solv", "grading-solv",
                 "all-fail", "all-fail-justify"}
NOTES_KEYS = {"task-details", "prompt", "injection", "rubric", "grading-base", "all-fail", "all-fail-justify",
              "reference-notes"}
NOT_RUBRIC = {"runs-base", "grading-base", "stumping-check", "runs-solv", "grading-solv", "all-fail",
              "all-fail-justify", "reference-notes"}

# How the inputs are named to contributors (the chat page's INPUT_LABELS say the same).
LABELS = {
    "task-details": "task setup",
    "injection": "injection SQL",
    "prompt": "prompt",
    "rubric": "rubric",
    "runs-base": "base runs",
    "grading-base": "grading result",
    "stumping-check": "stumping check",
    "runs-solv": "solvability re-runs",
    "grading-solv": "solvability grading",
    "all-fail": "all-fail rubrics",
    "all-fail-justify": "all-fail justifications",
    "reference-notes": "reference notes",
}
ORDER = config.REVIEW_COMMANDS  # the order to send the commands again

_EMPTY_SQL_SHA = hashlib.sha256(b"").hexdigest()
_NO_INJECTION_DIGESTS = {
    hashlib.sha256(json.dumps(["cleared", []]).encode()).hexdigest()[:16],
    hashlib.sha256(json.dumps(["saved", []]).encode()).hexdigest()[:16],
    hashlib.sha256(json.dumps(["saved", [_EMPTY_SQL_SHA]]).encode()).hexdigest()[:16],
}


def depends(stage, key):
    """Does a review of this stage read the saved input with this key?"""
    bare = key.removeprefix("source:")
    if stage == "injection":
        return bare in INJECTION_KEYS
    if stage == "prompt":
        return bare in PROMPT_KEYS or key.startswith("reference:")
    if stage == "rubric":
        return bare not in NOT_RUBRIC and (bare in RUBRIC_KEYS or key.startswith(("reference:", "source:", "extra:")))
    if stage == "stumping":
        return bare in STUMPING_KEYS or key.startswith("reference:")
    if stage == "all-fail-validation":
        return bare in ALL_FAIL_KEYS or key.startswith("reference:")
    if stage == "reference-notes":
        return bare in NOTES_KEYS or key.startswith("reference:")
    return False


def snapshot(attempt_id):
    """{input key: digest of its saved state and file hashes}, for every saved input of the attempt."""
    out = {}
    for item in workspace.saved_inputs(attempt_id):
        body = [item.get("state"), sorted(f.get("sha256") or "" for f in item.get("files") or [])]
        out[item["key"]] = hashlib.sha256(json.dumps(body).encode()).hexdigest()[:16]
    return out


def restamp(attempt_id, key, old, new):
    """Passes that were current on input `key` (digest `old`) stay current on `new`: for a change the portal makes that
    doesn't change what the review graded (the platform's business function or task skill over a mistyped one)."""
    if not old or not new or old == new:
        return 0
    n = 0
    for p in db.q("select code, payload from proofs where attempt_id=?", (attempt_id,)):
        payload = json.loads(p["payload"] or "{}")
        if (payload.get("inputs") or {}).get(key) == old:
            payload["inputs"][key] = new
            db.ex("update proofs set payload=? where code=?", (json.dumps(payload), p["code"]))
            n += 1
    return n


def label(key):
    if key.startswith("reference:"):
        return "reference file " + key.split(":", 1)[1]
    return LABELS.get(key.removeprefix("source:"), key)


def check(payload, now=None):
    """None when the pass still covers the saved inputs (or it predates snapshots); otherwise
    {"changed": [keys], "labels": [...], "message": "..."}. now = a snapshot() to reuse across many passes."""
    then = payload.get("inputs")
    stage = payload.get("stage")
    if not isinstance(then, dict) or not stage:
        return None
    if now is None:
        now = snapshot(payload["attempt_id"])
    # Optional /injection: if SQL was cleared or emptied after an earlier /injection pass, /injection is no longer
    # required and must not lock downstream cards.
    if stage == "injection" and (now.get("injection") is None or now.get("injection") in _NO_INJECTION_DIGESTS):
        return None
    keys = {k for k in set(then) | set(now) if depends(stage, k)}
    changed = sorted(k for k in keys if then.get(k) != now.get(k))
    if not changed:
        return None
    names = [label(k) for k in changed]
    said = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return {"changed": changed, "labels": names,
            "message": f"Your {said} changed after this review, so this pass no longer covers your current inputs. "
                       f"Send /{stage} again."}


# ---- Locked passes (2026-11-05) -----------------------------------------------------------------------------
# Task 6adfbc55...6c6f (attempt 6aeb9f8e): the CB passed /oe and /rubric on Nov 3, rewrote the injection SQL ~28
# times on Nov 4, re-ran only /injection, /prompt and /hardness, and submitted the Nov 3 rubric and OE screenshots.
# The chat had said "Outdated passes" five times. QC failed the rubric on exactly what the new SQL changed.
# Rule (Evan): cards unlock in order (injection -> prompt -> hardness -> oe -> rubric). A stage's pass shows its
# code and image only when it and every earlier stage are current; an outdated LATER stage never locks an earlier
# card (each card is needed to move on in the task: the prompt card before the Opus run for /hardness, ...).
# A card issued while a later stage is outdated carries a warning to send those again (warn_later), and
# submit_watch.py DMs Evan when a task is submitted while any pass is still outdated.

def _case_b_requires_all_fail(attempt_id):
    """True when the latest stumping report is Case B (requiring /all-fail-validation before /reference-notes)."""
    try:
        for r in workspace.list_reports(attempt_id):
            if r.get("stage") == "stumping":
                return (r.get("fields") or {}).get("case") == "B"
    except Exception:
        pass
    return False


def gate(attempt_id, now=None):
    """{"todo": [outdated stages, in order], "stages": [{stage, status, verdict, why, changed, locked}]}.
    status: "current", "outdated" (its newest pass no longer covers the saved inputs) or "none" (no pass yet;
    a stage not passed yet never locks anything). locked: that stage's card is held (it, or an earlier stage, is
    outdated)."""
    rows = db.q("select payload from proofs where attempt_id=? order by created_at", (attempt_id,))
    newest = {}
    for row in rows:
        p = json.loads(row["payload"])
        newest[p.get("stage")] = p
    now = snapshot(attempt_id) if now is None else now
    stages, todo, earlier = [], [], []
    for stage in list(ORDER) + sorted(s for s in newest if s not in ORDER):
        p = newest.get(stage)
        if stage == "injection" and (now.get("injection") is None or now.get("injection") in _NO_INJECTION_DIGESTS):
            p = None
        result = check(p, now) if p else None
        missing_case_b = (stage == "all-fail-validation" and not p and _case_b_requires_all_fail(attempt_id))
        status = ("outdated" if missing_case_b else "none") if not p else "outdated" if result else "current"
        why_msg = ("Case B requires /all-fail-validation (3 Opus solvability re-runs & target justifications) before submission." if missing_case_b else (result["message"] if result else None))
        stages.append({"stage": stage, "status": status, "verdict": (p or {}).get("verdict"),
                       "why": why_msg, "changed": result["labels"] if result else (["Case B solvability"] if missing_case_b else []),
                       "locked": bool(p) and (bool(result) or bool(earlier))})
        if result or missing_case_b:
            todo.append(stage)
            earlier.append(stage)
    return {"todo": todo, "stages": stages, "locked": any(s["locked"] for s in stages)}


def _rank(stage):
    return ORDER.index(stage) if stage in ORDER else len(ORDER)


def lock_reason(payload, the_gate):
    """Why this pass's card is held (None when it can be used): it is outdated itself, or an earlier stage is."""
    stage = payload.get("stage")
    before = [s for s in the_gate["todo"] if _rank(s) < _rank(stage)]
    if payload.get("outdated"):
        return {"self": True, "todo": before + [stage]}
    if before:
        return {"self": False, "todo": before}
    return None


def later_outdated(payload, the_gate):
    """Later stages that are outdated while this card is usable: the contributor must send them again."""
    stage = payload.get("stage")
    return [s for s in the_gate["todo"] if _rank(s) > _rank(stage)]


SECRET_FIELDS = ("code", "fingerprint", "report_sha256", "signature")


def redact(value):
    """Contributor's view: a locked pass keeps its verdict and stage but loses everything that could be uploaded
    or typed into the task (verification code, EVP code, report hashes). "ref" stays so the page can match cards."""
    if isinstance(value, dict):
        if value.get("locked") and "stage" in value and "code" in value:
            out = {k: v for k, v in value.items() if k not in SECRET_FIELDS}
            out.update(ref=f"{value.get('run_id')}:{value.get('stage')}", code=None, fingerprint=None,
                       report_hashes={}, report_fields={})
            return out
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def annotate(attempt_id, payloads):
    """Add "outdated" (check()'s result or None), "locked" (lock_reason), "warn_later" (later stages to send
    again) and "ref" to each pass payload, with one snapshot for the lot."""
    now = snapshot(attempt_id)
    the_gate = gate(attempt_id, now) if payloads else None
    for p in payloads:
        p["outdated"] = check(p, now)
        p["locked"] = lock_reason(p, the_gate)
        p["warn_later"] = [] if p["locked"] else later_outdated(p, the_gate)
        p["ref"] = f"{p.get('run_id')}:{p.get('stage')}"
    return payloads


def view_event(attempt_id, payload):
    """A live event for a contributor: a new pass (or its chat message) gets the same lock as the chat history."""
    event, data = payload.get("event"), payload.get("data")
    if event == "proof" and isinstance(data, dict):
        return {**payload, "data": redact(annotate(attempt_id, [dict(data)])[0])}
    if event == "message" and isinstance(data, dict) and data.get("role") == "proof" and isinstance(data.get("meta"), dict):
        return {**payload, "data": {**data, "meta": redact(annotate(attempt_id, [dict(data["meta"])])[0])}}
    return payload


def annotate_messages(attempt_id, messages):
    proofs = [m["meta"] for m in messages if m.get("role") == "proof" and isinstance(m.get("meta"), dict)]
    if proofs:
        annotate(attempt_id, proofs)
    return messages


def latest_outdated(attempt_id):
    """{stage: check result} for stages whose newest pass is outdated (an older outdated pass that a newer, current
    pass replaced doesn't count)."""
    rows = db.q("select payload from proofs where attempt_id=? order by created_at", (attempt_id,))
    newest = {}
    for row in rows:
        p = json.loads(row["payload"])
        newest[p.get("stage")] = p
    now = snapshot(attempt_id)
    out = {}
    for stage, p in newest.items():
        result = check(p, now)
        if result:
            out[stage] = result | {"code": p.get("code")}
    return out


def rerun_order(stages):
    return [s for s in ORDER if s in stages] + sorted(s for s in stages if s not in ORDER)


def notice(attempt_id):
    """A chat message (or None) for passes that became outdated since the last notice, so the contributor hears about
    it at once instead of finding out at L10. Remembers what it said in the proofs table (stale_notified)."""
    current = latest_outdated(attempt_id)
    fresh = {}
    for stage, result in current.items():
        mark = ",".join(result["changed"])
        row = db.one("select stale_notified from proofs where code=?", (result["code"],))
        if row and (row.get("stale_notified") or "") != mark:
            fresh[stage] = result
            db.ex("update proofs set stale_notified=? where code=?", (mark, result["code"]))
    if not fresh:
        return None
    order = rerun_order(current)  # every outdated stage, so the list of commands is complete
    lines = [f"- /{s}: your {', '.join(current[s]['labels'])} changed after it passed" for s in order]
    return ("⚠ **Outdated passes.** You changed your inputs after these passes, so they were graded on an older "
            "version and no longer count:\n" + "\n".join(lines) +
            "\n\nSend these again, in this order: " + ", ".join(f"`/{s}`" for s in order) + ". Each pass card "
            "unlocks as you go: a card after an outdated stage stays 🔒 locked until that stage passes again. "
            "**Re-run every one of them before you submit.** Submitting a task with an outdated pass (an older "
            "prompt or rubric screenshot, for example) gets it sent back and penalized, and the team is notified.")


def prompt_note(attempt_id):
    """One line for a follow-up's context: which passes are outdated right now."""
    current = latest_outdated(attempt_id)
    if not current:
        return ""
    parts = [f"/{s} (their {', '.join(current[s]['labels'])} changed since that review)" for s in rerun_order(current)]
    return ("(Portal: these passes are outdated right now; their cards, and every later stage's card, stay locked "
            "until they are run again in order, and submitting with an outdated pass gets the task sent back: "
            + "; ".join(parts) + ".)\n\n")
