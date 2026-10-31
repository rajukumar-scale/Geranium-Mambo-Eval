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
NO_PORTAL = ({}, {}, {}, {"billed": 0.0, "estimated": 0.0}, [])


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

    def test_lost_spend(self):
        # Lost is per person x task now (Evan, 2026-10-31), not per task after 3 quiet days: "busy" (worked on yesterday,
        # claim no longer held, last worker someone else) used to count as still active and now is lost at once.
        now = utc(2026, 10, 20) + 3600
        facts = FACTS + [
            {"KIND": "tcd", "T": utc(2026, 10, 10), "TASK": "gone", "CB": "c", "EMAIL": "c@x.com", "RUNS": 4, "SANDBOX": 1, "REQUESTS": 1},
            {"KIND": "tcd", "T": utc(2026, 10, 12), "TASK": "held", "CB": "d", "EMAIL": "d@x.com", "RUNS": 2, "SANDBOX": 0, "REQUESTS": 1},
            {"KIND": "tcd", "T": utc(2026, 10, 19), "TASK": "busy", "CB": "e", "EMAIL": "e@x.com", "RUNS": 3, "SANDBOX": 0, "REQUESTS": 1},
            {"KIND": "tcd", "T": utc(2026, 10, 11), "TASK": "l1", "CB": "f", "EMAIL": "f@x.com", "RUNS": 9, "SANDBOX": 0, "REQUESTS": 1},
            {"KIND": "tcd", "T": utc(2026, 10, 11), "TASK": "test", "CB": "s", "EMAIL": "s@scale.com", "RUNS": 8, "SANDBOX": 0, "REQUESTS": 1},
        ]
        states = [
            {"TASK": "gone", "TASK_STATUS": "canceled", "SUBMITTED": 0, "LIVE_CLAIM": 0, "LAST_ACTIVITY": utc(2026, 10, 10), "CANCEL_REASON": "dup"},
            {"TASK": "held", "TASK_STATUS": "pending", "SUBMITTED": 0, "LIVE_CLAIM": 1, "PENDING": 1, "LAST_ACTIVITY": utc(2026, 10, 12),
             "LAST_WORKER": "d", "LAST_WORKER_EMAIL": "d@x.com"},
            {"TASK": "quiet", "TASK_STATUS": "pending", "SUBMITTED": 0, "LIVE_CLAIM": 0, "PENDING": 1, "LAST_ACTIVITY": utc(2026, 10, 1)},
            {"TASK": "busy", "TASK_STATUS": "pending", "SUBMITTED": 0, "LIVE_CLAIM": 1, "PENDING": 1, "LAST_ACTIVITY": utc(2026, 10, 19),
             "LAST_WORKER": "z", "LAST_WORKER_EMAIL": "z@x.com"},
            {"TASK": "l1", "TASK_STATUS": "pending", "SUBMITTED": 1, "LAST_ACTIVITY": utc(2026, 10, 11)},  # submitted by an @scale.com account
        ]
        # portal reviews: a contributor's on "quiet" counts; an admin's is an internal test
        portal = ({}, {}, {}, {"billed": 0.0, "estimated": 0.0},
                  [{"at": utc(2026, 10, 14) + 60, "usd": 6.0, "task": "quiet", "user": "q", "email": "q@x.com", "admin": False},
                   {"at": utc(2026, 10, 14) + 90, "usd": 2.0, "task": "quiet", "user": None, "email": "boss@scale.com", "admin": True}])
        with mock.patch.object(cost, "_portal", lambda s, b, e: portal), mock.patch.object(cost.time, "time", lambda: now), \
                mock.patch.object(cost, "_claim_sources", lambda ids: ([], [])):
            r = cost.build(facts, utc(2026, 10, 7), utc(2026, 10, 20), states=states)
            off = cost.build(facts, utc(2026, 10, 7), utc(2026, 10, 20))
        lost = r["lost"]
        by = {(c["task_id"], c["email"]): c for c in lost["claims"]}
        # held: d is the live claim's holder; l1: f didn't submit it (the @scale.com submitter did), so f's spend is lost
        self.assertEqual(set(by), {("gone", "c@x.com"), ("quiet", "q@x.com"), ("busy", "e@x.com"), ("l1", "f@x.com")})
        self.assertEqual((by[("gone", "c@x.com")]["reason"], by[("gone", "c@x.com")]["cancel_reason"], by[("gone", "c@x.com")]["total"]), ("cancelled", "dup", 5))
        self.assertEqual(by[("quiet", "q@x.com")]["reason"], "released")
        self.assertEqual((by[("quiet", "q@x.com")]["portal"], by[("quiet", "q@x.com")]["ended_how"]), (6, "last seen"))
        self.assertEqual((lost["agent_runs"], lost["evals"]), (16, 7))
        self.assertEqual(lost["in_progress"], {"claims": 1, "tasks": 1, "agent_runs": 2, "evals": 0})  # held
        self.assertEqual({x["reason"]: x["claims"] for x in lost["reasons"]}, {"cancelled": 1, "released": 3})
        day = {d["day"]: d for d in r["daily"]}
        self.assertEqual((day[utc(2026, 10, 10)]["lost_runs"], day[utc(2026, 10, 10)]["lost_evals"]), (4, 1))
        self.assertEqual(day[utc(2026, 10, 19)]["lost_runs"], 3)  # yesterday's lost spend shows at once
        self.assertEqual(day[utc(2026, 10, 14)]["lost_evals"], 6)
        self.assertAlmostEqual(sum(w["lost_runs"] + w["lost_evals"] for w in r["weeks"]), 23)
        self.assertEqual(r["breakdown"]["internal"], {"agent_runs": 8, "evals": 2, "admin_portal": 2})
        self.assertIsNone(off["lost"])  # no task-state query: lost spend left out
        self.assertIsNone(off["breakdown"])
        self.assertEqual(r["pending_multi"]["tasks"], [])  # nobody on record claimed any of them twice
        self.assertIsNone(off["pending_multi"])

    def test_span(self):
        with mock.patch.object(cost.time, "time", lambda: utc(2026, 10, 28) + 100):
            self.assertEqual(cost.span(14), (utc(2026, 10, 15), utc(2026, 10, 28)))
            self.assertEqual(cost.span(start="2026-07-01", end="2026-12-01"), (cost.START, utc(2026, 10, 28)))
            self.assertEqual(cost.span(start="2026-10-20", end="2026-10-10"), (utc(2026, 10, 10), utc(2026, 10, 10)))
            self.assertEqual(cost.span(start="nope", end="2026-10-10"), (utc(2026, 10, 15), utc(2026, 10, 28)))


