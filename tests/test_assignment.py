"""server/assignment.py (2026-11-03): a chat's assignment and task date come from the platform task record when the
chat has none; a saved assignment that disagrees on difficulty mode or task type gets one note, never an overwrite."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import assignment, config, db, workspace

ROW = {"task_id": "6ad83eddd81b65c3e82bdc1d", "persona_name": "Frederick Stone", "persona_role": "Co-founder",
       "business_function": "Executive", "competency": "Corporate Ops", "task_skill": "Actions",
       "task_type": "long-horizon", "difficulty_mode": "medium", "universe_id": "hg4-synth-sep30cut",
       "universe_version": "16", "universe_date": "October 1, 2025 07:00"}
TASK = "6ad83eddd81b65c3e82bdc1d"


class RenderTest(unittest.TestCase):
    def test_render_has_every_field_the_evals_read(self):
        text = assignment.render(ROW)
        for line in ("Persona: Frederick Stone", "Role: Co-founder", "Business Function: Executive",
                     "Competency: Corporate Ops", "Task Skill: Actions", "Task Type: long-horizon",
                     "Difficulty Mode: medium", "Universe Date: October 1, 2025 07:00"):
            self.assertIn(line, text)

    def test_missing_fields_are_left_out_not_guessed(self):
        text = assignment.render({**ROW, "difficulty_mode": None, "persona_role": ""})
        self.assertNotIn("Difficulty Mode", text)
        self.assertNotIn("Role:", text)

    def test_universe_mapping(self):
        self.assertEqual(assignment.universe_key(ROW), "synthcut")
        self.assertEqual(assignment.universe_key({**ROW, "universe_id": "hg4-2026-08-31"}), "normal")
        self.assertIsNone(assignment.universe_key({**ROW, "universe_id": "hg4-real"}))

    def test_disagreements(self):
        pasted = "📋 Task Type\nnormal\n\n🎚️ Difficulty Mode\nhard\n"
        self.assertEqual(assignment.disagreements(pasted, ROW), [("task type", "normal", "long-horizon")])  # Hard on Medium is fine
        self.assertEqual(assignment.disagreements("Difficulty: Easy\n", ROW), [("difficulty mode", "easy", "medium")])
        self.assertEqual(assignment.disagreements("Difficulty: Medium\n", {**ROW, "difficulty_mode": "hard"}),
                         [("difficulty mode", "medium", "hard")])  # easier than assigned: corrected
        self.assertEqual(assignment.disagreements("Horizon: Long-Horizon\nDifficulty: Medium\n", ROW), [])
        self.assertEqual(assignment.disagreements("Persona: Frederick\n", ROW), [])  # nothing pasted: nothing to flag


class EnsureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.inputs = self.tmp / "attempts" / "a1" / "task_inputs"
        self.inputs.mkdir(parents=True)
        self.stored = []

        async def fake_store(attempt_id, *args):
            self.stored.append(args)
            if args[0] == "save":  # what the eval's tool does: record the input
                self._save("task-details", "00_Task_Assigned_Inputs.md",
                           (self.inputs / "_uploads" / assignment.SOURCE_NAME).read_text())
            elif args[0] == "universe":
                self.universe = args[1]

        self.universe = None
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(config, "RUNNER_MODE", "docker"), mock.patch.object(config, "ASSIGNMENT_AUTOFILL", 1),
                   mock.patch.object(workspace, "attempt_dir", lambda a: self.tmp / "attempts" / a),
                   mock.patch.object(workspace, "thaw", lambda a: False),
                   mock.patch.object(workspace, "dated_release", lambda base=None: True),
                   mock.patch.object(workspace, "universe_date", lambda a: self.universe),
                   mock.patch.object(assignment, "_store", fake_store),
                   mock.patch.object(assignment, "platform_record", mock.AsyncMock(return_value=ROW))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        assignment._locks.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
              "values (?,?,?,?,?,?,?)", ("a1", TASK, "u1", "cb@outlier.ai+outlier", "s1", now, now))

    def _save(self, key, name, text):
        (self.inputs / name).write_text(text)
        index = self.inputs / "inputs.json"
        data = json.loads(index.read_text()) if index.is_file() else {"inputs": {}}
        data["inputs"][key] = {"state": "saved", "revision": 1,
                               "files": [{"path": f"Evals/task_inputs/{name}", "sha256": "x", "bytes": len(text)}]}
        index.write_text(json.dumps(data))

    def run_ensure(self):
        return asyncio.run(assignment.ensure("a1"))

    def notes(self, flag):
        return [m for m in db.messages("a1") if m["meta"].get(flag)]

    def test_fills_assignment_and_date_when_missing(self):
        done = self.run_ensure()
        self.assertEqual(done, {"task_details": True, "universe": "synthcut"})
        self.assertEqual(self.stored[0], ("save", "task-details", "--source", f"Evals/task_inputs/_uploads/{assignment.SOURCE_NAME}"))
        self.assertEqual(self.stored[1], ("universe", "synthcut"))
        self.assertIn("Difficulty Mode: medium", (self.inputs / "00_Task_Assigned_Inputs.md").read_text())
        self.assertEqual(len(self.notes("assignment_autofill")), 1)
        self.assertEqual(self.run_ensure(), {})  # nothing left to do, no second note
        self.assertEqual(len(self.notes("assignment_autofill")), 1)

    def test_a_harder_mode_than_assigned_is_kept_without_a_note(self):
        """Evan, 2026-11-04: Hard on a Medium task grades stricter, so it isn't flagged and its passes stay current."""
        self._save("task-details", "00_Task_Assigned_Inputs.md", "Task Type: long-horizon\nDifficulty Mode: hard\n")
        self.universe = "synthcut"
        self.assertEqual(self.run_ensure(), {})
        self.assertEqual(self.stored, [])
        self.assertIn("Difficulty Mode: hard", (self.inputs / "00_Task_Assigned_Inputs.md").read_text())
        self.assertEqual(self.notes("assignment_replaced"), [])

    def test_platform_wins_over_a_disagreeing_paste(self):
        self._save("task-details", "00_Task_Assigned_Inputs.md", "Task Type: normal\nDifficulty Mode: easy\n")
        self.universe = "synthcut"
        done = self.run_ensure()
        self.assertEqual(done, {"replaced": ["difficulty mode", "task type"]})
        self.assertEqual(self.stored, [("save", "task-details", "--source", f"Evals/task_inputs/_uploads/{assignment.SOURCE_NAME}")])
        saved = (self.inputs / "00_Task_Assigned_Inputs.md").read_text()
        self.assertIn("Difficulty Mode: medium", saved)
        self.assertIn("Task Type: long-horizon", saved)
        self.assertEqual(len(self.notes("assignment_replaced")), 1)
        self.assertEqual(self.run_ensure(), {})  # now it matches: nothing more to do
        self.assertEqual(len(self.stored), 1)

    def test_matching_saved_assignment_is_left_alone(self):
        self._save("task-details", "00_Task_Assigned_Inputs.md", "Horizon: Long-Horizon\nDifficulty: Medium\n")
        self.universe = "normal"  # a date they picked is never changed either
        self.assertEqual(self.run_ensure(), {})
        self.assertEqual(self.stored, [])

    def test_skips_while_a_run_is_active_and_for_load_tests_or_when_off(self):
        with mock.patch.object(db, "active_run", lambda a: {"id": "r1"}):
            self.assertEqual(self.run_ensure(), {"skipped": True})
        db.update("chats", "attempt_id", "a1", task_id="loadtest")
        self.assertEqual(self.run_ensure(), {"skipped": True})
        db.update("chats", "attempt_id", "a1", task_id=TASK)
        with mock.patch.object(config, "ASSIGNMENT_AUTOFILL", 0):
            self.assertEqual(self.run_ensure(), {"skipped": True})
        self.assertEqual(self.stored, [])

    def test_no_platform_record_does_nothing(self):
        with mock.patch.object(assignment, "platform_record", mock.AsyncMock(return_value=None)):
            self.assertEqual(self.run_ensure(), {"skipped": True, "reason": "no platform record"})
        self.assertEqual(self.stored, [])

    def test_ensure_safe_swallows_errors(self):
        with mock.patch.object(assignment, "platform_record", mock.AsyncMock(side_effect=RuntimeError("boom"))):
            self.assertEqual(asyncio.run(assignment.ensure_safe("a1")), {"error": True})


if __name__ == "__main__":
    unittest.main()
