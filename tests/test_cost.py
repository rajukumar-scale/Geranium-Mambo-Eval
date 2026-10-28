"""Cost page: any date range cut from the cached facts (days, tasks, contributors, weeks, months), served without waiting."""

import calendar
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from server import cost

DAY = 86400


def utc(y, m, d):
    return calendar.timegm((y, m, d, 0, 0, 0))


FACTS = [
    {"KIND": "day", "T": utc(2026, 8, 1), "RUNS": 10, "RUNS_LINKED": 6, "SANDBOX": 0, "HINT": 1, "CB_IDS": "a,b", "TASKS_TOUCHED": 1},
    {"KIND": "day", "T": utc(2026, 8, 3), "RUNS": 5, "RUNS_LINKED": 5, "SANDBOX": 2, "HINT": 0, "CB_IDS": "a", "TASKS_TOUCHED": 1},
    {"KIND": "day", "T": utc(2026, 9, 2), "RUNS": 7, "RUNS_LINKED": 0, "SANDBOX": 3, "HINT": 0, "CB_IDS": "c", "TASKS_TOUCHED": 1},
    {"KIND": "tcd", "T": utc(2026, 8, 1), "TASK": "t1", "CB": "a", "EMAIL": "a@x.com", "RUNS": 6, "SANDBOX": 0, "REQUESTS": 3},
    {"KIND": "tcd", "T": utc(2026, 8, 3), "TASK": "t1", "CB": "a", "EMAIL": "a@x.com", "RUNS": 5, "SANDBOX": 2, "REQUESTS": 2},
    {"KIND": "tcd", "T": utc(2026, 9, 2), "TASK": "t2", "CB": "s", "EMAIL": "s@scale.com", "RUNS": 0, "SANDBOX": 3, "REQUESTS": 1},
    {"KIND": "sub", "T": utc(2026, 8, 3) + 3600, "TASK": "t1", "CB": "a", "EMAIL": "a@x.com"},
    {"KIND": "sub", "T": utc(2026, 9, 2) + 60, "TASK": "t1", "CB": "b", "EMAIL": "b@x.com"},
    {"KIND": "sub", "T": utc(2026, 9, 2) + 90, "TASK": "t3", "CB": "b", "EMAIL": "b@x.com"},
]
NO_PORTAL = ({}, {}, {}, {}, {"billed": 0.0, "estimated": 0.0})


