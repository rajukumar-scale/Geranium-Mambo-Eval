"""Chat replies and answers to a paused review run with a 1-hour prompt cache (runner._cache_ttl, 2026-11-03);
reviews keep Claude Code's 5-minute default."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, runner


class CacheTtlTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        for rid, kind, cmd, cont in (("f1", "followup", None, 0), ("r1", "review", "prompt", 0), ("c1", "review", "prompt", 1)):
            db.ex("insert into runs(id, attempt_id, kind, command, continuation, status, created_at) values (?,?,?,?,?,?,?)",
                  (rid, "a1", kind, cmd, cont, "queued", time.time()))
        self.r = runner.Runner.__new__(runner.Runner)

    def ttl(self, run_id):
        return self.r._env("tok", "/home/runner", run_id).get("CLAUDE_CODE_PROMPT_CACHE_TTL")

    def test_chat_reply_and_paused_review_answer_get_one_hour(self):
        with mock.patch.object(config, "CHAT_PROMPT_CACHE_TTL", "1h"):
            self.assertEqual(self.ttl("f1"), "1h")
            self.assertEqual(self.ttl("c1"), "1h")

    def test_reviews_keep_the_default(self):
        with mock.patch.object(config, "CHAT_PROMPT_CACHE_TTL", "1h"):
            self.assertIsNone(self.ttl("r1"))
            self.assertIsNone(self.ttl(None))

    def test_switch_off(self):
        for off in ("", "off", "2h"):
            with mock.patch.object(config, "CHAT_PROMPT_CACHE_TTL", off):
                self.assertIsNone(self.ttl("f1"), off)

    def test_default_is_one_hour(self):
        self.assertEqual(config.CHAT_PROMPT_CACHE_TTL, "1h")


if __name__ == "__main__":
    unittest.main()
