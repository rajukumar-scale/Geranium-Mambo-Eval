"""Robustness fixes (2026-11-02) after the status page showed reviews failing on boat machine starts, model errors and
"database is locked":
- B: a review boat can't give a machine waits longer between tries (6 tries); a light review then runs on this server.
- C: a temporary model/API error in a docker run (chat reply or server review) resumes the session, as on boat.
- D: a busy database is retried instead of failing the write; the proxy's usage write never breaks an answer."""

import asyncio
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import boatrun, config, db, hostload, proxy, runner, runner_image, workspace


def _outcome(**kw):
    out = {"events": 0, "result": None, "texts": [], "init": False, "stderr_tail": "", "before": {}, "after": {}, "reports": {}}
    out.update(kw)
    return out


class NoMachineFallbackTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.object(config, "SERVER_REVIEW_COMMANDS", ["injection", "hardness"]),
                  mock.patch.object(config, "SERVER_REVIEW_MIN_FREE_GB", 5.0),
                  mock.patch.object(runner, "server_reviews_enabled", lambda: True),
                  mock.patch.object(config, "SERVER_REVIEW_SLOTS", 3), mock.patch.object(runner, "deploy_pending", lambda: False),
                  mock.patch.object(runner_image, "present", lambda *a: True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        self.free = 9.0
        p = mock.patch.object(hostload, "mem_available_gb", lambda: self.free)
        p.start()
        self.addCleanup(p.stop)
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, session_started, session_guard, created_at, "
              "last_activity) values (?,?,?,?,?,?,?,?,?)", ("a1", "t1", "u1", "a@x.com", "S-1", 0, runner.GUARD_VERSION, now, now))
        (workspace.ensure_attempt("a1") / "logs" / "r1.prompt.txt").write_text("/injection")

    def run_review(self, command, held=()):
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at) values ('r1','a1','review',?,'queued',?)",
              (command, time.time()))
        calls = []

        async def boat(run_id, chat, prompt, args):
            calls.append("boat")
            return _outcome(no_machine=True, infra_error="boat new failed: readiness command did not succeed")

        async def here(run_id, chat, prompt, args):
            calls.append("server")
            return _outcome(init=True, result={"result": "PASS", "is_error": False, "subtype": "success"})

        r = runner.Runner()
        r.pool_of["r1"] = "boat"
        r.server_held.update(held)
        done = mock.AsyncMock()
        with mock.patch.object(r, "_spawn_boat", boat), mock.patch.object(r, "_spawn", here), \
                mock.patch.object(r, "_complete", done):
            asyncio.run(r._execute("r1"))
        return r, calls, done.call_args[0][1]

    def test_a_light_review_without_a_boat_machine_runs_on_the_server(self):
        r, calls, outcome = self.run_review("injection")
        self.assertEqual(calls, ["boat", "server"])
        self.assertEqual((outcome["result"] or {}).get("result"), "PASS")
        run = db.get_run("r1")
        self.assertEqual(run["boat_phase"], "server")
        self.assertIsNone(run["runner_box"])
        self.assertIn("r1", r.server_held)  # released by the worker when the run ends
        self.assertTrue(any("portal server instead" in a.get("text", str(a)) if isinstance(a, dict) else "portal server instead" in a
                            for a in r.activity.get("r1", [])))

    def test_heavy_reviews_or_low_memory_still_fail_as_before(self):
        r, calls, outcome = self.run_review("rubric")
        self.assertEqual(calls, ["boat"])
        self.assertTrue(outcome.get("no_machine"))
        db.ex("delete from runs")
        self.free = 2.0
        r, calls, outcome = self.run_review("injection")
        self.assertEqual(calls, ["boat"])
        db.ex("delete from runs")
        self.free = 9.0
        r, calls, outcome = self.run_review("injection", held={"x", "y", "z"})  # server slots full: no overload
        self.assertEqual(calls, ["boat"])

    def test_machine_tries_wait_longer_than_before(self):
        waits = [min(runner.MACHINE_WAIT_S * m, runner.MACHINE_WAIT_MAX_S) for m in range(1, runner.MACHINE_TRIES)]
        self.assertGreaterEqual(runner.MACHINE_TRIES, 6)
        self.assertGreaterEqual(sum(waits), 6 * 60)  # boat's bad stretches last minutes, not seconds


