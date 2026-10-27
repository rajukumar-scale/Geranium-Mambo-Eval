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

from . import db, workspace

PROMPT_KEYS = {"task-details", "universe", "prompt", "injection", "error-async", "dynamic-context"}
OE_KEYS = {"task-details", "universe", "prompt", "oe", "injection", "error-async"}
RUBRIC_KEYS = {"task-details", "universe", "prompt", "oe", "golden", "injection", "error-async", "dynamic-context",
               "rubric"}
INJECTION_KEYS = {"injection", "universe"}
HARDNESS_KEYS = {"universe", "prompt", "injection"}
NOT_RUBRIC = {"baseline", "baseline-verifier", "all-fail"}

# How the inputs are named to contributors (the chat page's INPUT_LABELS say the same).
LABELS = {"task-details": "task details", "universe": "task date", "prompt": "prompt", "injection": "injection SQL",
          "error-async": "error/async config", "dynamic-context": "dynamic context", "oe": "Oracle Events",
          "golden": "golden answer", "rubric": "rubric"}
ORDER = ("injection", "prompt", "hardness", "oe", "rubric")  # the order to send the commands again


def depends(stage, key):
    """Does a review of this stage read the saved input with this key?"""
    bare = key.removeprefix("source:")
    if stage == "prompt":
        return bare in PROMPT_KEYS or key.startswith("reference:")
    if stage == "oe":
        return bare in OE_KEYS or key.startswith("reference:")
    if stage == "rubric":
        return bare not in NOT_RUBRIC and (key in RUBRIC_KEYS or key.startswith(("reference:", "source:", "extra:")))
    if stage == "injection":
        return key in INJECTION_KEYS
    if stage == "hardness":
        return key in HARDNESS_KEYS or key.startswith("reference:")
    return False


def snapshot(attempt_id):
    """{input key: digest of its saved state and file hashes}, for every saved input of the attempt."""
    out = {}
    for item in workspace.saved_inputs(attempt_id):
        body = [item.get("state"), sorted(f.get("sha256") or "" for f in item.get("files") or [])]
        out[item["key"]] = hashlib.sha256(json.dumps(body).encode()).hexdigest()[:16]
    return out


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
    keys = {k for k in set(then) | set(now) if depends(stage, k)}
    changed = sorted(k for k in keys if then.get(k) != now.get(k))
    if not changed:
        return None
    names = [label(k) for k in changed]
    said = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return {"changed": changed, "labels": names,
            "message": f"Your {said} changed after this review, so this pass no longer covers your current inputs. "
                       f"Send /{stage} again."}


def annotate(attempt_id, payloads):
    """Add "outdated" (check()'s result or None) to each pass payload, with one snapshot for the lot."""
    now = snapshot(attempt_id)
    for p in payloads:
        p["outdated"] = check(p, now)
    return payloads


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
    return ("⚠ **Outdated passes.** These passes were graded on an older version of your inputs, so they no longer "
            "count for what you have saved now:\n" + "\n".join(lines) +
            "\n\nSend these again, in this order: " + ", ".join(f"`/{s}`" for s in order) +
            ". Use the new pass screens for your task, not the old ones.")


def prompt_note(attempt_id):
    """One line for a follow-up's context: which passes are outdated right now."""
    current = latest_outdated(attempt_id)
    if not current:
        return ""
    parts = [f"/{s} (their {', '.join(current[s]['labels'])} changed since that review)" for s in rerun_order(current)]
    return ("(Portal: these passes are outdated right now and must be run again before they submit: "
            + "; ".join(parts) + ".)\n\n")
