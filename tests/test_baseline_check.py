"""guidelines/baseline_check.py (2026-11-02): counts a pasted platform baseline the way baseline QC does, and the assistant
is told to use it."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from server import config, runner

sys.path.insert(0, str(config.GUIDELINES_DIR))
import baseline_check as bc  # noqa: E402

ROWS = [f"R{i}" for i in range(1, 21)]


def task(failed_sets, weights=None, calls=None, task_type="long-horizon", mode="medium"):
    weights = weights or {r: 1 for r in ROWS}
    calls = calls or [120] * len(failed_sets)
    return {"task_type": task_type, "difficulty_mode": mode,
            "criteria": [{"id": r, "weight": w} for r, w in weights.items()],
            "runs": [{"name": f"Run {i}", "tool_calls": c, "failed": f}
                     for i, (f, c) in enumerate(zip(failed_sets, calls), 1)]}


class PatternTest(unittest.TestCase):
    def pat(self, failed_sets):
        return bc.check(task(failed_sets))["checks"]["failure_patterns"]

    def test_guide_example_that_fails(self):
        f = self.pat([["R12"], ["R12", "R20"], ["R12"], ["R12"], [], []])
        self.assertEqual((f["failing_runs"], f["patterns"], f["largest_pattern"], f["ok"]), (4, 1, 4, False))

    def test_guide_example_that_passes(self):
        f = self.pat([["R5", "R6"], ["R5", "R6", "R9", "R10"], ["R9", "R10"], ["R5", "R6"], [], []])
        self.assertEqual((f["patterns"], f["largest_pattern"], f["ok"]), (3, 2, True))

    def test_the_admin_case_15_shared_rows_is_one_pattern(self):
        same = [f"R{i}" for i in range(1, 16)]
        f = self.pat([same, same + ["R16"], same, same, same + ["R16"], same])
        self.assertEqual((f["patterns"], f["largest_pattern"], f["ok"]), (1, 6, False))

    def test_grouping_is_transitive(self):
        f = self.pat([["R1"], ["R1", "R2"], ["R1", "R2", "R3"], ["R4", "R5", "R6"], [], []])
        self.assertEqual(f["groups"][0], ["Run 1", "Run 2", "Run 3"])
        self.assertFalse(f["ok"])  # 3 of 4 failing runs in one pattern

    def test_one_failing_run_cannot_pass(self):
        self.assertFalse(self.pat([["R1", "R2"], [], [], [], [], []])["ok"])


class OtherChecksTest(unittest.TestCase):
    def test_weighted_hardness_uses_weights_and_mode(self):
        weights = {r: (5 if r == "R1" else 1) for r in ROWS}  # total 24
        out = bc.check(task([["R1"]] * 6, weights=weights, mode="medium"))
        self.assertAlmostEqual(out["checks"]["hardness"]["mean_weighted"], 79.2, places=1)
        self.assertTrue(out["checks"]["hardness"]["ok"])
        self.assertFalse(bc.check(task([["R2"]] * 6, weights=weights))["checks"]["hardness"]["ok"])  # 95.8%
        self.assertFalse(bc.check(task([["R1"]] * 6, weights=weights, mode="hard"))["checks"]["hardness"]["ok"])

    def test_pass_at_1(self):
        self.assertFalse(bc.check(task([[], [], [], [], ["R1", "R2"], ["R3", "R4"]]))["checks"]["pass_at_1"]["ok"])
        self.assertTrue(bc.check(task([[], [], [], ["R5", "R6"], ["R1", "R2"], ["R3", "R4"]]))["checks"]["pass_at_1"]["ok"])

    def test_tool_calls_by_task_type(self):
        lh = bc.check(task([["R1"]] * 6, calls=[90, 95, 100, 110, 92, 99]))["checks"]["tool_calls"]
        self.assertFalse(lh["ok"])
        normal = bc.check(task([["R1"]] * 6, calls=[60] * 6, task_type="Normal"))["checks"]["tool_calls"]
        self.assertTrue(normal["ok"])
        self.assertFalse(bc.check(task([["R1"]] * 6, calls=[130] * 6, task_type="normal"))["checks"]["tool_calls"]["ok"])

    def test_missing_numbers_are_reported_not_guessed(self):
        data = task([["R1"]] * 6)
        data["criteria"][0]["weight"] = None
        for r in data["runs"]:
            r["tool_calls"] = None
        out = bc.check(data)
        self.assertNotIn("hardness", out["checks"])
        self.assertNotIn("tool_calls", out["checks"])
        self.assertEqual(len(out["missing"]), 2)

    def test_rows_form_and_cli(self):
        data = {"task_type": "normal", "runs": [{"name": f"Run {i}", "tool_calls": 70,
                                                 "rows": [{"id": "A", "weight": 5, "pass": i % 2 == 0},
                                                          {"id": "B", "weight": 1, "pass": i % 3 == 0}]}
                                                for i in range(1, 7)]}
        out = bc.check(data)
        self.assertIn("hardness", out["checks"])
        path = Path(self.id().replace(".", "_") + ".json")
        path.write_text(json.dumps(data))
        self.addCleanup(path.unlink)
        buf = io.StringIO()
        with redirect_stdout(buf):
            bc.main([str(path)])
        self.assertIn("Failure patterns:", buf.getvalue())

    def test_advice_says_change_the_task_not_the_rubric(self):
        same = [f"R{i}" for i in range(1, 16)]
        out = bc.check(task([same] * 6))
        text = " ".join(out["advice"])
        self.assertIn("gateway", text)
        self.assertIn("Change the task, not the rubric", text)


class GuardTest(unittest.TestCase):
    def test_the_assistant_is_told_to_use_it(self):
        self.assertIn("python3 guidelines/baseline_check.py", runner.GUARD)
        self.assertIn("Never guess a missing number", runner.GUARD)


if __name__ == "__main__":
    unittest.main()
