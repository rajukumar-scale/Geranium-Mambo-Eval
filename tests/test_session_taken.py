"""A chat reply cut off by a restart can leave the chat's new session ID with a transcript but session_started unset;
every later message then failed at once with "Session ID ... is already in use" (60 chats, 2026-11-01). The runner now
starts a fresh session once and saves it on the chat."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, runner, workspace


class SessionTakenTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, session_started, session_guard, created_at, "
              "last_activity) values (?,?,?,?,?,?,?,?,?)", ("a1", "t1", "u1", "a@x.com", "S-old", 0, runner.GUARD_VERSION, now, now))
        db.ex("insert into runs(id, attempt_id, kind, status, created_at) values ('r1','a1','followup','queued',?)", (now,))
        (workspace.ensure_attempt("a1") / "logs" / "r1.prompt.txt").write_text("save this as my prompt")

    def test_a_taken_session_id_gets_a_fresh_session_once(self):
        calls = []

        async def spawn(run_id, chat, prompt, session_args):
            calls.append(session_args)
            out = {"events": 0, "result": None, "texts": [], "init": False, "stderr_tail": "", "before": {}, "after": {},
                   "reports": {}}
            if len(calls) == 1:
                out.update(stderr_tail="Error: Session ID S-old is already in use.", session_taken=True)
            else:
                out.update(init=True, result={"result": "Saved.", "is_error": False, "subtype": "success"})
            return out

        r = runner.Runner()
        with mock.patch.object(r, "_spawn", spawn), mock.patch.object(r, "_complete", mock.AsyncMock()):
            asyncio.run(r._execute("r1"))
        self.assertEqual(calls[0], ["--session-id", "S-old"])
        self.assertEqual(calls[1][0], "--session-id")
        self.assertNotEqual(calls[1][1], "S-old")
        self.assertEqual(db.get_chat("a1")["session_id"], calls[1][1])


if __name__ == "__main__":
    unittest.main()
