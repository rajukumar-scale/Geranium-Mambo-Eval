"""Review progress: which step a running review has reached, and how far through a run each step
usually starts, so the chat page can show an estimated % and time left.

A review doesn't report its own progress, but every eval runs its checker tools in a fixed order
(save the inputs, the main review, the deep recheck passes, the fix-check passes for /rubric, the
report contract check), so the step a run has reached is a much better guide than the clock alone.
"""

import json
import re
import statistics
import time

from . import db

# Measured on the live runs (2026-10-15): the median length of a finished review and the median
# fraction of the run at which each step starts. Replaced by the recorded step times once enough
# runs have them.
DEFAULTS = {
    "injection": {"minutes": 6.0, "marks": {"inputs": 0.04, "gate": 0.70}},
    "prompt": {"minutes": 25.0, "marks": {"inputs": 0.01, "gate": 0.02, "recheck": 0.48, "final": 0.94}},
    "rubric": {"minutes": 20.0, "marks": {"inputs": 0.01, "recheck": 0.16, "fixcheck": 0.56, "final": 0.89}},
    "stumping": {"minutes": 22.0, "marks": {"inputs": 0.02, "recheck": 0.55, "final": 0.90}},
    "all-fail-validation": {"minutes": 12.0, "marks": {"inputs": 0.03, "recheck": 0.50, "final": 0.88}},
    "reference-notes": {"minutes": 15.0, "marks": {"inputs": 0.02, "recheck": 0.52, "final": 0.90}},
}
MIN_RUNS = 5  # recorded runs needed before they replace the defaults
STEPS = {"inputs", "gate", "recheck", "fixcheck", "final"}  # milestones also hold fixcheck_r2.. (see fixcheck_round)
# The rubric skill checks its suggested fixes in up to 3 rounds, then a restructure (2026-11-01: 10 of 60 /rubric
# reviews used all 4, median 47 min). Rounds 2+ often start after the first report check, so they are shown as such.
FIXCHECK_ROUNDS = 4
_cache = {}  # command -> (expires, calibration)


def step_of(block):
    """The step a top-level tool call starts, or None."""
    if block.get("type") != "tool_use":
        return None
    args = block.get("input") or {}
    if block.get("name") == "Bash":
        command = args.get("command") or ""
        if "hg_report_contract" in command or "hg_write_output" in command:
            return "final"
        if "hg_injection_gate" in command:
            return "gate"
        if "hg_store_input" in command:
            return "inputs"
    elif block.get("name") in ("Agent", "Task"):
        description = (args.get("description") or "").lower()
        if _is_fixcheck(description):  # "fix-check …", "Fixcheck round 2 …", "Fix check …"
            return "fixcheck"
        if "recheck" in description:
            return "recheck"
    return None


def _is_fixcheck(description):
    return re.sub(r"[\s-]", "", description).startswith("fixcheck")


def fixcheck_round(block):
    """Which fix-check round a top-level subagent call starts (1 when it doesn't say), or None."""
    if block.get("type") != "tool_use" or block.get("name") not in ("Agent", "Task"):
        return None
    description = ((block.get("input") or {}).get("description") or "").lower()
    if not _is_fixcheck(description):
        return None
    if "restructure" in description:
        return FIXCHECK_ROUNDS
    found = re.search(r"round\s*(\d+)", description)
    return min(int(found.group(1)), FIXCHECK_ROUNDS) if found else 1


def calibration(command):
    """{minutes, marks} for a review command: from the last finished runs with recorded step times,
    or the measured defaults."""
    if command not in DEFAULTS:
        return None
    hit = _cache.get(command)
    if hit and hit[0] > time.time():
        return hit[1]
    runs = db.q("select started_at, finished_at, milestones from runs where kind='review' and command=? and status='done' "
                "and report_stage is not null and coalesce(continuation,0)=0 and coalesce(loadtest,0)=0 "
                "and milestones is not null and finished_at>started_at order by finished_at desc limit 25", (command,))
    result = DEFAULTS[command]
    if len(runs) >= MIN_RUNS:
        fractions = {}
        for run in runs:
            length = run["finished_at"] - run["started_at"]
            for step, at in json.loads(run["milestones"]).items():
                if step not in STEPS:
                    continue
                fractions.setdefault(step, []).append(min(1.0, max(0.0, (at - run["started_at"]) / length)))
        result = {"minutes": round(statistics.median((r["finished_at"] - r["started_at"]) / 60 for r in runs), 1),
                  "marks": {s: round(statistics.median(v), 3) for s, v in fractions.items() if len(v) * 3 >= len(runs)}}
    _cache[command] = (time.time() + 600, result)
    return result