class DockerRetryTest(unittest.TestCase):
    """The scripts a docker run executes: run Claude; on a temporary API error, resume the session (up to 3 times)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.io = Path(tmp.name) / "io"
        self.io.mkdir()
        self.bin = Path(tmp.name) / "bin"
        self.bin.mkdir()

    def fake_claude(self, results):
        """A stand-in `claude`: the Nth call prints results[N] as its stream-json result line."""
        counter = self.io.parent / "n"
        lines = "\n".join(f'  {i}) echo {json.dumps(json.dumps(r, separators=(",", ":")))};;' for i, r in enumerate(results))
        script = (f"#!/bin/bash\nn=$(cat {counter} 2>/dev/null || echo 0); echo $((n+1)) > {counter}\n"
                  f"echo \"$@\" >> {self.io.parent}/args\ncat > /dev/null\ncase $n in\n{lines}\nesac\n")
        exe = self.bin / "claude"
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        return str(exe)

    def run_scripts(self, results):
        claude = self.fake_claude(results)
        (self.io / "in.txt").write_text("hello")
        runner.write_docker_scripts(self.io, [claude, "-p", "--session-id", "S-1"])
        review = (self.io / "review.sh").read_text().replace("/evp_io", str(self.io)).replace("cd /work", f"cd {self.io}")
        for name in ("cmd.sh", "resume.sh"):
            f = self.io / name
            f.write_text(f.read_text().replace("/evp_io", str(self.io)).replace("cd /work", f"cd {self.io}"))
        (self.io / "review.sh").write_text(review)
        env = {**os.environ, "EVP_RETRY_WAIT": "0"}
        proc = subprocess.run(["bash", str(self.io / "review.sh")], env=env, capture_output=True, text=True, timeout=60)
        args = (self.io.parent / "args").read_text().splitlines()
        return proc, args

    def test_a_temporary_api_error_resumes_the_session(self):
        err = {"type": "result", "is_error": True, "result": "API Error: Connection error. ECONNRESET"}
        ok = {"type": "result", "is_error": False, "result": "done"}
        proc, args = self.run_scripts([err, ok])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(args), 2)
        self.assertIn("--session-id S-1", args[0])
        self.assertIn("--resume S-1", args[1])
        self.assertIn('"result":"done"', (self.io / "out.jsonl").read_text())
        self.assertIn("resuming the session (1 of 3)", (self.io / "err.txt").read_text())

    def test_real_errors_are_not_retried_and_retries_stop_at_three(self):
        budget = {"type": "result", "is_error": True, "result": "Budget exceeded for this key"}
        proc, args = self.run_scripts([budget])
        self.assertEqual(len(args), 1)
        (self.io.parent / "n").unlink()
        (self.io.parent / "args").unlink()
        err = {"type": "result", "is_error": True, "result": "API Error: 529 status code (overloaded_error)"}
        proc, args = self.run_scripts([err] * 6)
        self.assertEqual(len(args), 4)  # first run + 3 resumes

    def test_boat_and_docker_share_the_retry_patterns(self):
        self.assertNotIn("__RETRY_ERRORS__", boatrun.REVIEW_SH)
        self.assertNotIn("__RETRY_ERRORS__", runner.DOCKER_REVIEW_SH)
        self.assertIn(boatrun.RETRY_ERRORS, runner.DOCKER_REVIEW_SH)


class BusyDatabaseTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())

    def flaky(self, failures):
        real = db.conn()
        calls = {"n": 0}

        class Flaky:
            def execute(self, *a):
                calls["n"] += 1
                if calls["n"] <= failures:
                    raise sqlite3.OperationalError("database is locked")
                return real.execute(*a)
        return calls, mock.patch.object(db, "conn", lambda: Flaky())

    def test_a_locked_database_is_retried_not_failed(self):
        calls, patch = self.flaky(2)
        with patch, mock.patch.object(db.time, "sleep", lambda s: None):
            db.ex("create table t(x)")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(db.q("select count(*) n from t")[0]["n"], 0)

    def test_it_gives_up_after_a_few_tries_and_other_errors_raise_at_once(self):
        calls, patch = self.flaky(99)
        with patch, mock.patch.object(db.time, "sleep", lambda s: None):
            with self.assertRaises(sqlite3.OperationalError):
                db.ex("create table t(x)")
        self.assertEqual(calls["n"], 4)
        with self.assertRaises(sqlite3.OperationalError):
            db.ex("select * from no_such_table")

    def test_a_failed_usage_write_never_breaks_the_answer(self):
        with mock.patch.object(proxy.db, "record_call", side_effect=sqlite3.OperationalError("database is locked")):
            proxy._record("r1", "claude-opus", "/v1/messages", 200, {}, 0.0)  # logged, not raised
