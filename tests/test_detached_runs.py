"""Chat replies and server reviews in detached containers (2026-11-02): the container reads logs/<run>.io/in.txt and
writes out.jsonl / err.txt there; the portal follows the file until `docker wait` returns, and after a restart it
re-attaches to a container that is still running (or finished meanwhile) instead of cutting it off.

A stand-in `docker` on PATH plays the container: `run -d` starts a background writer of the scripted lines, `wait`
waits for it and prints its exit code, `kill` stops it, `ps` lists the live ones."""

import asyncio
import json
import os
import stat
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, runner, workspace

FAKE_DOCKER = textwrap.dedent("""\
    #!/bin/bash
    # stand-in docker: containers are folders in $FAKE_DOCKER_DIR/<name>
    D=__DIR__; FAKE_SCRIPT=__SCRIPT__; cmd=$1; shift
    case "$cmd" in
      run)
        name=""; io=""
        while [ $# -gt 0 ]; do
          case "$1" in --name) name=$2; shift 2;; --mount) case "$2" in *target=/evp_io) io=${2#type=bind,source=}; io=${io%%,*};; esac; shift 2;;
          -d) shift;; *) shift;; esac
        done
        mkdir -p "$D/$name"; echo "$io" > "$D/$name/io"
        ( sleep 0.2; cat "$FAKE_SCRIPT" | while IFS= read -r l; do
            [ -e "$D/$name/killed" ] && exit 137
            case "$l" in SLEEP*) sleep "${l#SLEEP }";; *) printf '%s\\n' "$l" >> "$io/out.jsonl";; esac
          done; echo 0 > "$D/$name/exit" ) >/dev/null 2>&1 &
        echo "$name";;
      wait)
        while [ ! -e "$D/$1/exit" ] && [ ! -e "$D/$1/killed" ]; do sleep 0.1; done
        if [ -e "$D/$1/killed" ]; then echo 137; else cat "$D/$1/exit"; fi;;
      kill) touch "$D/$1/killed";;
      rm) shift; rm -rf "$D/$1";;
      ps) ls "$D" 2>/dev/null;;
    esac
""")

INIT = json.dumps({"type": "system", "subtype": "init", "session_id": "S-1"})
TEXT = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Here is your answer."}]}})
RESULT = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Here is your answer.",
                     "total_cost_usd": 0.01, "usage": {}})


class DetachedRunTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        (self.tmp / "containers").mkdir()
        self.script = self.tmp / "script.txt"
        docker.write_text(FAKE_DOCKER.replace("__DIR__", str(self.tmp / "containers")).replace("__SCRIPT__", str(self.script)))
        docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
        for p in (mock.patch.dict(os.environ, env), mock.patch.object(config, "DATA_DIR", self.tmp / "data"),
                  mock.patch.object(db, "_conn", None), mock.patch.object(config, "RUNNER_MODE", "docker"),
                  mock.patch.object(workspace, "docker_mounts", lambda a: [])):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) values "
              "('a1','t1','u1','a@x.com','S-1',?,?)", (time.time(), time.time()))
        workspace.ensure_attempt("a1")
        db.ex("insert into runs(id, attempt_id, kind, status, created_at, started_at) values ('r1','a1','followup','running',?,?)",
              (time.time(), time.time()))
        self.r = runner.Runner()

    def lines(self, *lines):
        self.script.write_text("\n".join(lines) + "\n")

    def test_a_reply_runs_in_a_detached_container_and_is_read_to_the_end(self):
        self.lines(INIT, TEXT, "SLEEP 0.3", RESULT)
        out = asyncio.run(self.r._spawn("r1", db.get_chat("a1"), "hello", ["--session-id", "S-1"]))
        self.assertEqual(out["returncode"], 0)
        self.assertEqual((out["result"] or {}).get("result"), "Here is your answer.")
        self.assertEqual(db.get_run("r1")["container"], "evp-r1")
        log = (workspace.attempt_dir("a1") / "logs" / "r1.jsonl").read_text()
        self.assertIn('"type": "result"', log)
        self.assertFalse(runner.docker_io("a1", "r1").exists())  # cleaned up
        self.assertFalse((self.tmp / "containers" / "evp-r1").exists())  # container removed

    def test_a_restart_mid_reply_re_attaches_instead_of_cutting_it_off(self):
        self.lines(INIT, TEXT, "SLEEP 1.5", RESULT)
        completed = []

        async def scenario():
            task = asyncio.ensure_future(self.r._spawn("r1", db.get_chat("a1"), "hello", ["--session-id", "S-1"]))
            await asyncio.sleep(0.8)
            task.cancel()  # the old portal process goes away mid-reply; the container keeps going
            try:
                await task
            except asyncio.CancelledError:
                pass

        # simulate the process dying: stop the follow without cleaning up the container
        async def no_cleanup(*a, **k):
            return None
        with mock.patch.object(runner, "_docker", no_cleanup), mock.patch.object(runner.shutil, "rmtree"):
            asyncio.run(scenario())
        self.assertTrue((self.tmp / "containers" / "evp-r1").exists())
        db.update("runs", "id", "r1", status="running", proxy_token="evp-tok")
        restarted = runner.Runner()
        with mock.patch.object(restarted, "_complete", mock.AsyncMock(side_effect=lambda rid, o: completed.append(o))), \
                mock.patch.object(runner, "drain_file", lambda: self.tmp / "nodrain"):
            names = runner._containers()
            self.assertIn("evp-r1", names)
            asyncio.run(restarted._reattach_docker("r1"))
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["returncode"], 0)
        self.assertEqual((completed[0]["result"] or {}).get("result"), "Here is your answer.")
        self.assertFalse((self.tmp / "containers" / "evp-r1").exists())

    def test_start_re_attaches_live_containers_and_fails_only_those_that_are_gone(self):
        db.ex("update runs set container='evp-r1', proxy_token='tok' where id='r1'")
        db.ex("insert into runs(id, attempt_id, kind, status, container, created_at, started_at) values "
              "('r2','a1','followup','running','evp-r2',?,?)", (time.time(), time.time()))
        (self.tmp / "containers" / "evp-r1").mkdir()
        (self.tmp / "containers" / "evp-old").mkdir()  # a leftover nobody follows
        r = runner.Runner()
        with mock.patch.object(runner.Runner, "_reattach_docker", mock.AsyncMock()) as reattach, \
                mock.patch.object(runner.Runner, "_janitor", mock.AsyncMock()), \
                mock.patch.object(runner.Runner, "_orphan_sweeper", mock.AsyncMock()), \
                mock.patch.object(runner.shutil, "which", lambda n: "/x/docker"):
            async def go():
                r.start()
                await asyncio.sleep(0.2)
            asyncio.run(go())
        self.assertEqual(r.reattach_docker, ["r1"])
        self.assertEqual(db.get_run("r1")["status"], "running")
        self.assertEqual(db.get_run("r2")["status"], "failed")  # its container is gone: cut off as before
        self.assertFalse((self.tmp / "containers" / "evp-old").exists())
        self.assertTrue((self.tmp / "containers" / "evp-r1").exists())

    def test_timeout_and_cancel_stop_the_container(self):
        self.lines(INIT, "SLEEP 30", RESULT)
        with mock.patch.object(config, "RUN_TIMEOUT_MIN", 0.02):  # ~1 s
            out = asyncio.run(self.r._spawn("r1", db.get_chat("a1"), "hello", ["--session-id", "S-1"]))
        self.assertTrue(out.get("timeout"))
        self.assertFalse((self.tmp / "containers" / "evp-r1").exists())


if __name__ == "__main__":
    unittest.main()
