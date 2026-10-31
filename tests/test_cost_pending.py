"""Cost page: tasks not submitted yet (not cancelled) that 2+ people have claimed, with their spend so far, what of it is lost and what the current holder spent (Rafael + Evan, 2026-10-31)."""

import calendar
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import cost, config, db

DAY = 86400
X, Y, W, S, Q = "a" * 24, "b" * 24, "c" * 24, "d" * 24, "e" * 24


def utc(y, m, d):
    return calendar.timegm((y, m, d, 0, 0, 0))


NOW = utc(2026, 10, 20) + 3600
RECENT = utc(2026, 10, 19)


def tcd(task, t=RECENT, runs=3, sandbox=0, cb=X, email="x@gmail.com"):
    return {"KIND": "tcd", "T": t, "TASK": task, "CB": cb, "EMAIL": email, "RUNS": runs, "SANDBOX": sandbox, "REQUESTS": 1}


def state(task, status="pending", live=1, last=RECENT, worker="y@x.com"):
    return {"TASK": task, "TASK_STATUS": status, "SUBMITTED": 0, "LIVE_CLAIM": live, "PENDING": 1, "LAST_ACTIVITY": last,
            "LAST_WORKER_EMAIL": worker}


def node(task, uid, status="completed", t2=None):
    return {"KIND": "node", "NODE": f"n-{task}-{uid[:2]}", "TASK": task, "WORKER": uid, "STATUS": status, "T1": RECENT - DAY, "T2": t2}


def user(uid, email, source=None):
    return {"KIND": "user", "WORKER": uid, "EMAIL": email, "SOURCE": source}


FACTS = [
    tcd("two", runs=3, sandbox=1),  # X spent, then released it; Y holds it now: listed
    tcd("one", runs=5),  # one person (on the platform and in the portal): not listed
    tcd("subd", runs=4), {"KIND": "sub", "T": RECENT + 60, "TASK": "subd", "CB": X, "EMAIL": "x@gmail.com"},  # submitted
    tcd("quiet", t=utc(2026, 10, 10), runs=2),  # quiet 10 days, X and Y both gone: listed now (see the test)
    tcd("cxl", runs=6),  # cancelled: not listed
    tcd("staff", runs=7),  # X + an @scale.com claimer + a queue_manager account: one real person
    tcd("chat2", t=RECENT - DAY, runs=1),  # X on the platform, W only in the portal's chats: listed
]
STATES = [state("two"), state("one"), state("subd"), state("quiet", live=0, last=utc(2026, 10, 10)), state("cxl", status="canceled", live=0),
          state("staff"), state("chat2", live=0, last=RECENT - DAY)]
CB_FACTS = [user(X, "x@gmail.com+outlier1@x.com"), user(Y, "y@x.com"), user(S, "s@scale.com"), user(Q, "bot@x.com", source="queue_manager"),
            node("two", X), node("two", Y, status="pending", t2=NOW + DAY), {"KIND": "incomplete", "TASK": "two", "WORKER": X, "T1": RECENT + 30},
            node("one", X), node("subd", X), node("subd", Y),
            node("quiet", X), node("quiet", Y), node("cxl", X), node("cxl", Y), node("staff", X), node("staff", S),
            {"KIND": "incomplete", "TASK": "staff", "WORKER": Q}, node("chat2", X)]
CHATS = [{"task_id": "one", "user_id": X, "email": "x@gmail.com"}, {"task_id": "staff", "user_id": W, "email": "w@scale.com+outlier2@x.com"},
         {"task_id": "chat2", "user_id": W, "email": "w@x.com"}]
PORTAL = ({}, {}, {}, {"billed": 0.0, "estimated": 0.0}, [{"at": RECENT + 60, "usd": 6.0, "task": "two", "user": Y, "email": "y@x.com", "admin": False}])