A, B, C, D, E, F, S, Q = ("%024x" % i for i in range(1, 9))
NOW = utc(2026, 10, 20) + 3600


def claim_case():
    """Each person x task case once (range Oct 14 - 20). Per day the logged totals also carry what can't be tied to a
    task: $10 of task runs never seen in a submission and $2 of hint pipeline (Oct 15), $0.50 of untagged sandbox."""
    d = lambda day: utc(2026, 10, day)  # noqa: E731
    tcd = lambda day, task, cb, email, runs=0, sandbox=0: {"KIND": "tcd", "T": d(day), "TASK": task, "CB": cb, "EMAIL": email,  # noqa: E731
                                                          "RUNS": runs, "SANDBOX": sandbox, "REQUESTS": 1}
    facts = [
        {"KIND": "day", "T": d(14), "RUNS": 2, "RUNS_LINKED": 2, "SANDBOX": 0, "HINT": 0, "CB_IDS": C},
        {"KIND": "day", "T": d(15), "RUNS": 23, "RUNS_LINKED": 13, "SANDBOX": 1.5, "HINT": 2, "CB_IDS": f"{A},{F},{S},{Q}"},
        {"KIND": "day", "T": d(16), "RUNS": 9, "RUNS_LINKED": 9, "SANDBOX": 3, "HINT": 0, "CB_IDS": f"{A},{B},{C}"},
        {"KIND": "day", "T": d(19), "RUNS": 5, "RUNS_LINKED": 5, "SANDBOX": 0, "HINT": 0, "CB_IDS": D},
        tcd(15, "sub2", A, "a@x.com", runs=4),  # A released it ...
        tcd(16, "sub2", B, "b@x.com", runs=6, sandbox=1),  # ... B took it over and submitted: A's spend is still lost
        tcd(14, "back", C, "c@x.com", runs=2), tcd(16, "back", C, "c@x.com", runs=3),  # C released it, came back, submitted
        tcd(19, "live", D, "d@x.com", runs=5),  # D holds a live claim
        tcd(15, "exp", F, "f@x.com", runs=1),  # F's claim ran out
        tcd(16, "cx", A, "a@x.com", sandbox=2),  # the task was cancelled
        tcd(15, "int", S, "s@scale.com", runs=8), tcd(15, "int", Q, "bot@x.com", sandbox=1),  # internal accounts
        {"KIND": "sub", "T": d(17), "TASK": "sub2", "CB": B, "EMAIL": "b@x.com"},
        {"KIND": "sub", "T": d(17) + 60, "TASK": "back", "CB": C, "EMAIL": "c@x.com"},
    ]
    node = lambda task, uid, status, t2=None: {"KIND": "node", "NODE": f"{task}-{uid[-1]}", "TASK": task, "WORKER": uid,  # noqa: E731
                                               "STATUS": status, "T1": d(14), "T2": t2}
    claims = [{"KIND": "user", "WORKER": u, "EMAIL": e, "SOURCE": src} for u, e, src in
              ((A, "a@x.com", None), (B, "b@x.com", None), (C, "c@x.com", None), (D, "d@x.com", None), (E, "e@x.com+outlier", None),
               (F, "f@x.com", None), (S, "s@scale.com", None), (Q, "bot@x.com", "queue_manager"))]
    claims += [node("sub2", A, "completed"), node("sub2", B, "completed"), node("back", C, "completed"),
               node("live", D, "pending", NOW + DAY), node("pause", E, "paused", d(17)), node("exp", F, "pending", d(16)),
               node("cx", A, "canceled"),
               {"KIND": "incomplete", "TASK": "sub2", "WORKER": A, "T1": d(15) + 3600},
               {"KIND": "incomplete", "TASK": "back", "WORKER": C, "T1": d(14) + 3600}]
    st = lambda task, status="pending", **k: {"TASK": task, "TASK_STATUS": status, "SUBMITTED": 0, "LIVE_CLAIM": 0, "PENDING": 0,  # noqa: E731
                                              "PAUSED": 0, "LAST_ACTIVITY": d(14), **k}
    states = [st("sub2", SUBMITTED=1), st("back", SUBMITTED=1), st("live", LIVE_CLAIM=1, PENDING=1, LAST_WORKER=D, LAST_WORKER_EMAIL="d@x.com"),
              st("pause", PAUSED=1, LAST_WORKER=E, LAST_WORKER_EMAIL="e@x.com"), st("exp", PENDING=1, LAST_WORKER=F),
              st("cx", "canceled", CANCEL_REASON="dup"), st("int", PENDING=1)]
    run = lambda usd, task, user, email, admin=False: {"at": d(18) + 60, "usd": usd, "task": task, "user": user, "email": email, "admin": admin}  # noqa: E731
    runs = [run(4.0, "pause", E, "e@x.com"), run(3.0, "int", None, "boss@scale.com", admin=True), run(1.5, None, A, "a@x.com")]
    portal = ({d(18): 8.5}, {E: 4.0, A: 1.5}, {d(18): {E, A}}, {"billed": 0.0, "estimated": 8.5}, runs)
    return facts, claims, states, portal


