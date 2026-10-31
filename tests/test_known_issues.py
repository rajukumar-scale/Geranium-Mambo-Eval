"""The "Known data issues" sidebar (2026-10-30): a pilot behind KNOWN_ISSUES_PILOT, showing the task's universe's list
(both, grouped, until the date is picked), from server/known_issues.json."""

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, known_issues, workspace

REQUIRED = ("id", "universe", "level", "fails", "title", "dont", "why")
ROOT = Path(__file__).resolve().parent.parent


def converter():
    spec = importlib.util.spec_from_file_location("build_known_issues", ROOT / "deploy" / "build_known_issues.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        for i in self.issues:
            with self.subTest(issue=i.get("id")):
                for key in REQUIRED:
                    self.assertIn(key, i)
                for key in ("title", "dont", "why"):
                    self.assertIsInstance(i[key], str)
                    self.assertTrue(i[key].strip())
                self.assertIn(i["universe"], known_issues.UNIVERSES)
                self.assertIn(i["level"], ("avoid", "watch"))
                self.assertIsInstance(i["fails"], int)
                self.assertGreaterEqual(i["fails"], 0)
                self.assertLessEqual(len(i["title"].split()), 8)
                self.assertTrue(i.get("safer", "x").strip())  # omitted, never empty
                self.assertRegex(i["id"], r"^SC-\d+$" if i["universe"] == "synthcut" else r"^NB-\d+$")
                self.assertLessEqual(set(i), set(REQUIRED) | {"safer"})

    def test_both_universes_most_fails_first(self):
        for u in known_issues.UNIVERSES:
            fails = [i["fails"] for i in self.issues if i["universe"] == u]
            self.assertTrue(fails, u)
            self.assertEqual(fails, sorted(fails, reverse=True), u)

    def test_universe_keys_match_the_task_dates(self):
        self.assertEqual(set(known_issues.UNIVERSES), set(workspace.UNIVERSE_DATES))

    def test_soft_issues_stay_avoid(self):
        """Soft issues are still traps (2026-10-30): SC-4 is soft but fully verified, NB-8 / NB-27 are soft only."""
        level = {i["id"]: i["level"] for i in self.issues}
        self.assertEqual([level["SC-4"], level["NB-8"], level["NB-27"]], ["avoid"] * 3)
        self.assertEqual([level["SC-7"], level["NB-30"]], ["watch"] * 2)  # partial; single report

    def test_no_internal_jargon(self):
        text = json.dumps(self.issues)
        for word in ("Phase 3.2", "Eval 1", "run_id", "FD-"):
            self.assertNotIn(word, text)


class WatchRuleTest(unittest.TestCase):
    def test_watch_is_partial_or_single_report_only(self):
        watch_level = converter().watch_level
        self.assertEqual(watch_level(partial=False, reports=40), "avoid")
        self.assertEqual(watch_level(partial=True, reports=40), "watch")
        self.assertEqual(watch_level(partial=False, reports=1), "watch")


class PilotTest(unittest.TestCase):
    chat = {"user_id": "6a42e279aaaaaaaaaaaaaaaa", "email": "cb@outlier.ai+outlier"}

    def pilot(self, value, session=None):
        with mock.patch.object(config, "KNOWN_ISSUES_PILOT", value):
            return known_issues.in_pilot(session or {"user_id": self.chat["user_id"]}, self.chat)

    def test_empty_is_off(self):
        self.assertFalse(self.pilot([]))

    def test_star_is_everyone(self):
        self.assertTrue(self.pilot(["*"]))

    def test_by_email_without_the_suffix_and_any_case(self):
        self.assertTrue(self.pilot(["CB@outlier.ai"]))
        self.assertTrue(self.pilot(["cb@outlier.ai+outlier"]))
        self.assertFalse(self.pilot(["other@outlier.ai"]))

    def test_by_outlier_id(self):
        self.assertTrue(self.pilot(["someone@x.com", "6a42e279aaaaaaaaaaaaaaaa"]))

    def test_by_admin_login(self):
        self.assertTrue(self.pilot(["atlasadmin"], {"admin": "atlasadmin", "user_id": ""}))


class ViewTest(unittest.TestCase):
    def test_one_universe(self):
        for u in known_issues.UNIVERSES:
            v = known_issues.view(u)
            self.assertEqual((v["enabled"], v["universe"], [g["universe"] for g in v["groups"]]), (True, u, [u]))
            self.assertTrue(all(i["universe"] == u for i in v["groups"][0]["issues"]))
            self.assertEqual(v["groups"][0]["label"], workspace.UNIVERSE_DATES[u][1])
            self.assertEqual(v["groups"][0]["date"], workspace.UNIVERSE_DATES[u][0])  # the header shows the task date

    def test_no_date_yet_gives_both_grouped(self):
        v = known_issues.view(None)
        self.assertIsNone(v["universe"])
        self.assertEqual([g["universe"] for g in v["groups"]], list(known_issues.UNIVERSES))
        total = sum(len(g["issues"]) for g in v["groups"])
        self.assertEqual(total, len(known_issues.data()["issues"]))


class EndpointTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.universe = None
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(workspace, "universe_date", lambda a: self.universe)]
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

    def test_enabled_follows_the_task_date(self):
        self.universe = "normal"
        res = self.get(["cb@outlier.ai"])
        self.assertTrue(res["enabled"])
        self.assertEqual([g["universe"] for g in res["groups"]], ["normal"])
        self.universe = "synthcut"
        self.assertEqual([g["universe"] for g in self.get(["u1"])["groups"]], ["synthcut"])
        self.universe = None
        self.assertEqual(len(self.get(["*"])["groups"]), 2)

    def test_signed_out(self):
        from fastapi.testclient import TestClient
        from server import app as portal
        self.assertEqual(TestClient(portal.app).get("/api/known-issues").status_code, 401)


if __name__ == "__main__":
    unittest.main()
