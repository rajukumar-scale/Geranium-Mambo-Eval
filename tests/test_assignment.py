"""server/assignment.py (2026-11-03, widened 2026-11-04): a chat's assignment and task date come from the platform task
record. A saved one that really differs (another persona, competency, business function, task skill or task date, an
easier mode or a shorter task type) is replaced by the platform's; typos, old names, a harder mode or a longer type are
left alone. Correcting only the business function or task skill keeps the passes."""

import asyncio
import hashlib
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
        normal = {**ROW, "task_type": "normal"}
        self.assertEqual(assignment.disagreements("Task Type: long-horizon\n", normal), [])  # longer than assigned is fine
        self.assertEqual(assignment.disagreements("Task Type: super-long-horizon\nDifficulty: Hard\n", normal), [])
        self.assertEqual(assignment.disagreements("Difficulty: Easy\n", ROW), [("difficulty mode", "easy", "medium")])
        self.assertEqual(assignment.disagreements("Difficulty: Medium\n", {**ROW, "difficulty_mode": "hard"}),
                         [("difficulty mode", "medium", "hard")])  # easier than assigned: corrected
        self.assertEqual(assignment.disagreements("Horizon: Long-Horizon\nDifficulty: Medium\n", ROW), [])
        self.assertEqual(assignment.disagreements("Persona: Frederick\n", ROW), [])  # nothing pasted: nothing to flag


