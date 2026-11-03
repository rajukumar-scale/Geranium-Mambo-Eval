"""Tool-call note after /hardness (server/toolcall_note.py, 2026-11-02): counts the uploaded Opus run's tool calls and
warns when they're below or close to the task type's minimum. Advice only, never for admins or with the flag off."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, toolcall_note as tn, workspace


def export(n_calls, wrap="list"):
    events = [{"type": "system", "subtype": "init"}]
    for i in range(n_calls):
        events.append({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"},
                                                                    {"type": "tool_use", "id": f"t{i}", "name": "x"}]}})
        events.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}"}]}})
    events.append({"type": "result", "num_turns": n_calls + 1})
    if wrap == "jsonl":
        return "\n".join(json.dumps(e) for e in events)
    return json.dumps(events)


ASSIGN = "📋 Task Type\n{t}\n\n🎚️ Difficulty Mode\nmedium\n"


class CountTest(unittest.TestCase):
    def test_counts_tool_use_blocks(self):
        self.assertEqual(tn.count_tool_calls(export(109)), 109)
        self.assertEqual(tn.count_tool_calls(export(42, "jsonl")), 42)
        msgs = {"messages": [{"role": "assistant", "content": [{"type": "tool_use"}, {"type": "tool_use"}]}]}
        self.assertEqual(tn.count_tool_calls(json.dumps(msgs)), 2)

    def test_unknown_formats_count_nothing(self):
        for text in ("not json at all {", json.dumps({"foo": 1}), json.dumps([1, 2, 3]), ""):
            self.assertIsNone(tn.count_tool_calls(text), text)

    def test_task_type(self):
        self.assertEqual(tn.task_type(ASSIGN.format(t="long-horizon")), "long-horizon")
        self.assertEqual(tn.task_type("Task Type: Normal"), "normal")
        self.assertEqual(tn.task_type("Task type - Long Horizon"), "long-horizon")
        self.assertEqual(tn.task_type("Persona: Julia Lawson\nHorizon: Long-Horizon\n"), "long-horizon")
        self.assertEqual(tn.task_type("Task skill: Actions\nHorizon: Long\n"), "long-horizon")
        self.assertEqual(tn.task_type("Horizon: Normal"), "normal")
        self.assertEqual(tn.task_type("📋 Horizon Type\nlong-horizon\n"), "long-horizon")
        self.assertIsNone(tn.task_type("Persona: Frederick"))

    def test_note_bands(self):
        self.assertIn("would likely fail", tn.note_text(61, "long-horizon"))
        self.assertIn("close to the Long-Horizon minimum of 100", tn.note_text(109, "long-horizon"))
        self.assertIsNone(tn.note_text(140, "long-horizon"))
        self.assertIn("would likely fail", tn.note_text(40, "normal"))
        self.assertIn("close to the Normal minimum of 50", tn.note_text(55, "normal"))
        self.assertIsNone(tn.note_text(80, "normal"))
        self.assertIn("above the Normal range", tn.note_text(130, "normal"))
        self.assertIn("(/?guide=toolcalls)", tn.note_text(10, "normal"))


class AfterReviewTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for p in (mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                  mock.patch.object(workspace, "attempt_dir", lambda a: self.tmp / "attempts" / a),
                  mock.patch.object(config, "TOOLCALL_NOTE_PILOT", ["*"])):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
              "values (?,?,?,?,?,?,?)", ("a1", "t1", "u1", "cb@outlier.ai+outlier", "s1", now, now))
        self.inputs = self.tmp / "attempts" / "a1" / "task_inputs"
        (self.inputs / "opus").mkdir(parents=True)
        self.n = 0

    def setup(self, calls, task_type="long-horizon"):
        (self.inputs / "opus" / "trajectory.json").write_text(export(calls))
        (self.inputs / "00_Task_Assigned_Inputs.md").write_text(ASSIGN.format(t=task_type))

    def run_review(self, command="hardness", status="done", actor_admin=0):
        self.n += 1
        rid = f"r{self.n}"
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, actor_admin) values (?,?,?,?,?,?,?)",
              (rid, "a1", "review", command, status, time.time(), actor_admin))
        return tn.after_review(db.get_chat("a1"), db.get_run(rid), status)

    def notes(self):
        return [m for m in db.q("select content, meta from messages where attempt_id='a1' and role='system'")]

    def test_posts_after_hardness_when_short(self):
        self.setup(61)
        msg = self.run_review()
        self.assertIsNotNone(msg)
        self.assertEqual(len(self.notes()), 1)
        self.assertIn("61", self.notes()[0]["content"])
        self.assertIn('"toolcall_note": true', self.notes()[0]["meta"])

    def test_silent_when_comfortably_above(self):
        self.setup(150)
        self.assertIsNone(self.run_review())
        self.assertEqual(self.notes(), [])

    def test_only_finished_hardness_reviews(self):
        self.setup(10)
        self.assertIsNone(self.run_review(command="prompt"))
        self.assertIsNone(self.run_review(status="failed"))
        self.assertIsNone(self.run_review(actor_admin=1))
        self.assertEqual(self.notes(), [])

    def test_flag_off_or_missing_files(self):
        self.setup(10)
        with mock.patch.object(config, "TOOLCALL_NOTE_PILOT", []):
            self.assertIsNone(self.run_review())
        (self.inputs / "opus" / "trajectory.json").unlink()
        self.assertIsNone(self.run_review())
        self.assertEqual(self.notes(), [])


if __name__ == "__main__":
    unittest.main()
