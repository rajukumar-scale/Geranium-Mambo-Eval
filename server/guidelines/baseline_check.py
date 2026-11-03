#!/usr/bin/env python3
"""Check a task's platform baseline (six Opus runs + the rubric verifier) the way baseline QC does, no model involved.

The chat assistant runs this when a contributor pastes or uploads their baseline results: it turns whatever they
pasted into the JSON below, runs the script, and explains the result with the Failure patterns guide
(distinct_failure_patterns.md). Read-only; prints a report.

Input (a JSON file, or "-" for stdin):
{
  "task_type": "normal" | "long-horizon" | null,
  "difficulty_mode": "easy" | "medium" | "hard" | null,     (null = medium)
  "criteria": [{"id": "R1", "weight": 5}, ...],             (every rubric row; optional when runs carry "rows")
  "runs": [
    {"name": "Run 1", "tool_calls": 123, "failed": ["R3", "R7"]},
    or {"name": "Run 2", "tool_calls": 98, "rows": [{"id": "R1", "weight": 5, "pass": true}, ...]}
  ]
}
Unknown numbers stay null: the script reports what it can and says what's missing, never a guess.

Checks (thresholds from the portal's guides and baseline QC):
- Hardness: the mean weighted pass rate must be under the mode's limit: Easy 90%, Medium 85%, Hard 70%.
- pass@1: at most 3 of the 6 runs may pass every row.
- Tool calls: the mean per run must be 50-100 for Normal and 100+ for Long-Horizon.
- Failure patterns: a run's pattern is its set of failed rows; two failing runs are one pattern unless they differ
  on at least two rows (grouped transitively). Needs at least two patterns and none covering more than half of the
  failing runs (Failure patterns guide, sections 1-2).
"""

import argparse
import json
import sys

LIMITS = {"easy": 90.0, "medium": 85.0, "hard": 70.0}
PASS_AT_1_MAX = 3


def _norm_mode(mode):
    mode = (mode or "medium").strip().lower()
    return mode if mode in LIMITS else "medium"


def _norm_type(task_type):
    t = (task_type or "").strip().lower()
    if "long" in t:
        return "long-horizon"
    if "normal" in t:
        return "normal"
    return None


def load_runs(data):
    """[(name, tool_calls, failed set, weights dict or None)], plus the rubric weights."""
    weights = {}
    for c in data.get("criteria") or []:
        weights[str(c["id"])] = c.get("weight")
    runs = []
    for i, r in enumerate(data.get("runs") or [], 1):
        name = r.get("name") or f"Run {i}"
        if r.get("rows"):
            failed = {str(x["id"]) for x in r["rows"] if not x.get("pass")}
            for x in r["rows"]:
                weights.setdefault(str(x["id"]), x.get("weight"))
        else:
            failed = {str(x) for x in r.get("failed") or []}
        runs.append({"name": name, "tool_calls": r.get("tool_calls"), "failed": failed})
    return runs, weights


def weighted_rate(failed, weights):
    if not weights or any(w is None for w in weights.values()):
        return None
    total = sum(float(w) for w in weights.values())
    if total <= 0:
        return None
    lost = sum(float(weights.get(c, 0) or 0) for c in failed)
    return 100.0 * (total - lost) / total


