"""The guide pop-up (Evan, 2026-11-06): before the first /prompt, a contributor's chat opens with "read these 3 guides
first, it's really important". A guide counts as read when its tab loads; "I've read all three" saves only after all
three were opened; until then it comes back every time the chat opens. Never for an admin viewing the chat."""

import time
import unittest
from unittest import mock

from server import app as portal, config, db, plan
from tests.test_plan import ROOT, Base, EndpointTest


class GuideIntroTest(EndpointTest):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(config, "GUIDELINES_DIR", self.tmp / "guides")
        p.start()
        self.addCleanup(p.stop)
        (self.tmp / "guides").mkdir()
        for file in portal.GUIDES.values():
            (self.tmp / "guides" / file).write_text("# guide\n")

    def intro(self, admin=False):
        return self.me(admin)["guide_intro"]

    def read(self, name, admin=False):
        with mock.patch.object(portal, "admin_role", lambda email: "admin" if admin else None):
            self.assertEqual(self.client(admin).get(f"/api/guides/{name}").status_code, 200)

    def test_shown_on_open_with_the_three_guides(self):
        intro = self.intro()
        self.assertIn("really important", intro["text"])
        self.assertEqual([(g["key"], g["read"]) for g in intro["guides"]],
                         [("hardness", False), ("toolcalls", False), ("patterns", False)])
        self.assertEqual(self.intro(), intro)  # comes back on the next open until acknowledged

    def test_opening_a_guide_ticks_it(self):
        self.read("hardness")
        self.read("known")  # not one of the three
        self.assertEqual([g["read"] for g in self.intro()["guides"]], [True, False, False])
        self.assertEqual(plan.guides_read("a1"), {"hardness"})

    def test_ack_needs_all_three(self):
        self.read("hardness")
        res = self.client().post("/api/guides/ack")
        self.assertEqual(res.status_code, 409)
        self.assertIn("Tool calls, Failure patterns", res.json()["detail"])
        self.read("toolcalls")
        self.read("patterns")
        self.assertEqual(self.client().post("/api/guides/ack").status_code, 200)
        self.assertIsNone(self.intro())

    def test_not_for_admins_and_admin_reads_dont_count(self):
        with mock.patch.object(config, "PLAN_PILOT", ["*"]):
            self.assertIsNone(self.intro(admin=True))
            self.read("hardness", admin=True)
        self.assertEqual(plan.guides_read("a1"), set())

    def test_not_once_a_prompt_review_started(self):
        self.prompt_run(status="failed")
        self.assertIsNone(self.intro())

    def test_not_outside_the_pilot(self):
        with mock.patch.object(config, "PLAN_PILOT", []):
            self.assertIsNone(self.intro())

    def test_the_page_shows_it(self):
        js = (ROOT / "web" / "app.js").read_text()
        self.assertIn("showGuideIntro();", js)
        self.assertIn('"/api/guides/ack"', js)


if __name__ == "__main__":
    unittest.main()
