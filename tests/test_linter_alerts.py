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


if __name__ == "__main__":
    unittest.main()
