"""Reviews on Modal sandboxes (REVIEW_RUNNER=modal) against a fake modal-proxy: the machine contract (create, exec, copy
in and out), following a review by polling its output, and the runner and box pool picking Modal over boat. Nothing here
reaches Modal, s2sauth or LiteLLM."""

import asyncio
import base64
import json
import os
import re
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

from server import boatrun, boxpool, config, db, modalrun, runner, workspace


class FakeModalProxy:
    """A virtual sandbox filesystem behind the endpoints modalrun uses (one sandbox is enough here)."""

    def __init__(self):
        self.files = {}  # path -> bytes
        self.requests = []
        self.terminated = []

    def handler(self, request: httpx.Request):
        path = request.url.path
        if path == "/sandbox":
            self.requests.append(("create", json.loads(request.content)))
            return httpx.Response(200, json={"sandbox_id": "sb-test", "sandbox_url": "https://x"})
        if path == "/sandbox-write-file":
            target = request.url.params["target_path"]
            self.files[target] = request.content
            self.requests.append(("write", target, len(request.content)))
            return httpx.Response(200, json={"bytes_written": len(request.content), "target_path": target})
        if path.startswith("/terminate-sandbox/"):
            self.terminated.append(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={})
        if path == "/sandbox-exec":
            body = json.loads(request.content)
            assert body["command"][:2] == ["bash", "-lc"]
            rc, out = self.run(body["command"][2])
            self.requests.append(("exec", body["command"][2]))
            return httpx.Response(200, json={"exit_code": rc, "stdout": out, "stderr": ""})
        return httpx.Response(404, json={"detail": "no route"})

    def run(self, cmd):
        """Just the shell forms modalrun emits."""
        if re.fullmatch(r"mkdir -p (\S+)", cmd):
            return 0, ""
        if m := re.fullmatch(r"stat -c %s (\S+)", cmd):
            data = self.files.get(m.group(1).strip("'"))
            return (0, f"{len(data)}\n") if data is not None else (1, "No such file")
        if m := re.fullmatch(r"tail -c \+(\d+) (\S+)(?: 2>/dev/null)? \| head -c (\d+) \| base64 -w0", cmd):
            data = self.files.get(m.group(2).strip("'"), b"")
            start, limit = int(m.group(1)) - 1, int(m.group(3))
            return 0, base64.b64encode(data[start:start + limit]).decode()
        if cmd == "cat /tmp/claude.exit 2>/dev/null || echo RUNNING":
            return 0, self.files["/tmp/claude.exit"].decode() if "/tmp/claude.exit" in self.files else "RUNNING\n"
        return 127, f"fake sandbox doesn't know: {cmd}"


class ModalTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeModalProxy()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

        async def no_jwt():
            return {"x-jwt-assertion": "JWT test"}

        for p in (mock.patch.object(modalrun, "_client", httpx.AsyncClient(transport=httpx.MockTransport(self.fake.handler),
                                                                           base_url="https://modal-proxy.test")),
                  mock.patch.object(modalrun, "_headers", no_jwt),
                  mock.patch.object(config, "DATA_DIR", self.tmp),
                  mock.patch.object(config, "MODAL_TEMPLATE_IMAGE", "im-base im-universe")):
            p.start()
            self.addCleanup(p.stop)


