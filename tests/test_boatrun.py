"""Review machines: the payload (resume and readiness scripts), waiting for Claude Code, and no leaked machines."""

import asyncio
import json
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from server import boatrun


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "attempt"
        for sub in ("task_inputs", "cache", "home", "logs"):
            (self.root / sub).mkdir(parents=True)
        (self.tmp / "bundle.tgz").write_bytes(b"x")
        patches = [mock.patch.object(boatrun.workspace, "attempt_dir", lambda a: self.root),
                   mock.patch.object(boatrun, "_bundle", lambda: self.tmp / "bundle.tgz")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def files(self, args, extra=None):
        payload = boatrun.build_payload("run1", "att1", "do /prompt", args, {"X": "1"}, extra)
        with tarfile.open(payload) as tar:
            return {name: tar.extractfile(name).read().decode() for name in tar.getnames()
                    if not name.endswith(".tgz")}  # bundle.tgz / state.tgz are binary

    def test_review_resumes_the_same_session_with_the_same_settings(self):
        files = self.files(["claude", "-p", "--append-system-prompt", "guard text", "--session-id", "S-1"])
        resume = files["resume.sh"]
        self.assertIn("--resume S-1", resume)
        self.assertNotIn("--session-id", resume)
        self.assertIn("'guard text'", resume)
        self.assertIn("resume_prompt.txt", resume)
        self.assertEqual(files["resume_prompt.txt"], boatrun.RESUME_PROMPT)
        self.assertIn("exec claude --version", files["check.sh"])
        self.assertIn("review.sh", files)

    def test_load_test_has_no_resume_or_check(self):
        files = self.files(["bash", "/tmp/payload/fake_review.sh"], {"fake_review.sh": "true"})
        self.assertNotIn("resume.sh", files)
        self.assertNotIn("check.sh", files)
        self.assertIn("review.sh", files)

    OLD = "0912a4d4-77e2-4ef4-a57a-6c45d00222fd"
    KEEP = "204694c4-3e28-4504-9746-c90dd90f2157"

    def chat_home(self):
        proj = self.root / "home/.claude/projects/-work"
        (proj / self.KEEP / "subagents").mkdir(parents=True)
        (proj / self.OLD / "subagents").mkdir(parents=True)
        for sid in (self.OLD, self.KEEP):
            (proj / f"{sid}.jsonl").write_text(sid)
            (proj / sid / "subagents" / "a.jsonl").write_text("sub")
        (proj / "memory").mkdir()
        (proj / "memory" / "notes.md").write_text("kept")
        (self.root / "home/.bashrc").write_text("rc")

    def uploaded_home(self, args):
        payload = boatrun.build_payload("run1", "att1", "do /prompt", args, {})
        with tarfile.open(payload) as outer:
            state = outer.extractfile("state.tgz").read()
        out = self.tmp / "state.tgz"
        out.write_bytes(state)
        with tarfile.open(out) as tar:
            return {n.split("home/runner/", 1)[1] for n in tar.getnames() if n.startswith("home/runner/")}

    def test_fresh_review_uploads_no_past_sessions(self):
        self.chat_home()
        names = self.uploaded_home(["claude", "-p", "--session-id", "11111111-2222-3333-4444-555555555555"])
        self.assertFalse([n for n in names if self.OLD in n or self.KEEP in n])
        self.assertIn(".claude/projects/-work/memory/notes.md", names)
        self.assertIn(".bashrc", names)

    def test_resumed_review_uploads_only_its_own_session(self):
        self.chat_home()
        names = self.uploaded_home(["claude", "-p", "--resume", self.KEEP])
        self.assertIn(f".claude/projects/-work/{self.KEEP}.jsonl", names)
        self.assertIn(f".claude/projects/-work/{self.KEEP}/subagents/a.jsonl", names)
        self.assertFalse([n for n in names if self.OLD in n])

    def test_restore_merges_home_and_replaces_inputs_and_cache(self):
        self.chat_home()
        (self.root / "task_inputs/old.txt").write_text("old")
        (self.root / "cache/old.idx").write_text("old")
        back = self.tmp / "back"
        new_sid = "33333333-4444-5555-6666-777777777777"
        proj = back / "home/runner/.claude/projects/-work"
        proj.mkdir(parents=True)
        (proj / f"{new_sid}.jsonl").write_text("new")
        (proj / f"{self.KEEP}.jsonl").write_text("continued")
        (back / "work/Evals/task_inputs").mkdir(parents=True)
        (back / "work/Evals/task_inputs/new.txt").write_text("new")
        (back / "work/Evals/.cache").mkdir(parents=True)
        tgz = self.tmp / "back.tgz"
        with tarfile.open(tgz, "w:gz") as tar:
            tar.add(str(back / "home"), "home")
            tar.add(str(back / "work"), "work")
        boatrun.restore_state("att1", tgz)
        home = self.root / "home/.claude/projects/-work"
        self.assertEqual((home / f"{self.OLD}.jsonl").read_text(), self.OLD)  # a session the box never got is kept
        self.assertEqual((home / f"{self.KEEP}.jsonl").read_text(), "continued")
        self.assertEqual((home / f"{new_sid}.jsonl").read_text(), "new")
        self.assertEqual((self.root / "home/.bashrc").read_text(), "rc")
        self.assertFalse((self.root / "task_inputs/old.txt").exists())
        self.assertTrue((self.root / "task_inputs/new.txt").exists())
        self.assertFalse((self.root / "cache/old.idx").exists())


class WaitForClaudeTest(unittest.TestCase):
    """The RUN_SH block that waits for Claude Code (boat installs it in the background after boot)."""

    def run_block(self, check_body):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "check.sh").write_text(check_body)
        block = "#" + boatrun.RUN_SH.split("# Boat installs and updates Claude Code")[1].split("rm -f /tmp/out.jsonl")[0]
        block = (block.replace("chown runner:runner $P/check.sh", "true")
                 .replace("timeout 30 sudo -u runner -H bash", "bash").replace("sleep 5", "true"))
        script = f"set -euo pipefail\nP={d}\n{block}echo STARTED\n"
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=d).stdout

    def test_ready_at_once(self):
        self.assertIn("STARTED", self.run_block("exit 0"))

    def test_ready_after_a_few_checks(self):
        self.assertIn("STARTED", self.run_block('n=$(cat c 2>/dev/null || echo 0); n=$((n+1)); echo $n > c; [ $n -ge 3 ]\n'))

    def test_never_ready(self):
        out = self.run_block("echo 'Error: claude native binary not installed.' >&2; exit 1")
        self.assertIn("CLAUDE_NOT_READY", out)
        self.assertNotIn("STARTED", out)

    def test_not_ready_machine_is_not_retried(self):
        calls = []

        async def step():
            calls.append(1)
            raise boatrun.MachineNotReady("not ready")

        with self.assertRaises(boatrun.MachineNotReady):
            asyncio.run(boatrun.retrying("review start", step))
        self.assertEqual(len(calls), 1)  # straight to a new machine


