"""Tests for Sonorra known_issues.json, known_data_issues.md, and /api/known-issues."""

import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, known_issues

REQUIRED = ("id", "universe", "level", "fails", "title", "dont", "why")
ROOT = Path(__file__).resolve().parent.parent


class DataFileTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(known_issues.PATH.read_text())
        self.issues = self.data["issues"]

    def test_verified_date(self):
        self.assertRegex(self.data["verified"], r"^\d{4}-\d{2}-\d{2}$")

    def test_unique_ids(self):
        ids = [i["id"] for i in self.issues]
        self.assertEqual(len(ids), len(set(ids)))

    def test_required_fields_and_values(self):
        self.assertGreaterEqual(len(self.issues), 20)
        for i in self.issues:
            with self.subTest(issue=i.get("id")):
                for key in REQUIRED:
                    self.assertIn(key, i)
                for key in ("title", "dont", "why"):
                    self.assertIsInstance(i[key], str)
                    self.assertTrue(i[key].strip())
                self.assertEqual(i["universe"], "sonorra")
                self.assertIn(i["level"], ("avoid", "watch"))
                self.assertIsInstance(i["fails"], int)
                self.assertGreaterEqual(i["fails"], 0)
                self.assertTrue(i.get("safer", "x").strip())
                self.assertRegex(i["id"], r"^(?:K-\d+|V12-C\d+)$")


class MarkdownTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(known_issues.PATH.read_text())
        self.md = (config.GUIDELINES_DIR / "known_data_issues.md").read_text()

    def test_same_ids_in_the_same_order(self):
        ids = re.findall(r"^### (K-\d+|V12-C\d+) — ", self.md, re.M)
        self.assertEqual(ids, [i["id"] for i in self.data["issues"]])
        self.assertIn(self.data["verified"], self.md)


class EndpointTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", "t1", "u1", "cb@outlier.ai+outlier", now, now))

    def get(self, pilot):
        from fastapi.testclient import TestClient
        from server import app as portal, auth
        with mock.patch.object(config, "KNOWN_ISSUES_PILOT", pilot), \
                mock.patch.object(portal.blocks, "disabled", lambda *a: None):
            client = TestClient(portal.app)
            client.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "a1", "user_id": "u1", "iat": time.time()}))
            res = client.get("/api/known-issues")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_disabled_outside_the_pilot(self):
        self.assertEqual(self.get([]), {"enabled": False})
        self.assertEqual(self.get(["other@outlier.ai"]), {"enabled": False})

    def test_enabled_returns_sonorra(self):
        res = self.get(["cb@outlier.ai"])
        self.assertTrue(res["enabled"])
        self.assertEqual(res["universe"], "sonorra")
        self.assertEqual([g["universe"] for g in res["groups"]], ["sonorra"])
        self.assertGreaterEqual(len(res["groups"][0]["issues"]), 20)

    def test_signed_out(self):
        from fastapi.testclient import TestClient
        from server import app as portal
        self.assertEqual(TestClient(portal.app).get("/api/known-issues").status_code, 401)


if __name__ == "__main__":
    unittest.main()
