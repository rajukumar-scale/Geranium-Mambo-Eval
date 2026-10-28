"""Cost page history: days since Aug 1 bucketed into UTC weeks (Monday start) and calendar months add up."""

import calendar
import unittest
from unittest import mock

from server import cost

DAY = 86400


def utc(y, m, d):
    return calendar.timegm((y, m, d, 0, 0, 0))


class HistoryTest(unittest.TestCase):
    def build(self, rows, now, portal_day=None, portal_people=None):
        portal = (portal_day or {}, {}, {}, portal_people or {}, {})
        with mock.patch.object(cost, "_portal", lambda start, billed: portal), mock.patch.object(cost.time, "time", lambda: now):
            return cost.build_history(rows)

    def test_period_start(self):
        self.assertEqual(cost._period_start(utc(2026, 8, 1), "week"), utc(2026, 7, 27))  # a Saturday → its Monday
        self.assertEqual(cost._period_start(utc(2026, 10, 26), "week"), utc(2026, 10, 26))  # a Monday stays
        self.assertEqual(cost._period_start(utc(2026, 9, 30), "month"), utc(2026, 9, 1))
        self.assertEqual(cost._period_start(utc(2026, 9, 30), "day"), utc(2026, 9, 30))

    def test_buckets_add_up(self):
        rows = [
            {"period": "day", "period_start": utc(2026, 8, 1), "runs": 10, "sandbox": 0, "hint": 1, "cb_ids": "a,b", "tasks_submitted": 2},
            {"period": "day", "period_start": utc(2026, 8, 3), "runs": 5, "sandbox": 2, "hint": 0, "cb_ids": "a", "tasks_submitted": 1},
            {"period": "day", "period_start": utc(2026, 9, 2), "runs": 7, "sandbox": 3, "hint": 0, "cb_ids": "c", "tasks_submitted": 0},
            {"period": "week", "period_start": utc(2026, 7, 27), "runs": 0, "sandbox": 0, "hint": 0, "cb_ids": "", "tasks_submitted": 2},
            {"period": "month", "period_start": utc(2026, 8, 1), "runs": 0, "sandbox": 0, "hint": 0, "cb_ids": "", "tasks_submitted": 3},
        ]
        now = utc(2026, 9, 2) + 3600
        h = self.build(rows, now, portal_day={utc(2026, 8, 3): 4.0}, portal_people={utc(2026, 8, 3): {"d"}})
        days, weeks, months = (h["periods"][p] for p in ("day", "week", "month"))
        self.assertEqual(len(days), 33)  # Aug 1 .. Sep 2
        self.assertTrue(days[-1]["today"])
        for key in ("runs", "sandbox", "hint", "portal"):
            total = sum(d[key] for d in days)
            self.assertAlmostEqual(sum(w[key] for w in weeks), total)
            self.assertAlmostEqual(sum(m[key] for m in months), total)
        self.assertEqual(weeks[0]["day"], utc(2026, 7, 27))
        self.assertTrue(weeks[0]["partial_start"])
        self.assertFalse(months[0]["partial_start"])
        self.assertEqual([m["day"] for m in months], [utc(2026, 8, 1), utc(2026, 9, 1)])
        self.assertTrue(months[-1]["today"] and not months[0]["today"])
        aug = months[0]
        self.assertEqual((aug["runs"], aug["sandbox"], aug["hint"], aug["portal"]), (15, 2, 1, 4.0))
        self.assertEqual(aug["active_cbs"], 3)  # a, b and the portal-only d
        self.assertEqual(aug["cb_days"], 4)  # a+b on Aug 1, a+d on Aug 3
        self.assertEqual(aug["tasks_submitted"], 3)  # the month's own distinct count, not the days' sum
        self.assertEqual(weeks[0]["tasks_submitted"], 2)
        self.assertNotIn("people", aug)


class BackgroundLoadTest(unittest.IsolatedAsyncioTestCase):
    """The lookup outlasts a proxy's wait, so the first call answers at once and later calls get the figures."""

    def setUp(self):
        cost._history.update(at=0.0, data=None, task=None, error=None, failed_at=0.0)
        self.addCleanup(cost._history.update, at=0.0, data=None, task=None, error=None, failed_at=0.0)

    async def test_loading_then_data(self):
        calls = []

        async def fake_load(force):
            calls.append(force)
            cost._history.update(at=cost.time.time(), data={"periods": {}})
            return cost._history["data"]

        with mock.patch.object(cost, "_load_history", fake_load):
            self.assertEqual(await cost.history(), {"loading": True})
            self.assertEqual(await cost.history(), {"loading": True})  # still running: no second lookup
            await cost._history["task"]
            self.assertEqual(await cost.history(), {"periods": {}})
            self.assertEqual(await cost.history(force=True), {"periods": {}, "refreshing": True})
            await cost._history["task"]
        self.assertEqual(calls, [False, True])

    async def test_error_is_reported(self):
        async def broken(force):
            raise RuntimeError("redash down")

        with mock.patch.object(cost, "_load_history", broken):
            await cost.history()
            await cost._history["task"]
            self.assertIn("redash down", (await cost.history())["error"])
            self.assertIn("redash down", (await cost.history())["error"])  # shown again, not silently retried
            self.assertIsNone(cost._history["task"])


if __name__ == "__main__":
    unittest.main()
