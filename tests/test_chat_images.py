"""Images in admin replies (2026-11-03): an admin pastes or attaches a screenshot, the contributor sees it in the
message. Only real images are stored (checked by their bytes, no SVG); only admins, the contributor of that chat and the
previous holder of a re-served chat may open them; the assistant reads "[image attached by the admin]"."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, runner

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class ChatImagesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, name, value in ((config, "DATA_DIR", Path(tmp.name)), (db, "_conn", None),
                                    (portal, "admin_role", lambda email: "owner" if email == "atlasadmin" else None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        for attempt, user in (("a1", "u1"), ("a2", "u2")):
            db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
                  "values (?,?,?,?,?,?,?)", (attempt, "t" + attempt, user, f"{user}@x.com", "s", now, now))

    def client(self, admin=False, attempt=None, user=None):
        c = TestClient(portal.app)
        if admin:
            c.cookies.set(auth.ADMIN_COOKIE, auth.sign({"email": "atlasadmin", "iat": time.time()}))
        if attempt:
            c.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": attempt, "user_id": user, "iat": time.time()}))
        return c

    def upload(self, data=PNG, name="shot.png", attempt="a1"):
        return self.client(admin=True).post(f"/api/admin/chat/{attempt}/image", files={"file": (name, data, "image/png")})

    def test_an_admin_uploads_a_screenshot_and_the_chat_owner_sees_it(self):
        r = self.upload()
        self.assertEqual(r.status_code, 200)
        url = r.json()["url"]
        self.assertTrue(url.startswith("/api/chat-image/a1/") and url.endswith(".png"))
        self.assertEqual(r.json()["markdown"], f"![image]({url})")
        own = self.client(attempt="a1", user="u1").get(url)
        self.assertEqual((own.status_code, own.content, own.headers["content-type"]), (200, PNG, "image/png"))
        self.assertEqual(own.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.client(admin=True).get(url).status_code, 200)

    def test_other_contributors_and_strangers_cannot_open_it(self):
        url = self.upload().json()["url"]
        self.assertEqual(self.client(attempt="a2", user="u2").get(url).status_code, 403)
        self.assertEqual(self.client().get(url).status_code, 403)

    def test_the_previous_holder_still_sees_it_in_their_archived_chat(self):
        url = self.upload().json()["url"]
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity, archived_from) "
              "values ('0000arch','ta1','u9','u9@x.com','s',0,0,'a1')")
        self.assertEqual(self.client(attempt="0000arch", user="u9").get(url).status_code, 200)

    def test_only_real_images_are_accepted(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertEqual(self.upload(svg, "x.svg").status_code, 415)
        self.assertEqual(self.upload(b"<html>hi</html>", "fake.png").status_code, 415)  # a .png name isn't enough
        self.assertEqual(self.upload(b"\xff\xd8\xff" + b"0" * 10, "a.jpg").json()["url"][-4:], ".jpg")
        with mock.patch.object(portal, "CHAT_IMAGE_MAX", 10):
            self.assertEqual(self.upload().status_code, 413)
        self.assertEqual(self.client().post("/api/admin/chat/a1/image", files={"file": ("s.png", PNG, "image/png")}).status_code, 401)
        self.assertEqual(self.upload(attempt="nope").status_code, 404)

    def test_bad_names_never_reach_the_disk(self):
        c = self.client(admin=True)
        for path in ("/api/chat-image/a1/..%2F..%2Fportal.db", "/api/chat-image/a1/abc.png", "/api/chat-image/a1/" + "a" * 32 + ".svg"):
            self.assertEqual(c.get(path).status_code, 404, path)

    def test_the_assistant_reads_a_placeholder_not_the_link(self):
        url = self.upload().json()["url"]
        row = {"content": f"Look at this part:\n\n![image]({url})", "meta": json.dumps({"admin": "atlasadmin"}), "created_at": time.time()}
        line = runner._admin_line(row, "k")
        self.assertIn("[image attached by the admin]", line)
        self.assertNotIn("/api/chat-image/", line)


if __name__ == "__main__":
    unittest.main()
