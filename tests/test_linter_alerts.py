"""The stuck-CB DM lists each stuck CB once, with the dashboard link."""

import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
class MessageTest(unittest.TestCase):
    def test_message(self):
        from server import config, linter_alerts
        rows = [{"STAGE": "Rubric", "TASK_ID": "6ad83eddd81b65c3e82bdc29", "WORKER_ID": "w1", "EMAIL": "a@outlier.ai",
                 "FAILS_SINCE_PASS": 10, "MINUTES_SINCE_FIRST_FAIL": 83},
                {"STAGE": "Prompt", "TASK_ID": "t2", "WORKER_ID": "w2", "EMAIL": None,
                 "FAILS_SINCE_PASS": 3, "MINUTES_SINCE_FIRST_FAIL": 48}]
        text = linter_alerts.message(rows)
        self.assertIn("2 CBs stuck", text)
        self.assertIn("• Rubric · a@outlier.ai · task 6ad83eddd81b65c3e82bdc29 · 10 fails over 83 min", text)
        self.assertIn("• Prompt · w2 · task t2", text)
        self.assertTrue(text.endswith(config.LINTER_ALERT_DASHBOARD_URL))
        self.assertIn("1 CB stuck", linter_alerts.message(rows[:1]))

    def test_keys_are_per_cb_task_stage(self):
        from server import linter_alerts
        a = {"STAGE": "Rubric", "TASK_ID": "t", "WORKER_ID": "w"}
        self.assertNotEqual(linter_alerts._key(a), linter_alerts._key({**a, "STAGE": "Prompt"}))



@unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
class DmWebhookTest(unittest.TestCase):
    """2026-11-06: no bot token, so the DM goes through a Slack workflow webhook that DMs Evan."""

    def send(self, token, hook, status=200):
        import asyncio
        from unittest import mock
        from server import config, linter_alerts
        posted = []

        class Client:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, **kw):
                posted.append((url, kw.get("json")))
                return mock.Mock(status_code=status, text="", json=lambda: {"ok": True})
        with mock.patch.object(config, "SLACK_BOT_TOKEN", token), mock.patch.object(config, "SLACK_DM_WEBHOOK_URL", hook), \
                mock.patch.object(linter_alerts.httpx, "AsyncClient", Client):
            return asyncio.run(linter_alerts.dm("hi")), posted, linter_alerts.dm_configured()

    def test_webhook_when_there_is_no_bot_token(self):
        self.assertEqual(self.send("", "https://hooks.example/dm"), (True, [("https://hooks.example/dm", {"text": "hi"})], True))

    def test_the_bot_token_wins(self):
        ok, posted, _ = self.send("xoxb-1", "https://hooks.example/dm")
        self.assertEqual(posted[0][0], "https://slack.com/api/chat.postMessage")

    def test_neither_is_off(self):
        self.assertEqual(self.send("", ""), (False, [], False))

    def test_a_failed_webhook_is_false(self):
        self.assertFalse(self.send("", "https://hooks.example/dm", status=500)[0])

if __name__ == "__main__":
    unittest.main()
