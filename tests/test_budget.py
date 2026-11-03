"""Per-task budget (Evan, 2026-11-03): $400 per attempt; a warning when the chat opens; at the budget, new reviews and
replies stop until an admin approves more (/admin and /help still work; a running review finishes)."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, spend


class BudgetTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(config, "CB_SPEND_LIMIT_USD", 400.0),
                   mock.patch.object(portal.blocks, "disabled", lambda *a: None),
                   mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None),
                   mock.patch.object(portal.rerun_guard, "same_as_last", lambda *a: None),
                   mock.patch.object(spend.notify, "slack", mock.AsyncMock(return_value=False)),
                   mock.patch.object(portal.notify, "slack", mock.AsyncMock(return_value=False)),
                   mock.patch.object(portal, "admin_role", lambda email: "owner" if email else None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
              "values ('a1','t1','u1','a1@x.com','s1',?,?)", (now, now))
        (self.tmp / "attempts" / "a1" / "logs").mkdir(parents=True)

    def spent(self, usd, rid="r0"):
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, finished_at, cost_estimate) "
              "values (?,?,?,?,?,?,?,?)", (rid, "a1", "review", "prompt", "done", time.time() - 60, time.time(), usd))

    def client(self, admin=False):
        c = TestClient(portal.app)
        session = {"attempt_id": "a1", "user_id": "u1", "iat": time.time()}
        if admin:
            session["admin"] = "evan@scale.com"
            c.cookies.set(auth.ADMIN_COOKIE, auth.sign({"email": "evan@scale.com", "iat": time.time()}))
        c.cookies.set(auth.USER_COOKIE, auth.sign(session))
        return c

    def send(self, text, admin=False):
        with mock.patch.object(portal.RUNNER, "submit", lambda run_id: None):
            return self.client(admin).post("/api/send", json={"text": text})

    def test_under_the_budget_runs_start(self):
        self.spent(399.0)
        self.assertEqual(self.send("/prompt").status_code, 200)

    def test_at_the_budget_new_runs_need_an_admins_approval(self):
        self.spent(400.5)
        r = self.send("/prompt")
        self.assertEqual(r.status_code, 402)
        self.assertIn("$400 budget", r.json()["detail"])
        self.assertEqual(self.send("a question").status_code, 402)  # replies too
        self.assertEqual(self.send("/admin I need one more /rubric").status_code, 200)  # can always ask
        self.assertEqual(self.send("/help").status_code, 200)
        self.assertEqual(self.send("/prompt", admin=True).status_code, 200)  # an admin's own runs aren't stopped

    def test_an_admin_approves_more_and_the_contributor_can_run_again(self):
        self.spent(410.0)
        r = self.client(admin=True).post("/api/admin/chat/a1/budget", json={"add_usd": 100, "note": "last rubric"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["limit"], 510.0)  # spend + approved amount
        self.assertEqual(r.json()["approved"]["approved_by"], "evan@scale.com")
        self.assertEqual(self.send("/rubric").status_code, 200)
        last = db.q("select content from messages where attempt_id='a1' and role='system' order by id desc")
        self.assertTrue(any("approved **$100** more" in m["content"] for m in last))
        self.assertEqual(self.client().post("/api/admin/chat/a1/budget", json={"add_usd": 100}).status_code, 401)
        self.assertEqual(self.client(admin=True).post("/api/admin/chat/a1/budget", json={"add_usd": 0}).status_code, 400)

    def test_the_warning_is_posted_once_when_the_chat_opens(self):
        for _ in range(2):
            self.assertEqual(self.client().get("/api/me").status_code, 200)
        notes = db.q("select content, meta from messages where attempt_id='a1' and meta like '%budget_notice%'")
        self.assertEqual(len(notes), 1)
        self.assertIn("Budget for this task: $400", notes[0]["content"])
        self.assertIn("don't go over $400", notes[0]["content"])

    def test_reaching_a_raised_budget_flags_and_tells_them_again(self):
        self.spent(401.0)
        asyncio.run(spend.check_limit("a1"))
        spend.approve("a1", 100, "evan@scale.com")
        self.spent(110.0, rid="r1")
        asyncio.run(spend.check_limit("a1"))
        flags = db.q("select content from messages where attempt_id='a1' and meta like '%spend_flag%'")
        self.assertEqual(len(flags), 2)
        self.assertIn("**$501** budget", flags[-1]["content"])
        asyncio.run(spend.check_limit("a1"))  # no repeat for the same budget
        self.assertEqual(len(db.q("select 1 from messages where attempt_id='a1' and meta like '%spend_flag%'")), 2)


if __name__ == "__main__":
    unittest.main()