class FieldsTest(unittest.TestCase):
    def test_every_pasted_layout_is_read(self):
        for text in ("🏢 Business Function Executive  🧑 Persona Frederick Stone Role: Co-founder  🎯 Competency Corporate Ops"
                     "  🛠️ Task Skill Actions  📋 Task Type long-horizon",
                     "🏢 Business Function\nExecutive\n\n🧑 Persona\nFrederick Stone\nRole: Co-founder\n\n🎯 Competency\nCorporate Ops\n",
                     "Persona : Frederick Stone, Co-founder Business function : Executive Competency : Corporate Ops Task skill : Actions"):
            f = assignment.pasted_fields(text)
            self.assertEqual((f["persona"][0], f["business function"][0], f["competency"][0]),
                             ("Frederick Stone", "Executive", "Corporate Ops"), text)

    def test_typos_and_old_names_are_the_same_value(self):
        for saved, platform in (("Simon Walkera", "Simon Walker"), ("Retrival", "Retrieval"), ("Enginerring Ops", "Engineering Ops"),
                                ("Founders-Exec-Strategy", "Executive"), ("Claire", "Claire Morgan"), ("Marketing & GTM", "Marketing and GTM")):
            self.assertTrue(assignment.same_value(saved, platform), (saved, platform))
        for saved, platform in (("Corporate Ops", "Executive"), ("Engineering Ops", "Design"), ("Leonard Hayes", "Martin Walsh")):
            self.assertFalse(assignment.same_value(saved, platform), (saved, platform))

    def test_real_differences_are_found_typos_are_not(self):
        self.assertEqual(assignment.disagreements("Persona: Frederik Stone\nBusiness function: Executive\nCompetency: Corporate Ops\n", ROW), [])
        self.assertEqual(assignment.disagreements("Persona: Frederick Stone\nBusiness function: Corporate Ops\nCompetency: Corporate Ops\n", ROW),
                         [("business function", "Corporate Ops", "Executive")])
        self.assertEqual(assignment.disagreements("Persona: Frederick Stone\nCompetency: Finance\n", ROW),
                         [("competency", "Finance", "Corporate Ops")])
        template = "Paste it here. Do not change the Persona or any assigned value.\n```\nPersona\nFrederick Stone\n```"
        self.assertEqual(assignment.disagreements(template, ROW), [])  # the label in the instructions isn't the value


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
                               "files": [{"path": f"Evals/task_inputs/{name}", "sha256": hashlib.sha256(text.encode()).hexdigest(),
                                          "bytes": len(text)}]}
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

    def proof(self, stage="prompt"):
        from server import staleness
        db.ex("insert into proofs(code, run_id, attempt_id, payload, created_at) values (?,?,?,?,?)",
              (f"EVP-{stage}", "r1", "a1", json.dumps({"stage": stage, "verdict": "PASS", "inputs": staleness.snapshot("a1")}), time.time()))

    def outdated(self):
        from server import staleness
        return staleness.latest_outdated("a1")

    def test_a_mistyped_business_function_is_corrected_and_the_passes_still_count(self):
        self._save("task-details", "00_Task_Assigned_Inputs.md",
                   "Persona: Frederick Stone\nBusiness function: Corporate Ops\nCompetency: Corporate Ops\nTask Type: long-horizon\n")
        self.universe = "synthcut"
        self.proof()
        done = self.run_ensure()
        self.assertEqual(done, {"replaced": ["business function"]})
        self.assertIn("Business Function: Executive", (self.inputs / "00_Task_Assigned_Inputs.md").read_text())
        self.assertEqual(self.outdated(), {})
        self.assertIn("still count", self.notes("assignment_replaced")[0]["content"])

    def test_another_competency_is_replaced_and_its_passes_go_outdated(self):
        self._save("task-details", "00_Task_Assigned_Inputs.md", "Persona: Frederick Stone\nCompetency: Finance\n")
        self.universe = "synthcut"
        self.proof()
        self.assertEqual(self.run_ensure(), {"replaced": ["competency"]})
        self.assertIn("prompt", self.outdated())

    def test_a_harder_mode_is_kept_when_another_field_is_corrected(self):
        self._save("task-details", "00_Task_Assigned_Inputs.md", "Competency: Finance\nTask Type: long-horizon\nDifficulty Mode: hard\n")
        self.universe = "synthcut"
        self.run_ensure()
        saved = (self.inputs / "00_Task_Assigned_Inputs.md").read_text()
        self.assertIn("Competency: Corporate Ops", saved)
        self.assertIn("Difficulty Mode: hard", saved)

    def test_chats_from_before_the_switch_keep_what_they_have(self):
        db.update("chats", "attempt_id", "a1", created_at=assignment.STRICT_SINCE - 60)
        self._save("task-details", "00_Task_Assigned_Inputs.md", "Persona: Frederick Stone\nCompetency: Finance\nTask Type: long-horizon\n")
        self.universe = "normal"
        self.assertEqual(self.run_ensure(), {})
        self.assertEqual(self.stored, [])
        self._save("task-details", "00_Task_Assigned_Inputs.md", "Difficulty Mode: easy\n")  # the easier-mode rule still applies
        self.assertEqual(self.run_ensure(), {"replaced": ["difficulty mode"]})

    def test_a_task_date_that_isnt_the_tasks_is_replaced(self):
        self._save("task-details", "00_Task_Assigned_Inputs.md", assignment.render(ROW))
        self.universe = "normal"
        done = self.run_ensure()
        self.assertEqual((done.get("universe_replaced"), self.universe), ("synthcut", "synthcut"))
        self.assertEqual(len(self.notes("universe_replaced")), 1)

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
        self.universe = "synthcut"  # the task's own date (a different one is replaced: see below)
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


class DatePickerTest(unittest.TestCase):
    """The task date comes from the platform's record: the picker can't move it to the other date (2026-11-04)."""

    def test_the_other_date_is_refused(self):
        from fastapi.testclient import TestClient
        from server import app as portal, auth
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.object(config, "ASSIGNMENT_AUTOFILL", 1),
                  mock.patch.object(assignment, "platform_record", mock.AsyncMock(return_value=ROW)),
                  mock.patch.object(workspace, "thaw", lambda a: False)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
              "values ('a1',?,'u1','cb@x.com','s1',?,?)", (TASK, time.time(), time.time()))
        c = TestClient(portal.app)
        c.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "a1", "user_id": "u1", "iat": time.time()}))
        r = c.post("/api/universe", json={"date": "normal"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("comes from the platform", r.json()["detail"])
