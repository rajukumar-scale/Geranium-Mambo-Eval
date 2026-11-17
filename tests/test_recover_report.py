"""A review's report the portal missed (2026-11-04 05:09: a deploy restart in the seconds a /rubric finished; the new
process's re-attach took a fresh "before" snapshot that already held the new PASS report, so no card and no pass).
The fix keeps each boat review's start-time snapshot on disk for the re-attach, and an admin can recover a missed
report from its file."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, runner, workspace


class RecoverReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.object(runner.fingerprint, "for_attempt", lambda a, s: "fp"),
                  mock.patch.object(runner.rerun_guard, "record", lambda *a: None),
                  mock.patch.object(runner.staleness, "snapshot", lambda a: {}),
                  mock.patch.object(runner.proofs, "create", lambda run, chat, info, **k: {"stage": info["stage"], "verdict": info["verdict"], "code": "EVP-TEST"}),
                  mock.patch.object(workspace, "parse_report", lambda stage, text: {
                      "stage": stage, "sha256": str(hash(text)), "verdict": "PASS" if "passes" in text else "FAIL",
                      "score": 5 if "passes" in text else 1, "exec_status": "COMPLETE"})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        now = time.time()
        self.t0 = now - 900
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity, pending_review_run) "
              "values ('a1','t1','u1','a@x.com','s',?,?,'r1')", (now, now))
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, started_at, finished_at) "
              "values ('r1','a1','review','rubric','done',?,?,?)", (self.t0, self.t0, now - 60))
        root = workspace.ensure_attempt("a1")
        (root / "task_inputs" / "reports").mkdir(parents=True, exist_ok=True)
        self.inp = root / "task_inputs" / "04_Rubric.json"
        self.inp.write_text("[]")
        os.utime(self.inp, (self.t0 - 100, self.t0 - 100))  # saved before the review started
        (root / "task_inputs" / "inputs.json").write_text(json.dumps({"inputs": {"rubric": {
            "state": "saved", "files": [{"path": "Evals/task_inputs/04_Rubric.json"}]}}}))
        self.report = root / "task_inputs" / "reports" / config.REPORT_FILES["rubric"]

    def write_report(self, text, at):
        self.report.write_text(text)
        os.utime(self.report, (at, at))

    def test_a_missed_pass_gets_its_report_and_pass_card(self):
        self.write_report("The rubric passes.", self.t0 + 600)
        out = runner.Runner().recover_report("r1")
        self.assertEqual((out["verdict"], out["score"], out["pass"]), ("PASS", 5, True))
        roles = [m["role"] for m in db.q("select role from messages where attempt_id='a1' order by id")]
        self.assertEqual(roles, ["report", "proof"])
        run = db.get_run("r1")
        self.assertEqual((run["report_stage"], run["verdict"]), ("rubric", "PASS"))
        self.assertIsNone(db.get_chat("a1")["pending_review_run"])  # no longer "waiting for an answer"
        with self.assertRaises(ValueError):  # never twice
            runner.Runner().recover_report("r1")

    def test_a_report_not_written_by_this_run_is_refused(self):
        self.write_report("The rubric passes.", self.t0 - 3600)  # from an earlier run
        with self.assertRaises(ValueError) as ctx:
            runner.Runner().recover_report("r1")
        self.assertIn("wasn't written by this review", str(ctx.exception))

    def test_inputs_changed_after_the_report_are_refused(self):
        self.write_report("The rubric passes.", self.t0 + 600)
        os.utime(self.inp, (self.t0 + 700, self.t0 + 700))
        with self.assertRaises(ValueError) as ctx:
            runner.Runner().recover_report("r1")
        self.assertIn("Inputs changed", str(ctx.exception))
        self.assertEqual(db.q("select count(*) n from messages")[0]["n"], 0)

    def test_a_failed_report_is_posted_without_a_pass(self):
        self.write_report("The rubric fails on R3.", self.t0 + 600)
        out = runner.Runner().recover_report("r1")
        self.assertFalse(out["pass"])
        self.assertEqual([m["role"] for m in db.q("select role from messages")], ["report"])

    def test_the_reattach_uses_the_snapshot_from_the_start(self):
        rf = config.REPORT_FILES["rubric"]
        runner.save_before("a1", "r1", {rf: 123.0})
        self.write_report("The rubric passes.", self.t0 + 600)  # copied back before the restart
        self.assertEqual(runner.load_before("a1", "r1"), {rf: 123.0})  # so the new report counts as new
        self.assertEqual(runner.load_before("a1", "r-old"), workspace.report_snapshot("a1"))  # older runs: as before


if __name__ == "__main__":
    unittest.main()
