"""A review that hits the time limit is paused, not thrown away (2026-10-30: two /rubric runs lost 90 minutes each one
step before saving their report). Its conversation is already on the server (the box's home comes back with the
state); the box's scratch files under /tmp come back too. The contributor's next plain message continues the same
session on a fresh box, once: a continuation that times out again is a normal failure. If any saved input changed in
between, it can't continue (the review would grade a mix of old and new), and a fresh run is needed.

One paused review per attempt: attempt_dir/paused/state.json plus scratch.tgz.
"""

import json
import shutil
import time

from . import staleness, workspace


def _dir(attempt_id):
    return workspace.attempt_dir(attempt_id) / "paused"


def save(attempt_id, run, scratch=None):
    d = _dir(attempt_id)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    if scratch and scratch.exists():
        shutil.move(str(scratch), d / "scratch.tgz")
    (d / "state.json").write_text(json.dumps({"run_id": run["id"], "command": run["command"], "at": time.time(),
                                              "inputs": staleness.snapshot(attempt_id)}))


def load(attempt_id):
    try:
        return json.loads((_dir(attempt_id) / "state.json").read_text())
    except (OSError, ValueError):
        return None


def scratch(attempt_id):
    path = _dir(attempt_id) / "scratch.tgz"
    return path if path.is_file() else None


def inputs_changed(attempt_id, state):
    return staleness.snapshot(attempt_id) != state.get("inputs")


def clear(attempt_id):
    shutil.rmtree(_dir(attempt_id), ignore_errors=True)


RESUME_NOTE = ("(Portal note: your /{command} review was stopped by the {minutes}-minute time limit before it finished. "
               "Continue exactly where you left off: don't redo steps that already finished; redo a step that was cut "
               "off (for example a recheck pass that hadn't returned) only if its result is missing. Your scratch files "
               "under /tmp were restored. Then finish the review and save its report as instructed.)")
