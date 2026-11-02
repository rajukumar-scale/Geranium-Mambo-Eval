"""2026-11-02: runs crashed on "database is locked", and the crash handler's own write hit the same lock, so the runs
stayed "running" forever. A chat with a "running" run is busy, so the contributor was stuck (and Cancel didn't help).
Now the crash write is retried, Cancel closes a run nothing is executing, and a sweep closes any that slip through."""

import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, runner


class OrphanRunsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        self.old = time.time() - 600
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", "t1", "u1", "a@x.com", self.old, self.old))

    def add_run(self, run_id, status="running", started=None):
        started = self.old if started is None else started
        db.ex("insert into runs(id, attempt_id, kind, status, created_at, started_at) values (?,?,?,?,?,?)",
              (run_id, "a1", "followup", status, started, started))

    def system_messages(self):
        return [m["content"] for m in db.q("select content from messages where attempt_id='a1' and role='system'")]

    def test_crash_write_is_retried_through_a_lock(self):
        self.add_run("r1")
        r = runner.Runner()
        real_update, fails = db.update, [2]

        def flaky(*a, **k):
            if fails[0]:
                fails[0] -= 1
                raise sqlite3.OperationalError("database is locked")
            return real_update(*a, **k)

        with mock.patch.object(db, "update", flaky), mock.patch.object(runner.asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(r._fail_crashed("r1", sqlite3.OperationalError("database is locked")))
        self.assertEqual(db.get_run("r1")["status"], "failed")
        self.assertIsNone(db.active_run("a1"))
        self.assertEqual(len(self.system_messages()), 1)

    def test_cancel_closes_a_run_nothing_is_executing(self):
        self.add_run("r1")
        r = runner.Runner()
        self.assertTrue(asyncio.run(r.cancel("r1")))
        run = db.get_run("r1")
        self.assertEqual((run["status"], run["error"]), ("cancelled", runner.CANCELLED))
        self.assertEqual(self.system_messages(), ["Run cancelled."])

    def test_sweep_skips_live_and_young_runs(self):
        self.add_run("orphan")
        self.add_run("live")
        self.add_run("young", started=time.time())
        r = runner.Runner()
        r.live.add("live")
        self.assertEqual([x["id"] for x in r._orphans()], ["orphan"])

    def test_finished_run_is_left_alone(self):
        self.add_run("r1", status="done")
        asyncio.run(runner.Runner()._fail_crashed("r1", "x"))
        self.assertEqual(db.get_run("r1")["status"], "done")
        self.assertEqual(self.system_messages(), [])


if __name__ == "__main__":
    unittest.main()
