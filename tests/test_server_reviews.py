"""Server first, boat as overflow (2026-10-30): /injection and /hardness run on the portal's own machine while a server
slot is free, memory allows and no deploy is waiting; otherwise on a boat box. They never queue for a server slot."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, hostload, runner, runner_image, status


class PlacementTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        patches = [mock.patch.object(config, "DATA_DIR", self.data), mock.patch.object(config, "REVIEW_RUNNER", "boat"),
                   mock.patch.object(config, "RUNNER_MODE", "docker"), mock.patch.object(config, "SERVER_REVIEW_SLOTS", 2),
                   mock.patch.object(config, "SERVER_REVIEW_COMMANDS", ["injection", "hardness"]),
                   mock.patch.object(config, "SERVER_REVIEW_MIN_FREE_GB", 5.0)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.free = 9.0
        self.image = True
        for p in (mock.patch.object(hostload, "mem_available_gb", lambda: self.free),
                  mock.patch.object(runner_image, "present", lambda *a: self.image)):
            p.start()
            self.addCleanup(p.stop)
        self.r = runner.Runner()

    def place(self, command, kind="review"):
        return self.r.place({"kind": kind, "command": command})

    def test_light_review_runs_here_when_a_slot_and_memory_are_free(self):
        self.assertEqual(self.place("injection"), "server")
        self.assertEqual(self.place("hardness"), "server")

    def test_other_reviews_and_followups_keep_their_place(self):
        self.assertEqual(self.place("prompt"), "boat")
        self.assertEqual(self.place("rubric"), "boat")
        self.assertEqual(self.place(None, kind="followup"), "main")

    def test_missing_review_image_sends_reviews_to_boat(self):
        self.image = False  # 2026-10-31: the image vanished and every server review ended at once
        self.assertEqual(self.place("injection"), "boat")

    def test_full_slots_overflow_to_boat_instead_of_queueing(self):
        self.r.server_held.update({"a", "b"})
        self.assertEqual(self.place("injection"), "boat")
        self.r._release_server_slot("a")
        self.assertEqual(self.place("injection"), "server")

    def test_low_or_unknown_memory_overflows_to_boat(self):
        self.free = 4.9
        self.assertEqual(self.place("injection"), "boat")
        self.free = None  # not Linux: never place here
        self.assertEqual(self.place("injection"), "boat")

    def test_a_waiting_deploy_sends_new_reviews_to_boat(self):
        (self.data / "DEPLOY_PENDING").touch()
        self.assertEqual(self.place("injection"), "boat")

    def test_off_without_docker_or_slots(self):
        with mock.patch.object(config, "RUNNER_MODE", "local"):
            self.assertEqual(self.place("injection"), "boat")
        with mock.patch.object(config, "SERVER_REVIEW_SLOTS", 0):
            self.assertEqual(self.place("injection"), "boat")


class StatusServerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, name, value in ((config, "DATA_DIR", Path(tmp.name)), (db, "_conn", None),
                                    (config, "REVIEW_RUNNER", "boat"), (config, "SERVER_REVIEW_SLOTS", 2)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()

    def test_counts_where_reviews_ran_without_naming_anyone(self):
        now = time.time()
        rows = [("r1", "review", "injection", "running", "server", None, now - 60),
                ("r2", "review", "injection", "done", "server", None, now - 3600),
                ("r3", "review", "prompt", "done", "started", "bx_1", now - 3600),
                ("r4", "followup", None, "running", None, None, now - 30),
                ("r5", "review", "prompt", "done", "started", "bx_2", now - 2 * 86400)]
        for rid, kind, cmd, st, phase, box, ca in rows:
            db.ex("insert into runs(id, attempt_id, kind, command, status, boat_phase, runner_box, created_at) "
                  "values (?,?,?,?,?,?,?,?)", (rid, "a1", kind, cmd, st, phase, box, ca))
        s = status._server(now)
        self.assertEqual((s["reviews_here_now"], s["followups_now"], s["review_slots"]), (1, 1, 2))
        self.assertEqual((s["reviews_here_24h"], s["reviews_machines_24h"]), (2, 1))
        for key in ("memory_free_gb", "memory_total_gb", "load1", "cpus", "disk_free_pct"):
            self.assertIn(key, s)
        self.assertFalse([k for k in s if "email" in k or "attempt" in k or "task" in k])


if __name__ == "__main__":
    unittest.main()