class PendingMultiTest(unittest.TestCase):
    def build(self, cb_facts=CB_FACTS, chats=CHATS):
        asked = []
        def sources(ids):
            asked.append(set(ids))
            return cb_facts, chats
        with mock.patch.object(cost, "_portal", lambda s, b, e: PORTAL), mock.patch.object(cost.time, "time", lambda: NOW), \
                mock.patch.object(cost, "_claim_sources", sources):
            r = cost.build(FACTS, utc(2026, 10, 7), utc(2026, 10, 20), states=STATES)
        return r, asked

    def test_not_submitted_tasks_claimed_by_two_or_more(self):
        r, asked = self.build()
        # Submitted and cancelled tasks never get this far. With lost spend per claim (Evan, 2026-10-31) there's no "quiet
        # for 3 days" rule any more, so "quiet" (nobody holds it, not cancelled, still waiting for a submit) is listed too.
        self.assertEqual(asked, [{"two", "one", "quiet", "staff", "chat2"}])
        p = r["pending_multi"]
        self.assertEqual([t["task_id"] for t in p["tasks"]], ["two", "quiet", "chat2"])  # by spend
        two, quiet, chat2 = p["tasks"]
        self.assertEqual((two["claimers"], two["claimed_by"]), (2, "x@gmail.com, y@x.com"))
        self.assertEqual((two["agent_runs"], two["sandbox"], two["portal"], two["total"]), (3, 1, 6, 10))
        self.assertEqual((two["lost_agent_runs"], two["lost_evals"], two["lost"]), (3, 1, 4))  # X's, released
        self.assertEqual((two["holder_agent_runs"], two["holder_evals"], two["holder_spend"]), (0, 6, 6))  # Y's
        self.assertEqual((two["holder"], two["live_claim"], two["last_activity"]), ("y@x.com", True, RECENT + DAY))
        self.assertEqual((quiet["lost"], quiet["holder_spend"], quiet["live_claim"]), (2, 0, False))
        self.assertEqual((chat2["claimers"], chat2["live_claim"]), (2, False))  # W is only in the portal's chats
        self.assertEqual((p["count"], p["agent_runs"], p["evals"], p["partial"]), (3, 6, 7, False))
        lost = {(c["task_id"], c["email"]): c for c in r["lost"]["claims"]}
        self.assertEqual((lost[("two", "x@gmail.com")]["ended"], lost[("two", "x@gmail.com")]["ended_how"]), (RECENT + 30, "ended"))
        self.assertEqual(lost[("cxl", "x@gmail.com")]["reason"], "cancelled")
        self.assertNotIn(("subd", "x@gmail.com"), lost)
        self.assertEqual(r["lost"]["in_progress"], {"claims": 1, "tasks": 1, "agent_runs": 0, "evals": 6})

    def test_scale_and_internal_accounts_dont_count(self):
        who = cost._claimers(["staff"], CB_FACTS, CHATS)
        self.assertEqual(who, {"staff": {X: "x@gmail.com"}})

    def test_claims_not_loaded_yet_counts_chats_only(self):
        chats = CHATS + [{"task_id": "two", "user_id": X, "email": "x@gmail.com"}, {"task_id": "two", "user_id": Y, "email": "y@x.com"}]
        r, _ = self.build(cb_facts=None, chats=chats)
        p = r["pending_multi"]
        self.assertTrue(p["partial"])
        self.assertTrue(r["lost"]["partial"] and r["breakdown"]["partial"])
        self.assertEqual([t["task_id"] for t in p["tasks"]], ["two"])  # chat2 has only one chat holder
        # without node rows, Y counts as holding "two" only through the task state (live claim, last worker y@x.com)
        self.assertEqual(p["tasks"][0]["holder_spend"], 6)

    def test_no_task_state_query(self):
        with mock.patch.object(cost, "_portal", lambda s, b, e: PORTAL), mock.patch.object(cost.time, "time", lambda: NOW):
            self.assertIsNone(cost.build(FACTS, utc(2026, 10, 7), utc(2026, 10, 20))["pending_multi"])


class ClaimSourcesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.dict(cost.cbs._facts, {"rows": None})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())

    def test_reads_the_tasks_chats(self):
        for attempt, task, uid in (("n1", "two", X), ("n2", "two", Y), ("n3", "other", W), ("n4", "two", None)):
            db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at) values (?,?,?,?,?)", (attempt, task, uid, "", NOW))
        facts, chats = cost._claim_sources(["two", "chat2"])
        self.assertIsNone(facts)
        self.assertEqual(sorted(c["user_id"] for c in chats), [X, Y])


if __name__ == "__main__":
    unittest.main()