class MachineContractTest(ModalTest):
    def test_a_sandbox_is_attributed_and_boots_the_template(self):
        box = asyncio.run(modalrun.create_box({"run": "r1"}))
        self.assertEqual(box, "sb-test")
        _, body = self.fake.requests[0]
        self.assertEqual(body["snapshot_image_id"], "im-base")
        self.assertEqual(body["image"], config.MODAL_BASE_IMAGE)
        self.assertEqual(body["directory_snapshots"], [{"snapshot_image_id": "im-universe", "mount_path": "/universe"}])
        self.assertRegex(body["project_id"], r"^[0-9a-f]{24}$")
        self.assertEqual(body["tags"], {"app": "atlas-eval", "run": "r1"})
        self.assertLessEqual(body["idle_timeout"], 1800)
        self.assertGreaterEqual(body["timeout"], config.RUN_TIMEOUT_MIN * 60)

    def test_no_template_fails_clearly(self):
        with mock.patch.object(config, "MODAL_TEMPLATE_IMAGE", ""):
            with self.assertRaisesRegex(RuntimeError, "build_template"):
                asyncio.run(modalrun.create_box())

    def test_a_base_image_without_universes_is_enough(self):
        with mock.patch.object(config, "MODAL_TEMPLATE_IMAGE", "im-base"):
            asyncio.run(modalrun.create_box())
        self.assertNotIn("directory_snapshots", self.fake.requests[0][1])

    def test_files_round_trip_in_chunks(self):
        data = os.urandom(3500)
        src, dst = self.tmp / "in.bin", self.tmp / "out.bin"
        src.write_bytes(data)
        with mock.patch.object(modalrun, "CHUNK", 1000):  # several chunks
            asyncio.run(modalrun.scp_to("sb-test", src, "/tmp/payload.tgz"))
            self.assertEqual(self.fake.files["/tmp/payload.tgz"], data)
            asyncio.run(modalrun.scp_from("sb-test", "/tmp/payload.tgz", dst))
        self.assertEqual(dst.read_bytes(), data)
        self.assertEqual(len([r for r in self.fake.requests if r[0] == "exec" and r[1].startswith("tail -c")]), 4)

    def test_a_missing_file_fails_the_download(self):
        with self.assertRaisesRegex(RuntimeError, "download from sb-test failed"):
            asyncio.run(modalrun.scp_from("sb-test", "/tmp/state_out.tgz", self.tmp / "x"))

    def test_read_from_follows_a_growing_file(self):
        self.fake.files["/tmp/out.jsonl"] = b'{"a":1}\n{"b"'
        first = asyncio.run(modalrun.read_from("sb-test", "/tmp/out.jsonl", 0))
        self.fake.files["/tmp/out.jsonl"] += b':2}\n'
        second = asyncio.run(modalrun.read_from("sb-test", "/tmp/out.jsonl", len(first)))
        self.assertEqual(first + second, b'{"a":1}\n{"b":2}\n')
        self.assertEqual(asyncio.run(modalrun.read_from("sb-test", "/tmp/out.jsonl", len(first + second))), b"")

    def test_ssh_reports_failures_as_a_returncode(self):
        rc, out = asyncio.run(modalrun.ssh("sb-test", "something unknown"))
        self.assertEqual(rc, 127)
        self.assertIn("doesn't know", out)

    def test_delete_terminates_the_sandbox(self):
        asyncio.run(modalrun.delete_box("sb-test"))
        self.assertEqual(self.fake.terminated, ["sb-test"])


