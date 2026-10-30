"""A review that hits the time limit is paused, not lost (2026-10-30): the next plain message continues the same session
once, with the box's /tmp scratch files; changed inputs or a fresh command end the pause."""

import asyncio
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import boatrun, config, db, pause, runner, staleness, workspace


class PauseTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.inputs = {"rubric": "v1"}
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(workspace, "attempt_dir", lambda a: self.tmp / "attempts" / a),
                   mock.patch.object(staleness, "snapshot", lambda a: dict(self.inputs)),
                   mock.patch.object(runner.Runner, "_check_spend", mock.AsyncMock()),
                   mock.patch.object(runner.Runner, "_stale_notice", lambda *a: None),
                   mock.patch.object(runner, "run_cost", lambda run, cc: 1.0)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        (self.tmp / "attempts" / "a1" / "logs").mkdir(parents=True)
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", "t1", "u1", "a1@x.com", now, now))

    def run_row(self, rid, continuation=0):
        db.ex("insert into runs(id, attempt_id, kind, command, status, session_id, continuation, created_at, started_at) "
              "values (?,?,?,?,?,?,?,?,?)", (rid, "a1", "review", "rubric", "running", "S-1", continuation, time.time(), time.time()))

    def complete(self, rid, timeout=True, scratch=None):
        outcome = {"events": 3, "result": None, "texts": ["Once R2 returns I'll save the report."], "reports": {}}
        if timeout:
            outcome["timeout"] = True
        if scratch:
            outcome["scratch"] = scratch
        asyncio.run(runner.Runner()._complete(rid, outcome))

    def messages(self):
        return db.q("select role, content, meta from messages where attempt_id='a1' order by id")

    def test_a_timeout_pauses_the_review_and_keeps_its_scratch(self):
        self.run_row("r1")
        scratch = self.tmp / "attempts" / "a1" / "logs" / "r1.scratch.tgz"
        scratch.write_bytes(b"x")
        self.complete("r1", scratch=scratch)
        state = pause.load("a1")
        self.assertEqual((state["run_id"], state["command"]), ("r1", "rubric"))
        self.assertTrue(pause.scratch("a1"))
        self.assertEqual(db.get_chat("a1")["pending_review_run"], "r1")
        texts = [m["content"] for m in self.messages()]
        self.assertTrue(any("paused" in t for t in texts))
        self.assertFalse(any("Once R2 returns" in t for t in texts))  # its mid-review line is not an answer
        self.assertEqual(db.get_run("r1")["status"], "failed")  # still counted as a timeout on the status page

    def test_only_one_continuation(self):
        self.run_row("r1")
        self.complete("r1")
        self.run_row("r2", continuation=1)
        self.complete("r2")  # the continuation times out too: a normal failure, the pause is gone
        self.assertIsNone(pause.load("a1"))
        self.assertIsNone(db.get_chat("a1")["pending_review_run"])
        self.assertTrue(any("didn't finish" in m["content"] for m in self.messages()))

    def test_a_finished_continuation_clears_the_pause(self):
        self.run_row("r1")
        self.complete("r1")
        self.run_row("r2", continuation=1)
        self.complete("r2", timeout=False)
        self.assertIsNone(pause.load("a1"))

    def test_other_failures_and_continuations_are_not_paused(self):
        self.run_row("r1", continuation=1)  # answering a review's question, then timing out
        self.complete("r1")
        self.assertIsNone(pause.load("a1"))

    def test_changed_inputs_block_the_continuation(self):
        self.run_row("r1")
        self.complete("r1")
        state = pause.load("a1")
        self.assertFalse(pause.inputs_changed("a1", state))
        self.inputs["rubric"] = "v2"
        self.assertTrue(pause.inputs_changed("a1", state))

    def test_the_continuation_upload_carries_the_scratch_files(self):
        (self.tmp / "attempts" / "a1" / "task_inputs").mkdir()
        (self.tmp / "attempts" / "a1" / "cache").mkdir()
        (self.tmp / "attempts" / "a1" / "home").mkdir()
        (self.tmp / "bundle.tgz").write_bytes(b"b")
        scratch = self.tmp / "scratch.tgz"
        scratch.write_bytes(b"s")
        with mock.patch.object(boatrun, "_bundle", lambda: self.tmp / "bundle.tgz"):
            payload = boatrun.build_payload("r2", "a1", "continue", ["claude", "--resume", "S-1"], {}, None, scratch)
        with tarfile.open(payload) as tar:
            self.assertIn("scratch.tgz", tar.getnames())
        self.assertIn("scratch.tgz", boatrun.RUN_SH)
        self.assertIn("-user runner", boatrun.COLLECT_SH)


class ContinueMessageTest(PauseTest):
    """The chat's next plain message after a pause: a continuation of the same session with the resume note."""

    def send(self, text):
        from fastapi.testclient import TestClient
        from server import app as portal, auth
        submitted = []
        with mock.patch.object(portal.RUNNER, "submit", submitted.append), \
                mock.patch.object(portal.blocks, "disabled", lambda *a: None), \
                mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None):
            client = TestClient(portal.app)
            client.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "a1", "user_id": "u1", "iat": time.time()}))
            res = client.post("/api/send", json={"text": text})
        self.assertEqual(res.status_code, 200, res.text)
        return db.get_run(submitted[0])

    def paused(self):
        self.run_row("r1")
        self.complete("r1")
        db.ex("update runs set status='failed' where id='r1'")

    def test_continue_resumes_the_same_session_with_the_note(self):
        self.paused()
        run = self.send("continue")
        self.assertEqual((run["kind"], run["command"], run["continuation"], run["session_id"]), ("review", "rubric", 1, "S-1"))
        prompt = (self.tmp / "attempts" / "a1" / "logs" / f"{run['id']}.prompt.txt").read_text()
        self.assertIn("stopped by the", prompt)
        self.assertTrue(prompt.rstrip().endswith("continue"))

    def test_changed_inputs_turn_the_message_into_a_normal_question(self):
        self.paused()
        self.inputs["rubric"] = "v2"
        run = self.send("continue")
        self.assertEqual(run["kind"], "followup")
        self.assertIsNone(pause.load("a1"))
        self.assertTrue(any("can't continue" in m["content"] for m in self.messages()))


if __name__ == "__main__":
    unittest.main()
