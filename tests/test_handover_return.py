"""A task the platform hands back to someone who held it before gets their own chat back, not a blank one
(2026-11-04: fikanoor's $608 chat and 10 others in 3 weeks were wiped on return)."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, handover, workspace


class ReturnTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        self.t0 = time.time() - 3600
        db.ex("insert into chats(attempt_id, task_id, project_id, user_id, email, session_id, created_at, last_activity) "
              "values ('a1','t1','p','uA','a@x.com','sA',?,?)", (self.t0, self.t0))
        db.add_message("a1", "system", "Welcome!")
        db.add_message("a1", "user", "/prompt my prompt")
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, finished_at, cost_estimate) "
              "values ('r1','a1','review','prompt','done',?,?,12.5)", (self.t0, self.t0 + 60))
        root = workspace.ensure_attempt("a1")
        (root / "task_inputs" / "04_Prompt.txt").write_text("A's prompt")

    def hand_to(self, user, email):
        """What sign-in does when the platform re-served the attempt: archive the current holder, then the new holder's
        own earlier chat if there is one, else a fresh chat."""
        chat = db.get_chat("a1")
        if chat and chat["user_id"] != user:
            handover.archive("a1")
        back = handover.restore("a1", user)
        if not back:
            db.upsert_chat("a1", "t1", "p", user, email, "s-" + user)
            if not db.one("select 1 from messages where attempt_id='a1'"):
                db.add_message("a1", "system", "Welcome!")
        return back

    def test_the_earlier_holder_gets_their_own_chat_back(self):
        self.assertIsNone(self.hand_to("uB", "b@x.com"))  # B gets a fresh chat; A's moves aside
        self.assertEqual(db.get_chat("a1")["user_id"], "uB")
        self.assertFalse((workspace.attempt_dir("a1") / "task_inputs" / "04_Prompt.txt").exists())
        back = self.hand_to("uA", "a@x.com")  # B never did anything; the task comes back to A
        self.assertTrue(back)
        chat = db.get_chat("a1")
        self.assertEqual((chat["user_id"], chat["email"], chat["archived_from"]), ("uA", "a@x.com", None))
        self.assertEqual((workspace.attempt_dir("a1") / "task_inputs" / "04_Prompt.txt").read_text(), "A's prompt")
        roles = [m["role"] for m in db.messages("a1")]
        self.assertIn("user", roles)
        self.assertTrue(any(m["meta"].get("welcome_back") for m in db.messages("a1")))
        self.assertEqual(db.one("select attempt_id from runs where id='r1'")["attempt_id"], "a1")  # spend comes back too
        self.assertIsNone(db.get_chat(back))

    def test_the_middle_holders_work_is_kept_aside_not_mixed_in(self):
        self.hand_to("uB", "b@x.com")
        db.add_message("a1", "user", "B's message")
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, finished_at) "
              "values ('rB','a1','review','prompt','done',?,?)", (time.time(), time.time()))
        self.assertTrue(self.hand_to("uA", "a@x.com"))
        self.assertNotIn("B's message", [m["content"] for m in db.messages("a1")])
        b_archive = db.one("select attempt_id from chats where archived_from='a1' and user_id='uB'")
        self.assertTrue(b_archive)
        self.assertEqual(db.one("select attempt_id from runs where id='rB'")["attempt_id"], b_archive["attempt_id"])

    def test_no_restore_once_they_worked_in_the_new_chat(self):
        self.hand_to("uB", "b@x.com")
        self.hand_to("uA", "a@x.com")  # restored
        db.add_message("a1", "user", "A's new work")
        self.hand_to("uB", "b@x.com")  # away again ...
        db.add_message("a1", "user", "B again")
        self.hand_to("uA", "a@x.com")  # ... and back: A's newest archive is the one restored
        contents = [m["content"] for m in db.messages("a1")]
        self.assertIn("A's new work", contents)
        self.assertNotIn("B again", contents)
        # A fresh chat with A's own work in it is never overwritten by an older archive of A's
        db.add_message("a1", "user", "A keeps going")
        self.assertIsNone(handover.restore("a1", "uA"))

    def test_a_running_review_waits(self):
        self.hand_to("uB", "b@x.com")
        aid = db.one("select attempt_id from chats where archived_from='a1' and user_id='uA'")["attempt_id"]
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at) values ('rx',?,'review','oe','running',?)",
              (aid, time.time()))
        with self.assertRaises(handover.Busy):
            handover.restore("a1", "uA")


if __name__ == "__main__":
    unittest.main()
