"""The /admin chat list: the grouped queries give what the old per-chat subqueries gave, one build is shared for a few
seconds, and an admin change rebuilds it."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db

# The list's query before it was grouped, kept as the reference.
OLD_SQL = """select c.*, (select content from messages m where m.attempt_id=c.attempt_id and m.role in ('user','admin')
               order by id desc limit 1) as last_text,
               (select status from runs r where r.attempt_id=c.attempt_id order by created_at desc limit 1) as last_run_status,
               (select count(*) from runs r where r.attempt_id=c.attempt_id) as run_count,
               (select coalesce(sum(cost_estimate),0) from runs r where r.attempt_id=c.attempt_id) as cost,
               (select count(*) from runs r where r.attempt_id=c.attempt_id and r.status in ('queued','running')) as active_runs,
               (select count(*) from proofs p where p.attempt_id=c.attempt_id) as passes,
               (select r.command || ' ' || r.verdict from runs r where r.attempt_id=c.attempt_id and r.kind='review'
                and r.verdict is not null order by r.finished_at desc limit 1) as last_review
               from chats c order by c.admin_open desc, c.last_activity desc limit 500"""


class AdminChatsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, name, value in ((config, "DATA_DIR", Path(tmp.name)), (db, "_conn", None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        portal.bust_admin_chats()
        self.seed()

    def seed(self):
        now = time.time()
        for i, (attempt, admin_open) in enumerate((("a1", 0), ("a2", 1), ("a3", 0))):
            db.ex("insert into chats(attempt_id, task_id, user_id, email, admin_open, created_at, last_activity) "
                  "values (?,?,?,?,?,?,?)", (attempt, "t" + attempt, "u" + attempt, attempt + "@x.com", admin_open, now, now - i))
        db.add_message("a1", "user", "first " + "y" * 300)
        db.add_message("a1", "assistant", "bot reply")
        db.add_message("a1", "admin", "admin note")
        db.add_message("a1", "system", "system note")
        db.add_message("a2", "assistant", "only the bot")
        runs = [("r1", "a1", "review", "prompt", "PASS", "done", 1.5, now - 50, now - 40),
                ("r2", "a1", "review", "oe", "FAIL", "done", 2.25, now - 30, now - 20),
                ("r3", "a1", "followup", None, None, "running", None, now - 10, None),
                ("r4", "a2", "review", None, "PASS", "done", 0.5, now - 5, now - 1),
                ("r5", "a2", "review", "rubric", None, "queued", None, now - 2, None)]
        for r in runs:
            db.ex("insert into runs(id, attempt_id, kind, command, verdict, status, cost_estimate, created_at, finished_at) "
                  "values (?,?,?,?,?,?,?,?,?)", r)
        for code, attempt in (("p1", "a1"), ("p2", "a1"), ("p3", "a3")):
            db.ex("insert into proofs(code, run_id, attempt_id, payload, created_at) values (?,?,?,?,?)",
                  (code, "r1", attempt, "{}", now))

    def test_matches_old_query(self):
        old = db.q(OLD_SQL)
        for r in old:
            r["last_text"] = r["last_text"] and r["last_text"][:160]
        with db.reading():
            new = portal._chat_list_rows()
        self.assertEqual(old, new)
        self.assertEqual([r["attempt_id"] for r in new], ["a2", "a1", "a3"])

    def test_shared_build_and_bust(self):
        client = TestClient(portal.app)
        with mock.patch.object(portal, "admin_role", lambda email: "owner"):
            client.cookies.set(auth.ADMIN_COOKIE, auth.sign({"email": "atlasadmin", "iat": time.time()}))
            with mock.patch.object(portal, "_build_admin_chats", wraps=portal._build_admin_chats) as build:
                self.assertEqual(client.get("/api/admin/chats").status_code, 200)
                self.assertEqual(client.get("/api/admin/chats").status_code, 200)
                self.assertEqual(build.call_count, 1)
                client.post("/api/admin/chat/a2/resolve")
                body = client.get("/api/admin/chats").json()
                self.assertEqual(build.call_count, 2)
        self.assertFalse(next(c for c in body["chats"] if c["attempt_id"] == "a2")["admin_open"])


if __name__ == "__main__":
    unittest.main()
