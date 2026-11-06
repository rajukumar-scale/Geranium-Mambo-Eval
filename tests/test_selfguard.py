"""The portal keeps its own boat box from auto-stopping (2026-11-05: boat stopped it twice, the second time an hour
after a dashboard resume gave it a lifetime)."""

import asyncio
import json
import unittest
from unittest import mock

from server import boatrun, config, selfguard


class SelfGuardTest(unittest.TestCase):
    def run_check(self, sandbox, extend_rc=0):
        calls, alerts = [], []

        async def cli(*args, timeout=300, input_bytes=None):
            calls.append(args)
            if args[0] == "info":
                return 0, json.dumps({"sandbox": sandbox}), ""
            return extend_rc, "{}", "" if extend_rc == 0 else "denied"

        async def slack(text):
            alerts.append(text)
            return True
        with mock.patch.object(config, "REVIEW_RUNNER", "boat"), mock.patch.object(config, "SERVER_BOX_ID", "bx_test"), \
                mock.patch.object(boatrun, "cli", cli), mock.patch.object(selfguard.notify, "slack", slack):
            return asyncio.run(selfguard.check()), calls, alerts

    def test_no_auto_stop_needs_nothing(self):
        out, calls, alerts = self.run_check({"ttlSeconds": None, "archiveAfter": None})
        self.assertEqual(out, {"ok": True})
        self.assertEqual([c[0] for c in calls], ["info"])
        self.assertEqual(alerts, [])

    def test_a_lifetime_is_turned_off_and_reported(self):
        out, calls, alerts = self.run_check({"ttlSeconds": 3500, "archiveAfter": None})
        self.assertEqual(out, {"fixed": True, "ttl": 3500})
        self.assertEqual(calls[1][:3], ("extend", "bx_test", "--no-auto-stop"))
        self.assertIn("turned it off", alerts[0])

    def test_a_failed_turn_off_asks_a_person(self):
        out, _, alerts = self.run_check({"ttlSeconds": 600}, extend_rc=1)
        self.assertEqual(out["error"], "extend")
        self.assertIn("boat extend bx_test --no-auto-stop", alerts[0])

    def test_off_when_reviews_dont_run_on_boat(self):
        with mock.patch.object(config, "REVIEW_RUNNER", "docker"):
            self.assertEqual(asyncio.run(selfguard.check()), {"skipped": True})


if __name__ == "__main__":
    unittest.main()