def patterns(runs):
    """Group failing runs: same pattern when their failed sets differ by 0 or 1 row, transitively."""
    failing = [r for r in runs if r["failed"]]
    parent = list(range(len(failing)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(failing)):
        for j in range(i + 1, len(failing)):
            if len(failing[i]["failed"] ^ failing[j]["failed"]) <= 1:
                parent[find(i)] = find(j)
    groups = {}
    for i, r in enumerate(failing):
        groups.setdefault(find(i), []).append(r["name"])
    return failing, sorted(groups.values(), key=len, reverse=True)


def check(data):
    mode = _norm_mode(data.get("difficulty_mode"))
    task_type = _norm_type(data.get("task_type"))
    runs, weights = load_runs(data)
    out = {"mode": mode, "task_type": task_type, "runs": len(runs), "checks": {}, "missing": [], "advice": []}
    if not runs:
        out["missing"].append("no runs")
        return out

    # Hardness
    rates = [weighted_rate(r["failed"], weights) for r in runs]
    if all(x is not None for x in rates):
        mean = sum(rates) / len(rates)
        limit = LIMITS[mode]
        out["checks"]["hardness"] = {"ok": mean < limit, "mean_weighted": round(mean, 1), "limit": limit,
                                     "per_run": [round(x, 1) for x in rates]}
    else:
        out["missing"].append("rubric weights for every row (needed for the weighted pass rate)")

    # pass@1
    clean = sum(1 for r in runs if not r["failed"])
    out["checks"]["pass_at_1"] = {"ok": clean <= PASS_AT_1_MAX, "runs_passing_every_row": clean, "max": PASS_AT_1_MAX}

    # Tool calls
    calls = [r["tool_calls"] for r in runs]
    if all(isinstance(c, (int, float)) for c in calls) and task_type:
        mean_calls = sum(calls) / len(calls)
        low = 100 if task_type == "long-horizon" else 50
        high = None if task_type == "long-horizon" else 100
        out["checks"]["tool_calls"] = {"ok": mean_calls >= low and (high is None or mean_calls <= high),
                                       "mean": round(mean_calls, 1), "min_run": min(calls), "range": [low, high]}
    else:
        if not all(isinstance(c, (int, float)) for c in calls):
            out["missing"].append("tool-call count for every run")
        if not task_type:
            out["missing"].append("task type (Normal or Long-Horizon)")

    # Failure patterns
    failing, groups = patterns(runs)
    n_fail = len(failing)
    largest = len(groups[0]) if groups else 0
    allowed = n_fail // 2
    ok = n_fail >= 2 and len(groups) >= 2 and largest <= allowed
    out["checks"]["failure_patterns"] = {"ok": ok, "failing_runs": n_fail, "patterns": len(groups),
                                         "largest_pattern": largest, "largest_allowed": allowed,
                                         "groups": groups}

    # Which fix applies (Failure patterns guide, section 8)
    all_fail = sorted(set.intersection(*[r["failed"] for r in runs])) if runs and all(r["failed"] for r in runs) else []
    if n_fail <= 1:
        out["advice"].append("Only one run (or none) failed: the task is too easy. Add traps (guide section 4).")
    elif len(groups) == 1:
        common = sorted(set.intersection(*[r["failed"] for r in failing]))
        if len(common) >= 5:
            out["advice"].append(f"Every failing run failed the same long run of rows ({len(common)} in common): an early "
                                 "miss is blocking the rest. Make the gateway findable and move the difficulty after it "
                                 "(guide section 6).")
        else:
            out["advice"].append("Every failing run missed the same thing: one trap is carrying the task. Add two or more "
                                 "independent traps on other parts of the task (guide section 4).")
    elif not ok:
        out["advice"].append("Runs differ, but most by only one row: the traps are graded on single rows. Design each trap "
                             "so it produces two independent outcomes (guide section 5), then re-run.")
    if all_fail:
        out["advice"].append("Rows every run failed (check each for a defect first, guide section 7): " + ", ".join(all_fail))
    if not ok or any(not c.get("ok", True) for c in out["checks"].values()):
        out["advice"].append("Change the task, not the rubric: don't delete, merge or split rows to change the counts "
                             "(guide section 8).")
    return out


def render(out):
    lines = [f"Baseline check: {out['runs']} runs, difficulty mode {out['mode']}, task type {out['task_type'] or 'unknown'}"]
    c = out["checks"]
    if "hardness" in c:
        h = c["hardness"]
        lines.append(f"- Hardness: {'OK' if h['ok'] else 'FAIL'}: mean weighted {h['mean_weighted']}% vs under "
                     f"{h['limit']:g}% (runs: {', '.join(f'{x}%' for x in h['per_run'])})")
    p = c["pass_at_1"]
    lines.append(f"- pass@1: {'OK' if p['ok'] else 'FAIL'}: {p['runs_passing_every_row']} run(s) passed every row "
                 f"(at most {p['max']})")
    if "tool_calls" in c:
        t = c["tool_calls"]
        rng = f"{t['range'][0]}+" if t["range"][1] is None else f"{t['range'][0]}-{t['range'][1]}"
        lines.append(f"- Tool calls: {'OK' if t['ok'] else 'FAIL'}: mean {t['mean']} per run (needs {rng}; lowest run "
                     f"{t['min_run']})")
    f = c["failure_patterns"]
    groups = "; ".join("{" + ", ".join(g) + "}" for g in f["groups"]) or "none"
    lines.append(f"- Failure patterns: {'OK' if f['ok'] else 'FAIL'}: {f['failing_runs']} failing run(s), "
                 f"{f['patterns']} pattern(s), largest covers {f['largest_pattern']} (at most {f['largest_allowed']} "
                 f"allowed). Groups: {groups}")
    if out["missing"]:
        lines.append("Missing (not guessed): " + "; ".join(out["missing"]))
    for a in out["advice"]:
        lines.append("Fix: " + a)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", help='JSON file, or "-" for stdin')
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)
    if args.input == "-":
        data = json.load(sys.stdin)
    else:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    out = check(data)
    print(json.dumps(out, indent=2) if args.json else render(out))


if __name__ == "__main__":
    main()
