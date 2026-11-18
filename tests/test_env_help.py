"""Sandbox-trouble /admin requests get the fix steps right away (Evan, 2026-11-04): hard refresh, Settings → Redeploy
Environment, the current injection SQL once, then /admin with the error. The admins are still told."""

import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, env_help

WEB = Path(__file__).resolve().parent.parent / "web"

# Real requests from Oct 13 – Nov 4 (shortened).
TROUBLE = [
    'Run Agent fails every time with "Run failed while snapshotting the universe: Snapshot failed (502)"',
    "Both Opus 5.5 and Haiku fail with \"Snapshot failed (502)\" before starting. RUN remains 0.",
    "agent run stuck at 2/8",
    "My task prompt passed, but the Opus 5.5 pre-check is stuck at step 2/8 (\"Loading data\").",
    "im waiting for more than 10 mins, is this normal? Run #1 'Waiting for the agent's first steps…'",
    "Your browser can't reach the environment's host, but the viewer's server can.",
    "I am not able to deploy the new environment and I got a 502 error.",
    "My environment redeploy has been stuck on \"Deploying environment & agent…\" for over 20 minutes.",
    "The universe is giving me an internal server error.",
    "btw agents are erroring out I've been trying since 2 hours.",
    "Opus runs don't include my injected Gmail email (3 runs, Jan 9 2025 email from Douglas missing).",
    "Verifier/baseline runs for my task used an old injection.",
    "Rubric Verifier fails on all 6 baseline runs: \"No artifact recorded by step 'task-snapshot-…-snapshot-env'\"",
]
NOT_TROUBLE = [
    "unable to use /prompt syntax says internal server error !!",
    "This run didn't finish ([Errno 28] No space left on device).",
    "prompt review fails with Internal Server Error and \"run didn't finish\".",
    "Requesting a budget increase (+$100); /rubric is the only thing left.",
    "The Outlier task rejects my pass screen: fingerprint mismatch.",
    "Unique Ground Truth went 5→2, I disagree with the verdict.",
    "will the hours I already put in be paid?",
]


class DetectTest(unittest.TestCase):
    def test_sandbox_trouble_is_caught(self):
        for text in TROUBLE:
            self.assertTrue(env_help.is_sandbox_trouble(text), text)

    def test_other_requests_and_our_own_portal_errors_are_not(self):
        for text in NOT_TROUBLE:
            self.assertFalse(env_help.is_sandbox_trouble(text), text)


class ReplyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.object(portal.blocks, "disabled", lambda *a: None),
                  mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None),
                  mock.patch.object(portal.notify, "slack", mock.AsyncMock(return_value=True))):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
              "values ('a1','t1','u1','a1@x.com','s1',?,?)", (now, now))

    def send(self, text):
        c = TestClient(portal.app)
        c.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "a1", "user_id": "u1", "iat": time.time()}))
        self.assertEqual(c.post("/api/send", json={"text": text}).status_code, 200)
        return db.q("select content, meta from messages where attempt_id='a1' and role='system' order by id desc limit 1")[0]

    def test_a_sandbox_request_gets_the_steps_and_the_admins_are_still_told(self):
        reply = self.send("/admin Opus run fails with Snapshot failed (502) twice")
        self.assertIn('"env_guide": true', reply["meta"])
        self.assertIn("Redeploy Environment", reply["content"])
        self.assertIn("admins have been notified", reply["content"])
        self.assertEqual(db.q("select count(*) n from admin_requests where status='open'")[0]["n"], 1)

    def test_other_requests_get_the_plain_reply(self):
        reply = self.send("/admin I need more budget for one last /rubric")
        self.assertNotIn("env_guide", reply["meta"])
        self.assertNotIn("Redeploy", reply["content"])


class GuideTest(unittest.TestCase):
    def test_the_guide_has_the_steps_in_order(self):
        js = (WEB / "common.js").read_text()
        guide = js[js.index("function envGuideHtml"):js.index("function showEnvGuide")]
        order = [guide.index(w) for w in ("Hard refresh", "Redeploy Environment", "Run your injection SQL again", "/admin")]
        self.assertEqual(order, sorted(order))
        for keys in ('"⌘ Cmd", "⇧ Shift", "R"', '"Ctrl", "Shift", "R"', '"Ctrl", "F5"'):
            self.assertIn(keys, guide)
        for name in re.findall(r"/static/img/([\w.]+\.png)", guide):
            self.assertTrue((WEB / "img" / name).is_file(), name)

    def test_the_reply_opens_it_and_has_a_button(self):
        js, app = (WEB / "common.js").read_text(), (WEB / "app.js").read_text()
        self.assertIn("data-envguide", js)
        self.assertIn("m.meta.env_guide && !state.me.admin", app)


if __name__ == "__main__":
    unittest.main()


class RedeployFromSnapshotTest(unittest.TestCase):
    """2026-11-06: a redeploy from a deleted snapshot. The guide now says to redeploy from the base universe."""

    def test_the_not_found_error_and_failed_redeploys_open_the_guide(self):
        for text in ("Artifact snapshot-hg4-with-gsuite-bundle-1174-e51786a0 version=16 not found",
                     "my environment failed to redeploy"):
            self.assertTrue(env_help.is_sandbox_trouble(text), text)

    def test_the_guides_say_base_universe_not_snapshot(self):
        js = (Path(__file__).resolve().parent.parent / "web" / "common.js").read_text()
        self.assertEqual(js.count("${REDEPLOY_BASE}"), 2)
        self.assertNotIn("keep <b>Universe this environment was deployed with</b> selected", js)
        for base in ("long_horizon_tech_35k_scoped", "Sonorra v17"):
            self.assertIn(base, js)
