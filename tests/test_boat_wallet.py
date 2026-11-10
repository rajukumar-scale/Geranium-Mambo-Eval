"""Slack alert when boat can't start machines (low-wallet alerts dropped 2026-11-09) (2026-11-01: the plan ran out and every
/prompt and /rubric hung at "Starting a fresh review machine")."""

import asyncio
import json
import unittest
from unittest import mock

from server import boat_wallet

H = 3600


class BoatWalletTest(unittest.TestCase):
    def setUp(self):
        self.sent, self.limits = [], {"canStart": True, "wallet": {"balanceDollars": 95.0}}
        for p in (mock.patch.object(boat_wallet.notify, "slack", mock.AsyncMock(side_effect=lambda t: self.sent.append(t))),
                  mock.patch.object(boat_wallet.boatrun, "cli", mock.AsyncMock(side_effect=lambda *a, **k: (0, json.dumps(self.limits), ""))),
                  mock.patch.dict(boat_wallet._state, {"low_since": None, "alerted": 0.0, "samples": []}),
                  mock.patch.object(boat_wallet.config, "BOAT_WALLET_ALERT_USD", 20.0)):
            p.start()
            self.addCleanup(p.stop)

    def at(self, t, wallet, can_start=True):
        self.limits = {"canStart": can_start, "wallet": {"balanceDollars": wallet}, "blockedReason": None if can_start else "usage_depleted"}
        return asyncio.run(boat_wallet.check(1_000_000 + t))

    def test_a_low_wallet_alone_never_alerts(self):
        """2026-11-09 (Evan: "stop saying this in slack pls"): no "running low" alerts any more."""
        for i, w in enumerate((40, 18, 8, 2, 0.5)):
            self.at(i * 7 * H, w)
        self.assertEqual(self.sent, [])

    def test_blocked_alerts_every_6_hours_then_recovery(self):
        self.at(0, 5, can_start=False)
        self.assertEqual(len(self.sent), 1)
        self.at(3 * H, 5, can_start=False)
        self.assertEqual(len(self.sent), 1)  # not again within 6 h
        self.at(6.5 * H, 5, can_start=False)
        self.assertEqual(len(self.sent), 2)
        self.at(7 * H, 120)
        self.assertIn("can start review machines again", self.sent[-1])
        self.assertNotIn("$", self.sent[-1])

    def test_blocked_boat_alerts_even_with_money(self):
        self.at(0, 50, can_start=False)
        self.assertIn("can't start review machines", self.sent[0])
        self.assertIn("usage_depleted", self.sent[0])

    def test_burn_rate_ignores_top_ups(self):
        self.assertAlmostEqual(boat_wallet.burn_per_hour([(0, 30), (H, 20), (1.5 * H, 100), (2 * H, 95)]), 7.5)
        self.assertIsNone(boat_wallet.burn_per_hour([(0, 30)]))


if __name__ == "__main__":
    unittest.main()
