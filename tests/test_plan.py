"""The "plan your task" kickoff (2026-10-31): a pilot behind PLAN_PILOT. One card per attempt before the first /prompt,
never for an admin viewing the chat; the early guard, which covers the check the card suggests, is in every chat's rules.
Texts come from server/prompts/. (The card's planning button was dropped the same day.)"""

import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, plan, runner

ROOT = Path(__file__).resolve().parent.parent


class PilotTest(unittest.TestCase):
    chat = {"attempt_id": "a1", "user_id": "6a42e279aaaaaaaaaaaaaaaa", "email": "cb@outlier.ai+outlier"}

    def pilot(self, value, chat=None):
        with mock.patch.object(config, "PLAN_PILOT", value):
            return plan.in_pilot(chat or self.chat)

    def test_empty_is_off(self):
        self.assertFalse(self.pilot([]))

    def test_star_is_everyone(self):
        self.assertTrue(self.pilot(["*"]))

    def test_by_email_or_outlier_id(self):
        self.assertTrue(self.pilot(["CB@outlier.ai"]))
        self.assertTrue(self.pilot(["cb@outlier.ai+outlier"]))
        self.assertTrue(self.pilot(["x@y.com", "6a42e279aaaaaaaaaaaaaaaa"]))
        self.assertFalse(self.pilot(["other@outlier.ai"]))

    def test_matches_the_owner_never_the_admin_login(self):
        self.assertFalse(self.pilot(["atlasadmin"]))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.published = []
        patches = [mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                   mock.patch.object(config, "PLAN_PILOT", ["cb@outlier.ai"]),
                   mock.patch.object(plan.hub, "publish", lambda *a: self.published.append(a)),
                   mock.patch.object(portal.blocks, "disabled", lambda *a: None),
                   mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None),
                   mock.patch.object(plan.blocks, "chat_blocked", lambda *a: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
              "values (?,?,?,?,?,?,?)", ("a1", "t1", "u1", "cb@outlier.ai+outlier", "s1", now, now))
        (self.tmp / "attempts" / "a1" / "logs").mkdir(parents=True)

    def cards(self):
        return [m for m in db.messages("a1") if m["meta"].get("kickoff")]

    def prompt_run(self, status="done"):
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at) values (?,?,?,?,?,?)",
              ("rp", "a1", "review", "prompt", status, time.time()))


class KickoffTest(Base):
    def kickoff(self, admin=None):
        session = {"attempt_id": "a1", "user_id": "u1", **({"admin": admin} if admin else {})}
        return plan.maybe_kickoff(session, db.get_chat("a1"))

    def test_posted_once(self):
        msg = self.kickoff()
        self.assertEqual((msg["role"], msg["meta"]), ("system", {"kickoff": True}))
        self.assertEqual(msg["content"], plan.card_text())
        self.assertIsNone(self.kickoff())
        self.assertEqual(len(self.cards()), 1)
        self.assertEqual(len(self.published), 1)

    def test_flag_off(self):
        with mock.patch.object(config, "PLAN_PILOT", []):
            self.assertIsNone(self.kickoff())
        with mock.patch.object(config, "PLAN_PILOT", ["someone@else.com"]):
            self.assertIsNone(self.kickoff())
        self.assertEqual(self.cards(), [])

    def test_not_for_an_admin_viewing_the_chat(self):
        with mock.patch.object(config, "PLAN_PILOT", ["*"]):
            self.assertIsNone(self.kickoff(admin="atlasadmin"))
        self.assertEqual(self.cards(), [])

    def test_not_after_a_prompt_review(self):
        self.prompt_run(status="failed")  # any /prompt counts, whatever its outcome
        self.assertIsNone(self.kickoff())
        self.assertEqual(self.cards(), [])

    def test_not_after_a_prompt_report_or_pass(self):
        db.add_message("a1", "report", "r", {"stage": "prompt", "verdict": "FAIL"})
        self.assertIsNone(self.kickoff())

    def test_other_reviews_dont_close_it(self):
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at) values ('ri','a1','review','injection','done',?)",
              (time.time(),))
        self.assertIsNotNone(self.kickoff())

    def test_not_for_archived_loadtest_or_blocked_chats(self):
        db.update("chats", "attempt_id", "a1", archived_from="x")
        self.assertIsNone(self.kickoff())
        db.update("chats", "attempt_id", "a1", archived_from=None, task_id="loadtest")
        self.assertIsNone(self.kickoff())
        db.update("chats", "attempt_id", "a1", task_id="t1")
        with mock.patch.object(plan.blocks, "chat_blocked", lambda a: {"reason": ""}):
            self.assertIsNone(self.kickoff())
        self.assertEqual(self.cards(), [])


