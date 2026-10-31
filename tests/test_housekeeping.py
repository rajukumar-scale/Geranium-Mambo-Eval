"""Disk housekeeping (2026-10-31, after the disk filled up): stale upload packages go, old run logs are gzipped,
Slack hears about low disk."""

import asyncio
import gzip
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, housekeeping, workspace


class HousekeepingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for p in (mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        self.logs = self.tmp / "attempts" / "a1" / "logs"
        self.logs.mkdir(parents=True)
        self.now = time.time()

    def _file(self, name, age, data=b"x" * 1000):
        path = self.logs / name
        path.write_bytes(data)
        os.utime(path, (self.now - age, self.now - age))
        return path

    def test_stale_upload_packages_go_fresh_ones_stay(self):
        old = self._file("r1.payload.tgz", 7 * 3600)
        fresh = self._file("r2.payload.tgz", 600)
        folder = self.logs / "r3.payload"
        folder.mkdir()
        (folder / "bundle.tgz").write_bytes(b"y" * 500)
        os.utime(folder, (self.now - 7 * 3600, self.now - 7 * 3600))
        log = self._file("r1.jsonl", 7 * 3600)
        self.assertEqual(housekeeping.sweep_payloads(self.now), (2, 1500))
        self.assertFalse(old.exists() or folder.exists())
        self.assertTrue(fresh.exists() and log.exists())

    def test_old_finished_logs_are_gzipped_intact(self):
        body = b'{"type":"result"}\n' * 500
        for rid, finished, status in (("old", self.now - 4 * 86400, "done"), ("new", self.now - 3600, "done"),
                                      ("live", None, "running")):
            db.ex("insert into runs(id, attempt_id, status, created_at, finished_at) values (?,?,?,?,?)",
                  (rid, "a1", status, self.now - 5 * 86400, finished))
            self._file(f"{rid}.jsonl", 0, body)
        done, saved = housekeeping.gzip_logs(self.now)
        self.assertEqual(done, 1)
        self.assertGreater(saved, 0)
        self.assertFalse((self.logs / "old.jsonl").exists())
        self.assertEqual(gzip.decompress((self.logs / "old.jsonl.gz").read_bytes()), body)
        self.assertTrue((self.logs / "new.jsonl").exists() and (self.logs / "live.jsonl").exists())
        self.assertEqual(housekeeping.gzip_logs(self.now), (0, 0))  # already done

    def test_low_disk_alert_is_throttled(self):
        sent = []
        with mock.patch.object(housekeeping, "free_gb", lambda: 4.2), \
             mock.patch.object(housekeeping.notify, "slack", mock.AsyncMock(side_effect=lambda t: sent.append(t))), \
             mock.patch.dict(housekeeping._state, {"alerted": 0.0}):
            asyncio.run(housekeeping.check_disk(self.now))
            asyncio.run(housekeeping.check_disk(self.now + 3600))
            asyncio.run(housekeeping.check_disk(self.now + 7 * 3600))
        self.assertEqual(len(sent), 2)
        self.assertIn("4.2 GB free", sent[0])

    def _chat(self, attempt, idle_days):
        db.ex("insert into chats(attempt_id, task_id, user_id, created_at, last_activity) values (?,?,?,?,?)",
              (attempt, "t", "u", self.now - 30 * 86400, self.now - idle_days * 86400))
        root = workspace.ensure_attempt(attempt)
        session = root / "home/.claude/projects/-work/abc.jsonl"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text('{"type":"user"}\n' * 2000)
        os.utime(session, (self.now - 9 * 86400, self.now - 9 * 86400))
        (root / "cache/records").mkdir(parents=True, exist_ok=True)
        (root / "cache/records/r.json").write_text("[]" * 1000)
        (root / "task_inputs/prompt.md").write_text("the prompt")
        return root

    def test_idle_chats_are_cold_stored_and_come_back_intact(self):
        idle, recent, running = self._chat("idle", 3), self._chat("recent", 0.5), self._chat("running", 5)
        db.ex("insert into runs(id, attempt_id, status, created_at) values ('r1', 'running', 'running', ?)", (self.now,))
        chats, freed = housekeeping.freeze_idle(self.now)
        self.assertEqual(chats, 1)
        self.assertGreater(freed, 0)
        self.assertTrue((idle / "cold.tgz").is_file())
        self.assertFalse((idle / "home").exists() or (idle / "cache").exists())
        self.assertTrue((idle / "task_inputs/prompt.md").is_file())  # the pages still read task_inputs
        self.assertFalse(workspace.is_cold("recent") or workspace.is_cold("running"))
        workspace.ensure_attempt("idle")  # any run, follow-up or task-date save unpacks it first
        session = idle / "home/.claude/projects/-work/abc.jsonl"
        self.assertEqual(session.read_text(), '{"type":"user"}\n' * 2000)
        self.assertAlmostEqual(session.stat().st_mtime, self.now - 9 * 86400, delta=2)
        self.assertEqual((idle / "cache/records/r.json").read_text(), "[]" * 1000)
        self.assertFalse(workspace.is_cold("idle"))

    def test_low_disk_cold_stores_sooner_and_gzips_younger_logs(self):
        self._chat("quiet", 0.5)
        with mock.patch.object(housekeeping, "free_gb", lambda: 3.0), mock.patch.object(housekeeping.notify, "slack", mock.AsyncMock()), \
             mock.patch.dict(housekeeping._state, {"alerted": 0.0}):
            asyncio.run(housekeeping.run_once())
        self.assertTrue(workspace.is_cold("quiet"))

    def test_a_run_starting_mid_pack_is_never_frozen(self):
        self._chat("busy", 3)
        self.assertEqual(workspace.freeze("busy", busy=lambda: True), 0)
        self.assertFalse(workspace.is_cold("busy"))


if __name__ == "__main__":
    unittest.main()
