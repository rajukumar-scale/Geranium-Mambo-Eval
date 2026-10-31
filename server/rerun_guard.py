"""Identical re-runs (2026-10-31). 39% of review spend was a review started within 30 minutes of the previous one on the
same attempt, and the eval grades the saved inputs: a review on exactly the inputs its last finished review graded gets
the same answer, at $2-23 a run. So a bare command (no text, no attachment) on unchanged inputs isn't started; the
chat page says what the last review found and offers to run it once more anyway (once per identical inputs: a second
opinion, not a loop). Changed inputs, a new eval release, extra text or an attachment, or an admin always run.

"Unchanged" = the saved inputs that stage reads (staleness.depends) plus the eval release, recorded when each review
ends with a report (runs.inputs_after). Reviews from before this existed have no record and never block.
"""

import json
import time

from . import config, db, staleness


def fingerprint(attempt_id, stage):
    snap = staleness.snapshot(attempt_id)
    return {"release": config.EVAL_BASE_DIR.resolve().name,
            "inputs": {k: v for k, v in sorted(snap.items()) if staleness.depends(stage, k)}}


def record(run_id, attempt_id, stage):
    """At the end of a review with a report: what it graded."""
    db.update("runs", "id", run_id, inputs_after=json.dumps(fingerprint(attempt_id, stage), sort_keys=True))


def same_as_last(attempt_id, command):
    """The last finished review of this command, if the inputs it graded are exactly what's saved now; else None."""
    last = db.one("select id, verdict, score, finished_at, cost_estimate, inputs_after, coalesce(rerun_forced, 0) forced "
                  "from runs where attempt_id=? and kind='review' and command=? and status='done' and report_stage is not null "
                  "order by finished_at desc limit 1", (attempt_id, command))
    if not last or not last["inputs_after"]:
        return None
    if json.loads(last["inputs_after"]) != fingerprint(attempt_id, command):
        return None
    return {"command": command, "run_id": last["id"], "verdict": last["verdict"], "score": last["score"],
            "finished_at": last["finished_at"], "minutes_ago": round((time.time() - (last["finished_at"] or 0)) / 60),
            "cost": round(last["cost_estimate"] or 0, 2), "forced": bool(last["forced"])}