class LostByClaimTest(unittest.TestCase):
    """Lost spend per person x task (Evan, 2026-10-31) and the total split into parts that add up to it."""

    def build(self, claims=True):
        facts, cb_facts, states, portal = claim_case()
        with mock.patch.object(cost, "_portal", lambda s, b, e: portal), mock.patch.object(cost.time, "time", lambda: NOW), \
                mock.patch.object(cost, "_claim_sources", lambda ids: (cb_facts if claims else None, [])):
            return cost.build(facts, utc(2026, 10, 14), utc(2026, 10, 20), states=states)

    def test_each_case(self):
        r = self.build()
        lost = {(c["task_id"], c["email"]): c for c in r["lost"]["claims"]}
        self.assertEqual(set(lost), {("sub2", "a@x.com"), ("exp", "f@x.com"), ("cx", "a@x.com")})
        # released, even though B submitted the task later; when = A's incomplete attempt
        self.assertEqual((lost[("sub2", "a@x.com")]["reason"], lost[("sub2", "a@x.com")]["ended"], lost[("sub2", "a@x.com")]["total"]),
                         ("released", utc(2026, 10, 15) + 3600, 4))
        self.assertEqual((lost[("exp", "f@x.com")]["ended"], lost[("exp", "f@x.com")]["ended_how"]), (utc(2026, 10, 16), "expired"))
        self.assertEqual((lost[("cx", "a@x.com")]["reason_label"], lost[("cx", "a@x.com")]["cancel_reason"]), ("Task cancelled", "dup"))
        b = r["breakdown"]
        self.assertEqual(b["claims"], {"submitted": 2, "lost": 3, "in_progress": 2, "internal": 2})  # people x tasks; C's re-claim isn't lost
        self.assertEqual(b["submitted"], {"agent_runs": 11, "evals": 1})  # B + C (both of C's turns)
        self.assertEqual(b["lost"], {"agent_runs": 5, "evals": 2})
        self.assertEqual(b["in_progress"], {"agent_runs": 5, "evals": 4})  # D's live claim + E's paused one
        self.assertEqual(b["internal"], {"agent_runs": 8, "evals": 4, "admin_portal": 3})  # @scale.com, queue_manager, admin run
        self.assertEqual(b["untied"], {"agent_runs": 12, "evals": 2, "hint": 2, "portal_no_task": 1.5})
        self.assertEqual(r["lost"]["in_progress"], {"claims": 2, "tasks": 2, "agent_runs": 5, "evals": 4})
        day = {d["day"]: d for d in r["daily"]}
        self.assertEqual((day[utc(2026, 10, 15)]["lost_runs"], day[utc(2026, 10, 16)]["lost_evals"]), (5, 2))
        a = next(p for p in r["people"] if p["email"] == "a@x.com")
        self.assertEqual((a["lost_agent_runs"], a["lost_evals"], a["lost"]), (4, 2, 6))

    def test_task_cost_is_the_submitters_own(self):
        r = self.build()
        tasks = {t["task_id"]: t for t in r["tasks"]}
        self.assertEqual((tasks["sub2"]["agent_runs"], tasks["sub2"]["sandbox"], tasks["sub2"]["total"]), (6, 1, 7))  # B's only
        self.assertEqual((tasks["sub2"]["lost_before_agent_runs"], tasks["sub2"]["lost_before"]), (4, 4))  # A's, shown next to it
        self.assertEqual((tasks["back"]["total"], tasks["back"]["lost_before"]), (5, 0))
        self.assertEqual(r["summary"]["per_task_all_in"], 27)  # $54 / 2 tasks

    def test_breakdown_adds_up_in_each_view(self):
        r = self.build()
        s, b = r["summary"], r["breakdown"]
        runs = round(sum(b[k]["agent_runs"] for k in cost.PARTS), 2)
        evals = round(sum(b[k]["evals"] for k in cost.PARTS), 2)
        self.assertEqual(runs, round(s["runs"] + s["hint"], 2))  # task runs view (viewOf: runs + hint)
        self.assertEqual(evals, round(s["sandbox"] + s["portal"], 2))  # evals view (sandbox + portal)
        self.assertEqual(round(runs + evals, 2), s["total"])  # both
        self.assertEqual((s["total"], b["total"]), (54, {"agent_runs": 41, "evals": 13, "all": 54}))

    def test_claims_not_loaded_uses_the_task_state(self):
        r = self.build(claims=False)
        self.assertTrue(r["breakdown"]["partial"] and r["lost"]["partial"])
        lost = {(c["task_id"], c["email"]) for c in r["lost"]["claims"]}
        # D (live claim, last worker) and E (paused, last worker) still count as in progress; C's re-claim and B are
        # submitted from the submissions alone. Q's worker source is unknown without the claims data, so it shows as lost.
        self.assertEqual(lost, {("sub2", "a@x.com"), ("exp", "f@x.com"), ("cx", "a@x.com"), ("int", "bot@x.com")})
        self.assertEqual(r["breakdown"]["in_progress"], {"agent_runs": 5, "evals": 4})


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
            self.assertEqual(len(calls), 3)  # facts + portal billed + task states, once
            self.assertIn("lost", r)
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
