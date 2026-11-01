"""Review progress: fix-check rounds are recognised however the reviewer spells them, and the chat page doesn't say
"finishing up" while /rubric is still in a later fix-check round (a 78-minute review on 2026-11-01 showed ~89% and
"finishing up" for its last 48 minutes)."""

import json
import pathlib
import shutil
import subprocess
import unittest
from unittest import mock

from server import progress

ROOT = pathlib.Path(__file__).resolve().parent.parent


def agent(description):
    return {"type": "tool_use", "name": "Agent", "input": {"description": description}}


class StepTest(unittest.TestCase):
    def test_fixcheck_spellings(self):
        for d in ("fix-check R1-R2", "Fixcheck round 1 R1-R2", "Fix check round 2", "FIXCHECK"):
            with self.subTest(d=d):
                self.assertEqual(progress.step_of(agent(d)), "fixcheck")
        self.assertEqual(progress.step_of(agent("Deep recheck pass R1")), "recheck")

    def test_fixcheck_round(self):
        self.assertEqual(progress.fixcheck_round(agent("Fixcheck round 1 R1-R2")), 1)
        self.assertEqual(progress.fixcheck_round(agent("Fixcheck round 3 R1-R7")), 3)
        self.assertEqual(progress.fixcheck_round(agent("Fixcheck round 4 (restructure) R1-R7")), 4)
        self.assertEqual(progress.fixcheck_round(agent("fix-check restructure")), 4)
        self.assertEqual(progress.fixcheck_round(agent("fix-check R1")), 1)
        self.assertIsNone(progress.fixcheck_round(agent("Deep recheck pass R2")))

    def test_calibration_learns_only_real_steps(self):
        runs = [{"started_at": 0, "finished_at": 100,
                 "milestones": json.dumps({"inputs": 1, "recheck": 20, "fixcheck": 40, "final": 50, "fixcheck_r2": 60})}] * 5
        with mock.patch.object(progress.db, "q", lambda *a: runs), mock.patch.object(progress, "_cache", {}):
            marks = progress.calibration("rubric")["marks"]
        self.assertEqual(set(marks), {"inputs", "recheck", "fixcheck", "final"})


@unittest.skipUnless(shutil.which("node"), "node not installed here")
class ChatEstimateTest(unittest.TestCase):
    """Runs the chat page's own estimate() (web/app.js) in node."""

    def estimate(self, milestones, elapsed_min):
        src = (ROOT / "web" / "app.js").read_text()
        code = src[src.index("const FIXCHECK_ROUNDS"):src.index("function renderRunTitle")]
        t0 = 1_793_560_000  # milestones are given as seconds after the start
        run = {"status": "running", "command": "rubric", "started_at": t0, "milestones": {k: t0 + v for k, v in milestones.items()},
               "progress": {"minutes": 22.1, "marks": progress.DEFAULTS["rubric"]["marks"]}}
        js = (f"{code}\nDate.now = () => {(t0 + elapsed_min * 60) * 1000};\n"
              f"console.log(JSON.stringify(estimate({json.dumps(run)})));")
        return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)

    def test_report_check_alone_is_finishing_up(self):
        est = self.estimate({"inputs": 10, "recheck": 300, "fixcheck": 900, "final": 1500}, 26)
        self.assertEqual((est["label"], est["left"]), ("Finishing the report", "finishing up"))

    def test_a_round_after_the_report_check_is_not_finishing_up(self):
        m = {"inputs": 10, "recheck": 1350, "fixcheck": 1500, "final": 1800, "fixcheck_r2": 2100, "fixcheck_r3": 2700}
        est = self.estimate(m, 50)
        self.assertEqual(est["label"], "Checking the suggested fixes · round 3 of up to 4")
        self.assertEqual(est["left"], "more rounds may follow")
        m["fixcheck_r4"] = 3300
        self.assertEqual(self.estimate(m, 60)["left"], "last round")

    def test_first_round_shows_the_round(self):
        est = self.estimate({"inputs": 10, "recheck": 300, "fixcheck": 900}, 16)
        self.assertEqual(est["label"], "Checking the suggested fixes · round 1 of up to 4")
        self.assertIn("min left", est["left"])


if __name__ == "__main__":
    unittest.main()