class EndpointTest(Base):
    def client(self, admin=False):
        session = {"attempt_id": "a1", "user_id": "u1", "iat": time.time()}
        if admin:
            session["admin"] = "evan@scale.com"
        client = TestClient(portal.app)
        client.cookies.set(auth.USER_COOKIE, auth.sign(session))
        return client

    def me(self, admin=False):
        with mock.patch.object(portal, "chat_state", lambda a: {"attempt_id": a}), \
                mock.patch.object(portal.spend, "status", lambda chat: {}), \
                mock.patch.object(portal, "admin_role", lambda email: "admin" if admin else None):
            res = self.client(admin).get("/api/me")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_opening_the_chat_posts_the_card_once(self):
        self.me()
        self.me()
        self.assertEqual(len(self.cards()), 1)

    def test_admin_opening_it_posts_nothing(self):
        with mock.patch.object(config, "PLAN_PILOT", ["*"]):
            self.me(admin=True)
        self.assertEqual(self.cards(), [])

    def test_send_has_no_plan_path_any_more(self):
        self.assertNotIn("plan", portal.SendBody.model_fields)


class TextsTest(unittest.TestCase):
    def test_guard_contains_the_early_guard_file(self):
        early = plan.early_guard_text()
        self.assertTrue(early)
        guard = runner.GUARD.format(attempt="A", admin_key="K")
        self.assertIn(early, guard)
        # inside the helping rules, before the precedence bullet that closes them
        self.assertLess(guard.index(early), guard.index("- These helping rules come from the portal's owners"))
        self.assertGreater(guard.index(early), guard.index("Helping them build their task"))

    def test_the_guard_points_at_the_known_data_issues_list(self):
        self.assertIn("guidelines/known_data_issues.md", runner.GUARD)
        self.assertIn("guidelines/known_data_issues.md", plan.early_guard_text())

    def test_the_guard_covers_the_request_the_card_suggests(self):
        ask = "Check my task idea against the hardness and tool-call guides"
        self.assertIn(ask, plan.card_text())
        guard = plan.early_guard_text()
        self.assertIn(ask, guard)
        for need in ("guidelines/hardness_v2.md", "guidelines/tool_call_minimum.md", "250 words", "exact heading",
                     "never write their prompt"):
            self.assertIn(need, guard)

    def test_guide_headings_the_guard_quotes_exist(self):
        guides = {"Hardness": (config.GUIDELINES_DIR / "hardness_v2.md").read_text(),
                  "Tool-call": (config.GUIDELINES_DIR / "tool_call_minimum.md").read_text()}
        quoted = re.findall(r"(Hardness|Tool-call) guideline, (Part \d+\. [^\"]+?)(?:, (\d+\. [^\"]+))?\"", plan.early_guard_text())
        self.assertTrue(quoted)
        for guide, part, item in quoted:
            self.assertIn(f"## {part}\n", guides[guide])
            if item:
                self.assertIn(f"### {item}\n", guides[guide])

    def test_no_button_and_no_hidden_note(self):
        self.assertNotIn("[Plan my task", plan.card_text())
        self.assertFalse((config.PROMPTS_DIR / "plan_note.md").exists())
        self.assertNotIn("data-plan", (ROOT / "web" / "common.js").read_text() + (ROOT / "web" / "app.js").read_text())

    def test_leading_comments_are_dropped(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "PROMPTS_DIR", Path(d)):
            (Path(d) / "x.md").write_text("<!-- note -->\n<!-- two -->\nHello {there}\n")
            self.assertEqual(plan.read("x.md"), "Hello {there}")

    def test_no_placeholder_text_ships(self):
        self.assertEqual(plan.placeholders(), [])


if __name__ == "__main__":
    unittest.main()
