"""Submit watch (2026-11-05): a task submitted while one of its portal passes is outdated DMs Evan once."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, proofs, staleness, submissions, submit_watch


class SubmitWatchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.saved = {"prompt": "p1", "injection": "sql1", "oe": "oe1", "rubric": "r1"}
        for p in [mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.object(staleness, "snapshot", lambda a: dict(self.saved)),
                  mock.patch.object(submissions, "fresh", lambda *a: True)]:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", "t1", "u1", "ebad@x.com", now, now))
        self.dms = []
        async def dm(text):
            self.dms.append(text)
            return self.ok
        self.ok = True
        p = mock.patch.object(submit_watch, "send", dm)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(submissions._latest.clear)
        self.n = 0

    def passed(self, stage):
        self.n += 1
        run = {"id": f"r{self.n}", "attempt_id": "a1", "command": stage, "models": {}, "started_at": time.time(),
               "finished_at": time.time()}
        report = {"stage": stage, "verdict": "PASS", "score": 5, "exec_status": "COMPLETE", "file": "x.md",
                  "sha256": "ab" * 32, "fingerprint": "fp"}
        proofs.create(run, db.get_chat("a1"), report, inputs=staleness.snapshot("a1"))

    def check(self):
        return asyncio.run(submit_watch.check_once())

    def submit(self, ago=0):
        submissions._latest[("t1", "u1")] = time.time() - ago

    def test_first_run_is_a_baseline_and_sends_nothing(self):
        self.passed("rubric")
        self.saved["injection"] = "sql2"
        self.submit(ago=60)
        self.assertEqual(self.check(), [])
        self.assertEqual(self.dms, [])

    def test_submit_with_outdated_passes_dms_once(self):
        self.check()  # baseline
        for stage in ("injection", "prompt", "oe", "rubric"):
            self.passed(stage)
        self.saved["injection"] = "sql2"
        self.passed("injection")
        self.passed("prompt")
        time.sleep(0.01)
        self.submit()
        hits = self.check()
        self.assertEqual(len(hits), 1)
        self.assertEqual(sorted(hits[0]["outdated"]), ["oe", "rubric"])
        self.assertEqual(len(self.dms), 1)
        self.assertIn("ebad@x.com", self.dms[0])
        self.assertIn("/rubric: injection SQL changed after it passed", self.dms[0])
        self.check()
        self.assertEqual(len(self.dms), 1)  # the same submit is never sent twice

    def test_clean_submit_sends_nothing(self):
        self.check()
        for stage in ("injection", "prompt", "oe", "rubric"):
            self.passed(stage)
        time.sleep(0.01)
        self.submit()
        self.assertEqual(self.check(), [])
        self.assertEqual(self.dms, [])

    def test_failed_dm_is_retried_next_check(self):
        self.check()
        self.passed("rubric")
        self.saved["rubric"] = "r2"
        time.sleep(0.01)
        self.submit()
        self.ok = False
        self.check()
        self.ok = True
        self.check()
        self.assertEqual(len(self.dms), 2)

    def test_off_switch(self):
        with mock.patch.object(config, "SUBMIT_WATCH", 0), mock.patch.object(config, "SLACK_BOT_TOKEN", "x"), \
                mock.patch.object(submit_watch, "check_once") as check:
            asyncio.run(submit_watch.after_refresh())
        check.assert_not_called()

    def test_sends_by_dm_or_falls_back_to_the_webhook(self):
        sent = []
        async def dm(text):
            sent.append(("dm", text)); return True
        async def hook(text):
            sent.append(("webhook", text)); return True
        with mock.patch.object(submit_watch.linter_alerts, "dm", dm), mock.patch.object(submit_watch.notify, "slack", hook), \
                mock.patch.object(submit_watch.notify, "configured", lambda: True):
            with mock.patch.object(config, "SLACK_BOT_TOKEN", ""), mock.patch.object(config, "SLACK_DM_WEBHOOK_URL", ""):
                self.assertTrue(asyncio.run(submit_watch.__dict__["_send_real"]("a")))
            with mock.patch.object(config, "SLACK_BOT_TOKEN", "x"):
                self.assertTrue(asyncio.run(submit_watch.__dict__["_send_real"]("b")))
        self.assertEqual(sent, [("webhook", "a"), ("dm", "b")])

    def test_message_links_the_chat(self):
        hit = {"attempt_id": "a1", "task_id": "t1", "email": "ebad@x.com", "at": 1762300000,
               "outdated": {"rubric": {"labels": ["injection SQL"]}}}
        text = submit_watch.message([hit])
        self.assertIn("/admin", text)
        self.assertIn("/rubric: injection SQL changed after it passed", text)


if __name__ == "__main__":
    unittest.main()
