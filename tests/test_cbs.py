"""CB tracker (/admin/cbs): one row per task x contributor at L-1, portal hours/$/reviews from their own runs, and the
training-first statuses (grace, needs training, trained, offboard only after training)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import cbs, config, db

T0 = cbs.START + 86400  # Oct 14
H = 3600
QMA, TRUSTED, NEW = "a" * 24, "b" * 24, "c" * 24


def user(uid, email, source=None, tr=None):
    return {"KIND": "user", "WORKER": uid, "EMAIL": email, "SOURCE": source, "TR_FIRST": tr and tr[0], "TR_LAST": tr and tr[1]}


def node(task, uid, t, status="completed", max_claim=None):
    return {"KIND": "node", "NODE": f"n-{task}-{uid[:2]}", "TASK": task, "WORKER": uid, "STATUS": status, "T1": t, "T2": max_claim}


def submit(task, uid, t, secs=0):
    return {"KIND": "submit", "TASK": task, "WORKER": uid, "T1": t, "SECS": secs}


def review(attempt, uid, command, verdict, t, cost=10.0):
    return {"attempt_id": attempt, "kind": "review", "command": command, "verdict": verdict, "created_at": t,
            "finished_at": t + 600, "cost_estimate": cost, "actor_user_id": uid, "by_admin": 0}


def row(outcome, hours=None, cost=0, reviews=1, t=T0):
    return {"outcome": outcome, "hours": hours, "cost": cost, "reviews": reviews, "first_open": t}


class RowsTest(unittest.TestCase):
    def test_hours_cost_reviews_from_own_runs(self):
        facts = [user(TRUSTED, "b@gmail.com+outlier1@x.com", tr=("2026-10-10", "2026-10-30")),
                 node("t1", TRUSTED, T0), submit("t1", TRUSTED, T0 + 30 * H)]
        chats = [{"attempt_id": "n-t1-bb", "task_id": "t1", "user_id": TRUSTED, "email": "b@gmail.com", "created_at": T0 + H,
                  "archived_from": None}]
        runs = [review("n-t1-bb", TRUSTED, "injection", "FAIL", T0 + H), review("n-t1-bb", TRUSTED, "injection", "PASS", T0 + 2 * H),
                review("n-t1-bb", TRUSTED, "prompt", "NON-FAIL", T0 + 3 * H), review("n-t1-bb", TRUSTED, "oe", "PASS", T0 + 4 * H),
                review("n-t1-bb", TRUSTED, "rubric", "PASS", T0 + 10 * H),
                review("n-t1-bb", "z" * 24, "rubric", "PASS", T0 + 11 * H),  # someone else's run doesn't count
                {**review("n-t1-bb", TRUSTED, "rubric", "PASS", T0 + 12 * H), "by_admin": 1}]  # nor an admin's
        [r] = cbs.build_rows(facts, chats, runs, now=T0 + 40 * H)
        self.assertEqual((r["group"], r["outcome"], r["email"]), ("trusted", "submitted", "b@gmail.com"))
        self.assertEqual(r["reviews"], 5)
        self.assertEqual(r["cost"], 50.0)
        self.assertEqual(r["tries"], {"injection": 2, "prompt": 1, "oe": 1, "rubric": 1})
        self.assertAlmostEqual(r["hours"], (10 * H + 600 - H) / H, places=2)  # first portal activity -> all 4 passed

    def test_groups_live_claims_and_old_tasks(self):
        facts = [user(QMA, "q@x.com", source="qm_auditor"), user(NEW, "n@x.com"), user("d" * 24, "s@scale.com"),
                 node("t2", QMA, T0), node("t3", NEW, T0, status="pending", max_claim=T0 + 99 * H),
                 node("t4", NEW, cbs.START - 5 * 86400), node("t5", "d" * 24, T0)]
        rows = {r["task"]: r for r in cbs.build_rows(facts, [], [], now=T0 + H)}
        self.assertNotIn("t4", rows)  # opened before Oct 13
        self.assertEqual(rows["t2"]["group"], "QMA")
        self.assertEqual((rows["t3"]["group"], rows["t3"]["outcome"]), ("non-trusted", "working"))
        self.assertEqual(rows["t5"]["group"], "internal")


class StatusTest(unittest.TestCase):
    def test_grace_then_needs_training_on_two_drops(self):
        self.assertEqual(cbs.status_of([row("dropped"), row("dropped")])[0], "grace")
        self.assertEqual(cbs.status_of([row("submitted", 20, 200), row("dropped"), row("dropped")])[0], "needs_training")

    def test_needs_training_on_two_slow_submits(self):
        slow = row("submitted", 60, 200)
        self.assertEqual(cbs.status_of([row("dropped"), slow, slow])[0], "needs_training")
        # Efficient / Acceptable judge the whole record, $ and hours per submitted task (Evan, 2026-11-03)
        self.assertEqual(cbs.status_of([row("dropped"), row("submitted", 30, 300), row("submitted", 32, 320)])[0], "acceptable")
        self.assertEqual(cbs.status_of([row("dropped"), row("submitted", 25, 250), row("submitted", 20, 200)])[0], "efficient")
        self.assertEqual(cbs.status_of([row("dropped"), slow, row("submitted", 25, 250)])[0], "ok")  # 42.5 h per submit
        # one fast task among many dropped, expensive claims: overall over the Flag bar -> needs training
        scarano = [row("dropped", cost=170, t=T0 + i * H) for i in range(8)] + [row("submitted", 20, 120, t=T0 + 9 * H)]
        status, reason = cbs.status_of(scarano)
        self.assertEqual(status, "needs_training")
        self.assertIn("Overall over the Flag bar: $1,480", reason)

    def test_offboard_only_after_training(self):
        before = [row("dropped", t=T0), row("dropped", t=T0 + H), row("dropped", t=T0 + 2 * H)]
        self.assertEqual(cbs.status_of(before)[0], "needs_training")
        trained = T0 + 3 * H
        self.assertEqual(cbs.status_of(before, trained)[0], "trained")
        self.assertEqual(cbs.status_of(before + [row("dropped", t=T0 + 4 * H)], trained)[0], "trained")
        self.assertEqual(cbs.status_of(before + [row("dropped", t=T0 + 4 * H), row("dropped", t=T0 + 5 * H)], trained)[0], "offboard")
        self.assertEqual(cbs.status_of(before + [row("dropped", t=T0 + 4 * H), row("submitted", 20, 200, t=T0 + 5 * H)], trained)[0], "efficient")

    def test_offboarded_comes_first_and_needs_the_tag_today(self):
        facts = [user(TRUSTED, "b@gmail.com", tr=("2026-10-10", "2026-11-01")),
                 {**user(NEW, "c@gmail.com"), "BLK_FIRST": "2026-10-28", "BLK_LAST": "2026-11-01"},
                 {**user(QMA, "a@gmail.com"), "BLK_FIRST": "2026-10-20", "BLK_LAST": "2026-10-25"},  # tag removed
                 node("t1", NEW, T0), node("t2", QMA, T0), node("t3", TRUSTED, T0)]
        rows = {r["user"]: r for r in cbs.build_rows(facts, [], [], now=T0 + H)}
        self.assertEqual(rows[NEW]["offboarded"], "2026-10-28")
        self.assertIsNone(rows[QMA]["offboarded"])
        self.assertIsNone(rows[TRUSTED]["offboarded"])
        trained = T0 + 3 * H  # even a trained person who would be "offboard" shows as done
        claims = [dict(row("dropped", t=T0 + 4 * H), offboarded="2026-10-28"), dict(row("dropped", t=T0 + 5 * H), offboarded="2026-10-28")]
        self.assertEqual(cbs.status_of(claims, trained), ("offboarded", "Tagged mcp_ac_temp_block since 2026-10-28"))

    def test_the_live_tag_wins_over_the_day_old_snapshot(self):
        # 2026-11-02: tagged today = not in the daily snapshot yet; untagged today = still in it
        facts = [{**user(NEW, "c@gmail.com"), "BLK_NOW": True},  # tagged this morning
                 {**user(QMA, "a@gmail.com"), "BLK_FIRST": "2026-10-28", "BLK_LAST": "2026-11-01", "BLK_NOW": False},  # removed today
                 {**user(TRUSTED, "b@gmail.com"), "BLK_FIRST": "2026-10-30", "BLK_LAST": "2026-11-01", "BLK_NOW": True},
                 node("t1", NEW, T0), node("t2", QMA, T0), node("t3", TRUSTED, T0)]
        rows = {r["user"]: r for r in cbs.build_rows(facts, [], [], now=T0 + H)}
        self.assertTrue(rows[NEW]["offboarded"])
        self.assertIsNone(rows[QMA]["offboarded"])
        self.assertEqual(rows[TRUSTED]["offboarded"], "2026-10-30")

    def test_live_claims_dont_count_as_drops(self):
        self.assertEqual(cbs.status_of([row("submitted", 50, 400), row("dropped"), row("working")])[0], "ok")


class ReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())

    def test_report_and_training_record(self):
        rows = []
        for i, (uid, grp) in enumerate([(QMA, "QMA"), (NEW, "non-trusted"), (NEW, "non-trusted"), (NEW, "non-trusted")]):
            rows.append({"task": f"t{i}", "user": uid, "email": f"{uid[0]}@x.com", "group": grp, "first_open": T0 + i * H,
                         "outcome": "submitted" if i == 0 else "dropped", "submitted_at": None, "portal": True,
                         "hours": 20.0 if i == 0 else None, "cost": 100.0, "reviews": 3, "tries": {}, "runs_per_stage": {}})
        rows.append({**rows[1], "task": "t9", "reviews": 0, "first_open": T0 + 9 * H})  # untouched: left out, counted
        cbs.mark_trained(NEW, "c@x.com", "evan@scale.com", "walked through /rubric")
        d = cbs.report(rows, cbs._training(), now=T0 + 20 * H)
        people = {p["user"]: p for p in d["people"]}
        self.assertEqual(people[NEW]["status"], "trained")
        self.assertEqual(people[NEW]["untouched"], 1)
        self.assertEqual(people[NEW]["claims"], 3)
        self.assertEqual(people[QMA]["status"], "grace")
        self.assertEqual(d["conversion"]["claimed"], 1)
        self.assertEqual(d["conversion"]["qma_efficient"], 1)
        cbs.unmark_trained(NEW)
        d = cbs.report(rows, cbs._training(), now=T0 + 20 * H)
        self.assertEqual({p["user"]: p["status"] for p in d["people"]}[NEW], "needs_training")

    def test_needs_training_is_ranked_worst_first_by_the_four_factors(self):
        # Evan 2026-11-03: 1 submit rate, 2 spend per submit, 3 hours per submit, 4 the last 2 claims went badly
        def person(uid, outcomes, cost, hours=None):
            return [{"task": f"{uid[:3]}{i}", "user": uid, "email": f"{uid[:3]}@x.com", "group": "non-trusted",
                     "first_open": T0 + i * H, "outcome": o, "submitted_at": None, "portal": True,
                     "hours": hours if o == "submitted" else None, "cost": cost, "reviews": 3, "tries": {},
                     "runs_per_stage": {}} for i, o in enumerate(outcomes)]
        d_ = "dropped"
        rows = (person("1" * 24, ["submitted", "submitted", d_, d_], 50, 10)      # 50% submitted
                + person("2" * 24, [d_, d_, d_, d_], 30)                           # 0%, $120 total
                + person("3" * 24, [d_, d_, d_, d_, d_], 40)                       # 0%, $200 total: worst
                + person("4" * 24, ["submitted", d_, d_, d_], 50, 40)              # 25%, $200/submit
                + person("5" * 24, ["submitted", d_, d_, d_], 50, 10))             # 25%, $200/submit, fewer hours
        d = cbs.report(rows, cbs._training(), now=T0 + 30 * H)
        ranked = [p for p in d["people"] if p["status"] == "needs_training"]
        self.assertEqual([p["user"][0] for p in ranked], ["3", "2", "4", "5", "1"])
        self.assertEqual([p["rank"] for p in ranked], [1, 2, 3, 4, 5])
        first = ranked[0]
        self.assertEqual((first["submit_rate"], first["spend_per_submit"], first["recent_bad"]), (0.0, 200.0, True))
        self.assertEqual((ranked[2]["spend_per_submit"], ranked[2]["hours_per_submit"]), (200.0, 40.0))
        best = sorted((p for p in d["people"] if p.get("best_rank")), key=lambda p: p["best_rank"])
        self.assertEqual([p["user"][0] for p in best], ["1", "5", "4"])  # 50% first; then cheaper, fewer hours
        self.assertNotIn("best_rank", next(p for p in d["people"] if p["user"][0] == "2"))  # never submitted


if __name__ == "__main__":
    unittest.main()
