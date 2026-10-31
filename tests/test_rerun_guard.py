"""Identical re-runs (2026-10-31): a bare review command on exactly the inputs its last finished review graded isn't
started; it can be run once more anyway; changed inputs, extra text, an attachment or an admin always run."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, rerun_guard


class RerunGuardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.saved = {"prompt": "v1", "injection": "sql1"}
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(rerun_guard.staleness, "snapshot", lambda a: dict(self.saved)),
                   mock.patch.object(portal.blocks, "disabled", lambda *a: None),
                   mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", "t1", "u1", "a1@x.com", now, now))
        (self.tmp / "attempts" / "a1" / "logs").mkdir(parents=True)

    def finished_review(self, rid="r1", command="prompt", verdict="FAIL", forced=0):
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, finished_at, report_stage, verdict, "
              "cost_estimate, rerun_forced) values (?,?,?,?,?,?,?,?,?,?,?)",
              (rid, "a1", "review", command, "done", time.time() - 900, time.time() - 600, command, verdict, 11.4, forced))
        rerun_guard.record(rid, "a1", command)

    def send(self, text, admin=False, **extra):
        submitted = []
        session = {"attempt_id": "a1", "user_id": "u1", "iat": time.time()}
        if admin:
            session["admin"] = "evan@scale.com"
        with mock.patch.object(portal.RUNNER, "submit", submitted.append), \
                mock.patch.object(portal, "admin_role", lambda email: "admin" if admin else None):
            client = TestClient(portal.app)
            client.cookies.set(auth.USER_COOKIE, auth.sign(session))
            res = client.post("/api/send", json={"text": text, **extra})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json(), submitted

    def test_unchanged_inputs_are_not_reviewed_again(self):
        self.finished_review()
        res, started = self.send("/prompt")
        self.assertFalse(started)
        self.assertEqual((res["identical"]["verdict"], res["identical"]["forced"]), ("FAIL", False))
        self.assertEqual(res["identical"]["cost"], 11.4)

    def test_run_it_anyway_once_then_not_again(self):
        self.finished_review()
        res, started = self.send("/prompt", force=True)
        self.assertEqual(len(started), 1)
        self.assertEqual(db.get_run(started[0])["rerun_forced"], 1)
        db.ex("delete from runs where id=?", (started[0],))
        self.finished_review("r2", forced=1)  # that forced run finished on the same inputs
        res, started = self.send("/prompt", force=True)
        self.assertFalse(started)
        self.assertTrue(res["identical"]["forced"])

    def test_changed_inputs_or_extra_text_or_admin_always_run(self):
        self.finished_review()
        self.saved["prompt"] = "v2"
        self.assertTrue(self.send("/prompt")[1])
        db.ex("delete from runs where id != 'r1'")
        self.saved["prompt"] = "v1"
        self.assertTrue(self.send("/prompt here is my new prompt ...")[1])
        db.ex("delete from runs where id != 'r1'")
        self.assertTrue(self.send("/prompt", admin=True)[1])

    def test_an_input_the_stage_doesnt_read_doesnt_count(self):
        self.finished_review(command="injection", verdict="PASS")
        self.saved["prompt"] = "v2"  # /injection reads only the SQL and the date
        self.assertFalse(self.send("/injection")[1])

    def test_a_new_eval_release_runs_again(self):
        self.finished_review()
        db.ex("update runs set inputs_after=? where id='r1'",
              (json.dumps({"release": "older", "inputs": {"prompt": "v1", "injection": "sql1"}}),))
        self.assertTrue(self.send("/prompt")[1])

    def test_reviews_without_a_record_never_block(self):
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, finished_at, report_stage, verdict) "
              "values ('old','a1','review','prompt','done',?,?,'prompt','FAIL')", (time.time() - 900, time.time() - 600))
        self.assertTrue(self.send("/prompt")[1])


if __name__ == "__main__":
    unittest.main()
