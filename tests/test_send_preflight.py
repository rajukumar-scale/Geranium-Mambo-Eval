"""Send-path fixes from the 2026-11-23 tester audit: a command alone on the last line starts the review; a bare
review command with its inputs missing gets a free checklist instead of a run; an Outlier stdgrade ID gets a hint."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db


async def _noop():
    return None


class ParseCommandTest(unittest.TestCase):
    def test_leading_command(self):
        self.assertEqual(portal.parse_command("/prompt fix the date", None), ("prompt", "fix the date"))
        self.assertEqual(portal.parse_command("/refnotes", None), ("reference-notes", ""))

    def test_trailing_command_alone_on_the_last_line(self):
        self.assertEqual(portal.parse_command('[{"title": "x", "weight": 5}]\n/rubric', None),
                         ("rubric", '[{"title": "x", "weight": 5}]'))
        self.assertEqual(portal.parse_command("notes\n  /refnotes  ", None), ("reference-notes", "notes"))

    def test_command_inside_a_sentence_stays_a_followup(self):
        for text in ("can you explain why /prompt failed?", "help me before I run\nthe /rubric please",
                     "body\n/unknown", "\n/prompt"):
            self.assertEqual(portal.parse_command(text, None), (None, text))

    def test_a_waiting_review_takes_the_message_as_its_answer(self):
        text = "here is the grading\n/stumping"
        self.assertEqual(portal.parse_command(text, "run-1"), (None, text))


class PreflightTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.saved = []
        self.submitted = []
        for p in (
            mock.patch.object(config, "DATA_DIR", self.tmp),
            mock.patch.object(db, "_conn", None),
            mock.patch.object(portal.blocks, "disabled", lambda *a: None),
            mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None),
            mock.patch.object(portal.assignment, "schedule", lambda *a: None),
            mock.patch.object(portal.assignment, "ensure_safe", lambda *a: _noop()),
            mock.patch.object(portal.plan, "maybe_kickoff", lambda *a: None),
            mock.patch.object(portal.spend, "maybe_notice", lambda *a: None),
            mock.patch.object(portal.RUNNER, "submit", self.submitted.append),
            mock.patch.object(portal.rerun_guard, "same_as_last", lambda *a: None),
            mock.patch.object(portal.workspace, "saved_inputs", lambda a: [
                {"key": k, "state": "saved", "files": [{"path": k}]} for k in self.saved]),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", "t1", "u1", "a1@x.com", now, now))
        (self.tmp / "attempts" / "a1" / "logs").mkdir(parents=True)

    def send(self, text, files=()):
        c = TestClient(portal.app)
        c.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "a1", "user_id": "u1", "iat": time.time()}))
        r = c.post("/api/send", json={"text": text, "files": list(files)})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def last_system(self):
        return db.q("select content from messages where attempt_id='a1' and role='system' order by id desc limit 1")[0]["content"]

    def test_bare_rubric_without_a_rubric_is_not_started(self):
        self.saved = ["task-details", "prompt"]
        res = self.send("/rubric")
        self.assertEqual((res, self.submitted), ({"ok": True}, []))
        self.assertIn("your rubric JSON", self.last_system())
        self.assertIn("no cost", self.last_system())

    def test_missing_grading_names_the_stdgrade_field(self):
        self.saved = ["task-details", "prompt", "rubric", "runs-base"]
        self.send("/stumping")
        self.assertEqual(self.submitted, [])
        self.assertIn("Grading Reference field", self.last_system())

    def test_saved_inputs_or_pasted_text_start_the_run(self):
        self.saved = ["task-details", "prompt", "rubric"]
        self.assertTrue(self.send("/rubric")["run"])
        db.ex("delete from runs")
        self.saved = ["task-details"]
        self.assertTrue(self.send('/rubric [{"title": "x", "weight": 5}]')["run"])
        self.assertEqual(len(self.submitted), 2)

    def test_trailing_command_starts_the_review(self):
        self.saved = ["task-details", "prompt", "rubric"]
        res = self.send('[{"title": "x", "weight": 5}]\n/rubric')
        self.assertEqual(res["run"]["command"], "rubric")

    def test_stdgrade_id_gets_the_hint_for_free(self):
        self.send("lh-agentenv-smoke-20261029-009-stdgrade-rmwp3lw9b\ngrading reference.")
        self.assertEqual(self.submitted, [])
        self.assertIn("Task Runner page", self.last_system())


class OverrideLabelTest(unittest.TestCase):
    def test_chat_card_and_pass_image_name_the_original_verdict(self):
        js = (Path(__file__).resolve().parent.parent / "web" / "common.js").read_text()
        self.assertIn("(admin override; original verdict: ${esc(meta.admin_override.original_verdict", js)
        self.assertIn("(original verdict: ${p.admin_override.original_verdict", js)


if __name__ == "__main__":
    unittest.main()