class PollTest(ModalTest):
    def setUp(self):
        super().setUp()
        self.r = runner.Runner()
        for p in (mock.patch.object(runner.Runner, "cancel_requested", staticmethod(lambda run_id: False)),
                  mock.patch.object(self.r, "_push_activity", lambda *a, **k: None),
                  mock.patch.object(runner.db, "get_run", lambda run_id: {"kind": "review", "resumed": 0, "milestones": None})):
            p.start()
            self.addCleanup(p.stop)

    def poll(self, sleep):
        outcome = {"events": 0, "result": None, "texts": [], "init": False, "stderr_tail": ""}
        with mock.patch.object(runner.asyncio, "sleep", sleep), open(self.tmp / "log.jsonl", "w") as raw_log:
            asyncio.run(self.r._poll_modal("r1", "a1", "sb-test", raw_log, outcome, time.time() + 60))
        return outcome

    def test_the_stream_is_read_across_polls_and_the_exit_ends_it(self):
        lines = [{"type": "system", "subtype": "init"},
                 {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reviewing the oracle events"}]}},
                 {"type": "result", "subtype": "success", "total_cost_usd": 1.5}]
        stream = "".join(json.dumps(x) + "\n" for x in lines).encode()
        self.fake.files["/tmp/out.jsonl"] = stream[:40]  # a review mid-line
        calls = {"n": 0}

        async def sleep(seconds):
            calls["n"] += 1
            if calls["n"] == 1:
                self.fake.files["/tmp/out.jsonl"] = stream  # the rest arrives
            elif calls["n"] == 2:
                self.fake.files["/tmp/claude.exit"] = b"0\n"  # then the review ends

        outcome = self.poll(sleep)
        self.assertEqual(outcome["events"], 3)
        self.assertTrue(outcome["init"])
        self.assertEqual(outcome["result"]["total_cost_usd"], 1.5)
        self.assertEqual(outcome["returncode"], 0)

    def test_a_gone_sandbox_ends_the_poll(self):
        async def gone(*a, **k):
            return None

        async def no_wait(seconds):
            return None

        with mock.patch.object(runner.modalrun, "read_from", gone):
            outcome = self.poll(no_wait)
        self.assertTrue(outcome.get("box_gone"))
        self.assertNotIn("returncode", outcome)


class RoutingTest(unittest.TestCase):
    """REVIEW_RUNNER=modal sends reviews to Modal sandboxes; boat stays exactly as it was."""

    def setUp(self):
        p = mock.patch.object(config, "REVIEW_RUNNER", "modal")
        p.start()
        self.addCleanup(p.stop)

    def test_reviews_go_to_a_remote_machine_and_follow_ups_stay_here(self):
        r = runner.Runner()
        self.assertEqual(r.place({"kind": "review", "command": "oe"}), "boat")
        self.assertEqual(r.place({"kind": "followup", "command": None}), "main")
        self.assertIs(runner.remote(), modalrun)
        with mock.patch.object(config, "REVIEW_RUNNER", "boat"):
            self.assertIs(runner.remote(), boatrun)

    def test_no_machine_reuse_on_modal_and_a_finished_sandbox_is_ended(self):
        deleted = []

        async def delete(box):
            deleted.append(box)

        async def create(tags=None):
            return "sb-1"

        with mock.patch.object(config, "BOX_REUSE_MAX", 15), mock.patch.object(modalrun, "create_box", create), \
                mock.patch.object(modalrun, "delete_box", delete), mock.patch.object(boxpool, "_boxes", {}):
            self.assertFalse(boxpool.enabled())
            box = asyncio.run(boxpool.acquire())
            asyncio.run(boxpool.release(box, clean=True))
        self.assertEqual((box, deleted), ("sb-1", ["sb-1"]))

    def test_config_accepts_modal_and_needs_a_real_project_and_the_meter(self):
        with mock.patch.object(config, "PROXY_PUBLIC_URL", "https://meter.example"), \
                mock.patch.object(config, "MODAL_PROJECT_ID", "chat-task-tools"):
            self.assertTrue(any("MODAL_PROJECT_ID" in p for p in config.check()))
        with mock.patch.object(config, "PROXY_PUBLIC_URL", ""):
            self.assertTrue(any("REVIEW_RUNNER=modal needs PROXY_PUBLIC_URL" in p for p in config.check()))
        with mock.patch.object(config, "PROXY_PUBLIC_URL", "https://meter.example"):
            self.assertFalse(any("REVIEW_RUNNER" in p or "MODAL_PROJECT_ID" in p for p in config.check()))


class SpawnTest(unittest.TestCase):
    """_spawn_boat on Modal: the review calls the meter (never holds the LiteLLM key), no iptables egress lock, the
    universe is loaded once per machine before the review starts, and the sandbox is ended afterwards."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        (self.tmp / "bundle-x.tgz").write_bytes(b"bundle")
        for p in (mock.patch.object(config, "DATA_DIR", self.tmp), mock.patch.object(db, "_conn", None),
                  mock.patch.object(config, "REVIEW_RUNNER", "modal"),
                  mock.patch.object(config, "PROXY_PUBLIC_URL", "https://meter.example"),
                  mock.patch.object(config, "LITELLM_API_KEY", "sk-secret-key"),
                  mock.patch.object(config, "MODAL_TEMPLATE_IMAGE", "im-base"),
                  mock.patch.object(boatrun, "current_bundle", lambda: self.tmp / "bundle-x.tgz")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, session_started, session_guard, created_at, "
              "last_activity) values (?,?,?,?,?,?,?,?,?)", ("a1", "t1", "u1", "a@x.com", "S-1", 0, runner.GUARD_VERSION, now, now))
        workspace.ensure_attempt("a1")
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, loadtest) "
              "values ('r1','a1','review','oe','running',?,1)", (now,))
        self.calls, self.uploads = [], {}

    def run_spawn(self, start_outputs):
        outputs = list(start_outputs)

        async def create_box(tags=None):
            self.calls.append("create")
            return f"sb-{self.calls.count('create')}"

        async def scp_to(box, local, remote):
            self.calls.append(f"upload {remote}")
            if remote == "/tmp/payload.tgz":
                with tarfile.open(local) as tar:
                    self.uploads = {n: tar.extractfile(n).read().decode() for n in tar.getnames()
                                    if n in ("run.env", "proxy_host")}

        async def ssh(box, command, timeout=300):
            if "run.sh" in command:
                self.calls.append(f"start {box}")
                return 0, outputs.pop(0)
            return 0, ""

        async def ensure_universe(box, which, log_fn):
            self.calls.append(f"universe {box}")

        async def delete_box(box):
            self.calls.append(f"delete {box}")

        async def follow(run_id, attempt, box, token, outcome, deadline, replay=False):
            self.calls.append(f"follow {box}")
            return outcome

        r = runner.Runner()
        with mock.patch.multiple(modalrun, create_box=create_box, scp_to=scp_to, ssh=ssh, ensure_universe=ensure_universe,
                                 delete_box=delete_box), \
                mock.patch.object(r, "_follow_boat", follow), mock.patch.object(r, "_push_activity", lambda *a: None), \
                mock.patch.object(runner.asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(r._spawn_boat("r1", db.get_chat("a1"), "300", ["--session-id", "S-1"]))
            asyncio.run(asyncio.sleep(0))

    def test_the_review_calls_the_meter_and_never_gets_the_key(self):
        self.run_spawn(["STARTED"])
        self.assertIn("ANTHROPIC_BASE_URL=https://meter.example", self.uploads["run.env"])
        self.assertIn("ANTHROPIC_AUTH_TOKEN=evp-", self.uploads["run.env"])
        self.assertNotIn("sk-secret-key", self.uploads["run.env"])
        self.assertEqual(self.uploads["proxy_host"], "")  # no iptables lock on Modal (gVisor has none)
        self.assertEqual(self.calls, ["create", "upload /tmp/payload.tgz", "upload /tmp/evp_bundle.tgz", "universe sb-1",
                                      "start sb-1", "follow sb-1"])

    def test_a_retried_start_doesnt_load_the_universe_again_but_a_new_machine_does(self):
        self.run_spawn(["not yet", "STARTED"])  # a retried start on the same machine
        self.assertEqual([c for c in self.calls if c.startswith(("universe", "start"))],
                         ["universe sb-1", "start sb-1", "start sb-1"])
        self.calls.clear()
        self.run_spawn(["CLAUDE_NOT_READY", "STARTED"])  # that machine is replaced
        self.assertEqual([c for c in self.calls if c.startswith(("universe", "start", "delete"))],
                         ["universe sb-1", "start sb-1", "delete sb-1", "universe sb-2", "start sb-2"])


if __name__ == "__main__":
    unittest.main()
