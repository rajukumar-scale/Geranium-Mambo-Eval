"""Cost page by pipeline layer (Evan, 2026-11-09): every figure for L-1 or L0 only, and the all-layers split."""

import calendar
import unittest
from unittest import mock

from server import cost


def utc(y, m, d):
    return calendar.timegm((y, m, d, 0, 0, 0))


D1, D2 = utc(2026, 11, 2), utc(2026, 11, 3)
FACTS = [
    {"KIND": "day", "T": D1, "RUNS": 20, "RUNS_LINKED": 12, "SANDBOX": 0, "HINT": 4, "CB_IDS": "a,r", "TASKS_TOUCHED": 1},
    {"KIND": "day", "T": D2, "RUNS": 9, "RUNS_LINKED": 9, "SANDBOX": 0, "HINT": 0, "CB_IDS": "r", "TASKS_TOUCHED": 1},
    # D1: 10 at L-1 (8 linked), 6 at L0 (4 linked), 4 with no layer; D2: 9 at L0
    {"KIND": "dl", "T": D1, "LVL": -1, "RUNS": 10, "RUNS_LINKED": 8, "SANDBOX": 0, "REQUESTS": 5},
    {"KIND": "dl", "T": D1, "LVL": 0, "RUNS": 6, "RUNS_LINKED": 4, "SANDBOX": 0, "REQUESTS": 3},
    {"KIND": "dl", "T": D1, "LVL": None, "RUNS": 4, "RUNS_LINKED": 0, "SANDBOX": 0, "REQUESTS": 2},
    {"KIND": "dl", "T": D2, "LVL": 0, "RUNS": 9, "RUNS_LINKED": 9, "SANDBOX": 0, "REQUESTS": 4},
    {"KIND": "tcd", "T": D1, "TASK": "t1", "CB": "a", "EMAIL": "a@x.com", "RUNS": 8, "SANDBOX": 0, "REQUESTS": 4, "LVL": -1},
    {"KIND": "tcd", "T": D1, "TASK": "t1", "CB": "r", "EMAIL": "r@x.com", "RUNS": 4, "SANDBOX": 0, "REQUESTS": 2, "LVL": 0},
    {"KIND": "tcd", "T": D2, "TASK": "t1", "CB": "r", "EMAIL": "r@x.com", "RUNS": 9, "SANDBOX": 0, "REQUESTS": 4, "LVL": 0},
    {"KIND": "sub", "T": D1 + 60, "TASK": "t1", "CB": "a", "EMAIL": "a@x.com", "LVL": -1},
    {"KIND": "sub", "T": D2 + 60, "TASK": "t1", "CB": "r", "EMAIL": "r@x.com", "LVL": 0},
    {"KIND": "node", "T": D1, "TASK": "n1", "CB": "a", "LVL": -1},
    {"KIND": "node", "T": D1, "TASK": "n0", "CB": "r", "LVL": 0},
]
# portal reviews: 5 on a's L-1 chat (2 of it after the chat was archived to a new id), 3 on r's L0 chat, 1 on a chat whose
# node isn't known
RUNS = [{"id": "p1", "attempt_id": "n1", "at": D1 + 100, "usd": 3.0, "task": "t1", "user": "a", "email": "a@x.com"},
        {"id": "p1b", "attempt_id": "0000aa", "archived_from": "n1", "at": D1 + 150, "usd": 2.0, "task": "t1", "user": "a", "email": "a@x.com"},
        {"id": "p2", "attempt_id": "n0", "at": D2 + 100, "usd": 3.0, "task": "t1", "user": "r", "email": "r@x.com"},
        {"id": "p3", "attempt_id": "old", "at": D2 + 200, "usd": 1.0, "task": "t1", "user": "a", "email": "a@x.com"}]


def fake_q(sql, params=()):
    return [{"id": r["id"], "attempt_id": r["attempt_id"], "cost_estimate": r["usd"], "models": "{}", "created_at": r["at"], "by_admin": 0,
             "actor_user_id": r["user"], "actor_email": r["email"], "task_id": r["task"], "chat_user": r["user"], "chat_email": r["email"], "archived_from": r.get("archived_from")} for r in RUNS]