class CreateBoxTest(unittest.TestCase):
    def run_create(self, events):
        calls = []

        async def fake_cli(*args, **kwargs):
            calls.append(args[0])
            if args[0] == "new":
                return (0 if events[-1].get("event") == "ready" else 1), "\n".join(json.dumps(e) for e in events), ""
            return 0, "", ""

        async def no_wait():
            pass

        with mock.patch.object(boatrun, "cli", fake_cli), mock.patch.object(boatrun, "_start_slot", no_wait):
            return asyncio.run(boatrun.create_box()), calls

    def test_half_made_machine_is_deleted(self):
        with self.assertRaisesRegex(RuntimeError, "boat new failed"):
            self.run_create([{"event": "created", "id": "bx_half"},
                             {"event": "error", "error": "sandbox provisioning failed: readiness command did not succeed"}])

    def test_half_made_machine_delete_is_called(self):
        calls = []

        async def fake_cli(*args, **kwargs):
            calls.append(args[0])
            if args[0] == "new":
                return 1, json.dumps({"event": "error", "id": "bx_half", "error": "readiness command did not succeed"}), ""
            return 0, "", ""

        async def no_wait():
            pass

        with mock.patch.object(boatrun, "cli", fake_cli), mock.patch.object(boatrun, "_start_slot", no_wait):
            with self.assertRaises(RuntimeError):
                asyncio.run(boatrun.create_box())
        self.assertEqual(calls, ["new", "delete"])

    def test_ready_machine_is_returned(self):
        box, calls = self.run_create([{"event": "created", "id": "bx_ok"}, {"event": "ready"}])
        self.assertEqual((box, calls), ("bx_ok", ["new"]))


if __name__ == "__main__":
    unittest.main()