class BuildTest(unittest.TestCase):
    def build(self, start, end, now=utc(2026, 9, 2) + 7200):
        with mock.patch.object(cost, "_portal", lambda s, b, e: NO_PORTAL), mock.patch.object(cost.time, "time", lambda: now):
            return cost.build(FACTS, start, end)

    def test_periods(self):
        self.assertEqual(cost._period_start(utc(2026, 8, 1), "week"), utc(2026, 7, 27))  # a Saturday → its Monday
        self.assertEqual(cost._period_start(utc(2026, 9, 30), "month"), utc(2026, 9, 1))
        self.assertEqual(cost._period_end(utc(2026, 12, 1), "month"), utc(2027, 1, 1))
        self.assertEqual(cost._period_end(utc(2026, 7, 27), "week"), utc(2026, 8, 3))

    def test_whole_history(self):
        r = self.build(utc(2026, 8, 1), utc(2026, 9, 2))
        self.assertEqual(len(r["daily"]), 33)
        self.assertTrue(r["daily"][-1]["today"])
        for key in ("runs", "sandbox", "hint", "portal", "total"):
            total = sum(d[key] for d in r["daily"])
            self.assertAlmostEqual(sum(w[key] for w in r["weeks"]), total)
            self.assertAlmostEqual(sum(m[key] for m in r["months"]), total)
        self.assertTrue(r["weeks"][0]["partial_start"])
        self.assertFalse(r["months"][0]["partial_start"])
        self.assertTrue(r["months"][-1]["partial_end"] and r["months"][-1]["today"])
        aug = r["months"][0]
        self.assertEqual((aug["active_cbs"], aug["cb_days"], aug["tasks_submitted"]), (2, 3, 1))
        self.assertEqual(r["months"][1]["tasks_submitted"], 2)  # t1 again + t3
        t1 = next(t for t in r["tasks"] if t["task_id"] == "t1")
        self.assertEqual((t1["agent_runs"], t1["sandbox"], t1["submissions"], t1["submitters"]), (11, 2, 2, "a@x.com, b@x.com"))
        self.assertEqual({p["email"] for p in r["people"]}, {"a@x.com", "b@x.com"})  # @scale.com left out
        b = next(p for p in r["people"] if p["email"] == "b@x.com")
        self.assertEqual((b["task_runs"], b["tasks_submitted"]), (0, 2))
        self.assertEqual(r["summary"]["tasks_submitted"], 2)

    def test_range_cuts(self):
        r = self.build(utc(2026, 8, 2), utc(2026, 8, 3))  # a past range: t1 submitted on Aug 3
        self.assertEqual([d["day"] for d in r["daily"]], [utc(2026, 8, 2), utc(2026, 8, 3)])
        self.assertEqual(r["summary"]["runs"], 5)
        self.assertEqual([t["task_id"] for t in r["tasks"]], ["t1"])
        self.assertEqual(r["tasks"][0]["task_runs"], 13)  # tasks: everything tied to them since they started
        a = r["people"][0]
        self.assertEqual((a["task_runs"], a["active_days"]), (7, 1))  # contributors: the range only
        week = r["weeks"][0]
        self.assertTrue(week["partial_start"] and not week["partial_end"])  # Jul 27 week, from Aug 2
        self.assertEqual(r["weeks"][1]["day"], utc(2026, 8, 3))
        self.assertTrue(r["weeks"][1]["partial_end"])

    def test_span(self):
        with mock.patch.object(cost.time, "time", lambda: utc(2026, 10, 28) + 100):
            self.assertEqual(cost.span(14), (utc(2026, 10, 15), utc(2026, 10, 28)))
            self.assertEqual(cost.span(start="2026-07-01", end="2026-12-01"), (cost.START, utc(2026, 10, 28)))
            self.assertEqual(cost.span(start="2026-10-20", end="2026-10-10"), (utc(2026, 10, 10), utc(2026, 10, 10)))
            self.assertEqual(cost.span(start="nope", end="2026-10-10"), (utc(2026, 10, 15), utc(2026, 10, 28)))


class ServeTest(unittest.IsolatedAsyncioTestCase):
    """The lookup outlasts a proxy's wait: the page never waits on it, and a restart keeps the last figures."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for p in (mock.patch.object(cost, "CACHE_FILE", self.tmp / "cost_facts.json"),
                  mock.patch.object(cost, "_portal", lambda s, b, e: NO_PORTAL),
                  mock.patch.dict(cost._facts, {"at": 0.0, "rows": None, "billed": [], "task": None, "error": None, "failed_at": 0.0})):
            p.start()
            self.addCleanup(p.stop)

    async def test_loading_then_figures_then_restart(self):
        calls = []

        async def fake_rows(query_id, parameters=None, max_age=0, timeout=90):
            calls.append(query_id)
            return FACTS if query_id == cost.config.COST_FACTS_QUERY_ID else []

        with mock.patch.object(cost.auth, "redash_rows", fake_rows):
            self.assertEqual(await cost.report(14), {"loading": True})
            self.assertEqual(await cost.report(14), {"loading": True})  # still running: no second lookup
            await cost._facts["task"]
            r = await cost.report(start="2026-08-01", end="2026-09-02")
            self.assertIn("t1", [t["task_id"] for t in r["tasks"]])
            self.assertFalse(r["refreshing"])
            self.assertEqual(len(calls), 2)  # facts + portal billed, once
            self.assertTrue((await cost.report(14, force=True))["refreshing"])
            await cost._facts["task"]
        saved = json.loads((self.tmp / "cost_facts.json").read_text())
        self.assertEqual(len(saved["rows"]), len(FACTS))
        cost._facts.update(rows=None, at=0.0)
        cost.restore()  # after a restart
        self.assertEqual(len(cost._facts["rows"]), len(FACTS))

    async def test_error_shown_not_retried_at_once(self):
        async def broken(*a, **k):
            raise RuntimeError("redash down")

        with mock.patch.object(cost.auth, "redash_rows", broken):
            await cost.report(14)
            await cost._facts["task"]
            self.assertIn("redash down", (await cost.report(14))["error"])
            self.assertIsNone(cost._facts["task"])


if __name__ == "__main__":
    unittest.main()
