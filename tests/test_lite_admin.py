"""Lite admins (Evan, 2026-11-07): only chats with an open /admin request, nothing else. Allowlist: every admin endpoint
refuses them unless it opts in (current_admin(lite=True)) and checks the chat (lite_chat / lite_run)."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db

LITE, FULL = "helper@scale.com", "full@scale.com"


class LiteAdminTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, name, value in ((config, "DATA_DIR", Path(tmp.name)), (db, "_conn", None),
                                    (config, "ADMIN_EMAILS", ["owner@scale.com"])):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name in ("disabled", "chat_blocked"):
            p = mock.patch.object(portal.blocks, name, lambda *a: None)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        portal.bust_admin_chats()
        now = time.time()
        for attempt, admin_open in (("open1", 1), ("closed1", 0)):
            db.ex("insert into chats(attempt_id, task_id, user_id, email, admin_open, created_at, last_activity) "
                  "values (?,?,?,?,?,?,?)", (attempt, "t-" + attempt, "u-" + attempt, attempt + "@x.com", admin_open, now, now))
            db.ex("insert into runs(id, attempt_id, kind, command, status, created_at) values (?,?,?,?,?,?)",
                  ("run-" + attempt, attempt, "review", "prompt", "done", now))
        pw = auth.hash_password("pw-123456789")
        for email, role in ((LITE, "lite"), (FULL, "admin")):
            db.ex("insert into admins(email, role, password_hash, created_at) values (?,?,?,?)", (email, role, pw, now))

    def admin(self, email):
        c = TestClient(portal.app)
        c.cookies.set(auth.ADMIN_COOKIE, auth.sign({"email": email, "iat": time.time()}))
        return c

    def chat_page(self, email, attempt):
        c = TestClient(portal.app)
        c.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": attempt, "user_id": "", "admin": email, "iat": time.time()}))
        return c

    def test_roles(self):
        self.assertEqual(portal.admin_role(LITE), "lite")
        self.assertEqual(portal.admin_role(FULL), "admin")
        self.assertEqual(portal.admin_role("owner@scale.com"), "owner")

    def test_the_list_is_only_open_requests(self):
        res = self.admin(LITE).get("/api/admin/chats").json()
        self.assertTrue(res["lite"])
        self.assertEqual([c["attempt_id"] for c in res["chats"]], ["open1"])
        self.assertNotIn("stats_24h", res)
        full = self.admin(FULL).get("/api/admin/chats").json()
        self.assertEqual(sorted(c["attempt_id"] for c in full["chats"]), ["closed1", "open1"])

    def test_open_chat_yes_other_chat_no(self):
        c = self.admin(LITE)
        with mock.patch.object(portal.workspace, "list_reports", lambda a: []), \
                mock.patch.object(portal.workspace, "saved_inputs", lambda a: []):
            self.assertEqual(c.get("/api/admin/chat/open1").status_code, 200)
        self.assertEqual(c.get("/api/admin/chat/closed1").status_code, 403)
        self.assertEqual(c.post("/api/admin/chat/closed1/reply", json={"text": "hi"}).status_code, 403)
        self.assertEqual(c.post("/api/admin/chat/closed1/resolve").status_code, 403)
        self.assertEqual(c.get("/api/admin/runs/run-closed1/log").status_code, 403)
        self.assertEqual(c.post("/api/admin/runs/run-closed1/cancel").status_code, 403)
        self.assertEqual(c.get("/api/admin/reports/closed1/1_prompt.md").status_code, 403)
        self.assertEqual(c.post("/api/admin/open-chat", json={"attempt_id": "closed1"}).status_code, 403)
        self.assertEqual(c.post("/api/admin/open-chat", json={}).status_code, 403)  # "the latest chat" isn't theirs

    def test_reply_then_resolve_ends_access(self):
        c = self.admin(LITE)
        self.assertEqual(c.post("/api/admin/chat/open1/reply", json={"text": "on it"}).status_code, 200)
        self.assertEqual(c.post("/api/admin/chat/open1/resolve").status_code, 200)
        self.assertEqual(c.post("/api/admin/chat/open1/reply", json={"text": "again"}).status_code, 403)

    def test_everything_else_is_refused(self):
        c = self.admin(LITE)
        for method, url, body in (("get", "/api/admin/spend", None), ("get", "/api/admin/blocks", None),
                                  ("get", "/api/admin/broadcasts", None), ("get", "/api/admin/evalfiles", None),
                                  ("get", "/api/admin/budget", None), ("get", "/api/admin/cbs", None),
                                  ("get", "/api/admin/rubric-done", None), ("get", "/api/admin/status/incidents", None),
                                  ("post", "/api/admin/chat/open1/override-pass", {"run_id": "run-open1", "stage": "prompt", "reason": "x"}),
                                  ("post", "/api/admin/chat/open1/budget", {"add_usd": 100}),
                                  ("post", "/api/admin/block", {"kind": "attempt", "attempt_id": "open1"}),
                                  ("get", "/api/admin/admins", None)):
            res = getattr(c, method)(url, **({"json": body} if body is not None else {}))
            self.assertEqual(res.status_code, 403, url)
            self.assertTrue(res.json()["detail"].startswith(("Lite admins", "Only the owner")), url)

    def test_chat_page_follows_the_request(self):
        with mock.patch.object(portal.workspace, "list_reports", lambda a: []), \
                mock.patch.object(portal.workspace, "saved_inputs", lambda a: []), \
                mock.patch.object(portal.spend, "status", lambda chat: {}):
            self.assertEqual(self.chat_page(LITE, "open1").get("/api/me").status_code, 200)
            self.assertEqual(self.chat_page(LITE, "closed1").get("/api/me").status_code, 403)
            hist = [r["attempt_id"] for r in self.chat_page(LITE, "open1").get("/api/history").json()]
            self.assertEqual(hist, ["open1"])
            self.assertEqual(self.chat_page(LITE, "open1").post("/api/switch", json={"attempt_id": "closed1"}).status_code, 403)
            db.update("chats", "attempt_id", "open1", admin_open=0)
            self.assertEqual(self.chat_page(LITE, "open1").get("/api/me").status_code, 403)

    def test_login_bypass_only_into_open_requests(self):
        with mock.patch.object(portal.auth, "redash_attempt", mock.AsyncMock(return_value=[])):
            c = TestClient(portal.app)
            res = c.post("/api/login", json={"identifier": LITE, "attempt_id": "c" * 24, "password": "pw-123456789"})
        self.assertEqual(res.status_code, 403)

    def test_invite_lite_and_reset_keeps_the_role(self):
        owner = self.admin("owner@scale.com")
        self.assertEqual(owner.post("/api/admin/admins", json={"email": "new@scale.com", "role": "lite"}).status_code, 200)
        self.assertEqual(db.one("select role from admins where email='new@scale.com'")["role"], "lite")
        self.assertEqual(owner.post("/api/admin/admins", json={"email": "new@scale.com"}).status_code, 200)  # reset link
        self.assertEqual(db.one("select role from admins where email='new@scale.com'")["role"], "lite")
        self.assertEqual(owner.post("/api/admin/admins", json={"email": "x@scale.com", "role": "boss"}).status_code, 400)

    def test_streams_drop_other_chats(self):
        src = (Path(__file__).resolve().parent.parent / "server" / "app.py").read_text()
        self.assertIn("view=lambda p: p if p.get(\"attempt_id\") is None or lite_can_open(p[\"attempt_id\"]) else None", src)
        self.assertIn("if payload is not None:", src)
        self.assertTrue(portal.lite_can_open("open1"))
        self.assertFalse(portal.lite_can_open("closed1"))
        self.assertFalse(portal.lite_can_open("nope"))


if __name__ == "__main__":
    unittest.main()