class LayerTest(unittest.TestCase):
    def build(self, layer=None):
        with mock.patch.object(cost.db, "q", fake_q), mock.patch.object(cost.spend, "run_spend", lambda run: run["cost_estimate"]), \
                mock.patch.object(cost.time, "time", lambda: D2 + 7200):
            return cost.build(FACTS, D1, D2, layer=layer)

    def test_all_layers_split_adds_up(self):
        r = self.build()
        for d in r["daily"]:
            self.assertAlmostEqual(sum(sum(v) for v in d["layers"].values()), d["total"])
        self.assertEqual(r["layers"], {"l1": [10, 5], "l0": [15, 3], "later": [0, 0], "unknown": [8, 1]})  # unknown = 4 runs + 4 hint, 1 portal
        self.assertEqual(r["weeks"][0]["layers"]["l0"], [15, 3])
        t1 = r["tasks"][0]
        self.assertEqual((t1["layers"]["l1"], t1["layers"]["l0"], t1["layers"]["unknown"]), ([8, 5], [13, 3], [0, 1]))
        self.assertEqual(r["summary"]["tasks_submitted"], 1)

    def test_one_layer(self):
        l1, l0 = self.build("l1"), self.build("l0")
        self.assertEqual((l1["layer"], l0["layer"]), ("l1", "l0"))
        self.assertEqual((l1["summary"]["runs"], l1["summary"]["runs_linked"], l1["summary"]["hint"], l1["summary"]["portal"]), (10, 8, 0, 5))
        self.assertEqual((l0["summary"]["runs"], l0["summary"]["runs_linked"], l0["summary"]["portal"]), (15, 13, 3))
        self.assertEqual([d["active_cbs"] for d in l0["daily"]], [1, 1])
        # each layer's own submissions and its submitters' own spend at that layer
        self.assertEqual([(t["task_id"], t["agent_runs"], t["portal"]) for t in l1["tasks"]], [("t1", 8, 5)])
        self.assertEqual([(t["task_id"], t["agent_runs"], t["portal"]) for t in l0["tasks"]], [("t1", 13, 3)])
        self.assertEqual([p["email"] for p in l0["people"]], ["r@x.com"])
        self.assertEqual(l1["layers"], self.build()["layers"])  # the split is always every layer's
        self.assertIsNone(self.build("bogus")["breakdown"])
        self.assertEqual(self.build("bogus")["layer"], "all")


# Everything spent on each task (Evan, 2026-11-10): runs tied by the task id (tr rows, whoever ran them) + every holder's evals
TR = [{"KIND": "tr", "T": D1, "TASK": "t1", "LVL": -1, "RUNS": 12, "SANDBOX": 0},  # 8 in a submission + 4 never in one
      {"KIND": "tr", "T": D1, "TASK": "t1", "LVL": 0, "RUNS": 4, "SANDBOX": 0},
      {"KIND": "tr", "T": D2, "TASK": "t1", "LVL": 0, "RUNS": 9, "SANDBOX": 0}]
DROPPER = {"id": "p4", "attempt_id": "nx", "at": D1 + 300, "usd": 4.0, "task": "t1", "user": "d", "email": "d@x.com"}


class EverythingTest(unittest.TestCase):
    def build(self, layer=None):
        runs = RUNS + [DROPPER]
        q = lambda sql, params=(): [{"id": r["id"], "attempt_id": r["attempt_id"], "cost_estimate": r["usd"], "models": "{}", "created_at": r["at"],  # noqa: E731
                                     "by_admin": 0, "actor_user_id": r["user"], "actor_email": r["email"], "task_id": r["task"],
                                     "chat_user": r["user"], "chat_email": r["email"], "archived_from": r.get("archived_from")} for r in runs]
        with mock.patch.object(cost.db, "q", q), mock.patch.object(cost.spend, "run_spend", lambda run: run["cost_estimate"]), \
                mock.patch.object(cost.time, "time", lambda: D2 + 7200):
            return cost.build(FACTS + TR, D1, D2, layer=layer)

    def test_task_cost_is_everything_spent_on_it(self):
        t1 = self.build()["tasks"][0]
        self.assertEqual((t1["runs_all"], t1["evals_submitters"], t1["evals_others"], t1["evals_all"], t1["total_all"]), (25, 9, 4, 13, 38))
        self.assertEqual((t1["agent_runs"], t1["portal"]), (21, 9))  # the submitters' own, as before
        self.assertEqual((t1["layers"]["l1"], t1["layers"]["l0"], t1["layers"]["unknown"]), ([12, 5], [13, 3], [0, 5]))

    def test_range_total_splits_into_task_spend_and_untied(self):
        r = self.build()
        s = r["summary"]["split"]
        self.assertEqual((s["submitted_tasks"], s["other_tasks"], s["untied"]), ([25, 13], [0, 0], [8, 0]))  # untied: 4 runs + 4 hint
        self.assertAlmostEqual(sum(sum(s[k]) for k in ("submitted_tasks", "other_tasks", "untied")), r["summary"]["total"])

    def test_one_layer(self):
        t1 = self.build("l0")["tasks"][0]
        self.assertEqual((t1["runs_all"], t1["evals_all"], t1["total_all"]), (13, 3, 16))


if __name__ == "__main__":
    unittest.main()
