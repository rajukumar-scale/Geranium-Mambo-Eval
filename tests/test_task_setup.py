"""Tests for Geranium Mambo 3-dropdown Task Setup, GUARD, prompts, and guidelines (Phase 3)."""

import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, plan, runner, workspace

REAL_BUNDLE = Path(__file__).resolve().parents[2] / "New-Evals-Bundle"


def _ensure_bundle(base: Path):
    if REAL_BUNDLE.is_dir():
        return REAL_BUNDLE
    roster = {"personas": [
        {"name": f"Persona {i}", "email": ("yusuf@sonorra.com" if i == 1 else f"p{i}@sonorra.com"),
         "canonical_job_title": "VP of Engineering" if i == 1 else "Engineer"}
        for i in range(1, 16)
    ]}
    rpath = base / "Evals/references/persona/roster.json"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    rpath.write_text(json.dumps(roster))
    tpath = base / "Evals/tools/hg_store_input.py"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text('''import hashlib, json
from pathlib import Path
REPOSITORY = Path(".")
def save_input(key, sources=None, content=None, origin=None):
    dst = REPOSITORY / "Evals/task_inputs/00_Task_Assigned_Inputs.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(content)
    idx = dst.parent / "inputs.json"
    data = json.loads(idx.read_text()) if idx.is_file() else {"format_version": 1, "revision": 0, "inputs": {}}
    data["revision"] += 1
    data["inputs"][key] = {"state": "saved", "revision": data["revision"], "files": [{
        "filename": dst.name, "path": "Evals/task_inputs/" + dst.name,
        "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
    }]}
    idx.write_text(json.dumps(data))
    return data["inputs"][key]
''')
    return base


class TaskSetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        bundle = _ensure_bundle(self.tmp / "bundle")
        patches = [
            mock.patch.object(config, "DATA_DIR", self.tmp / "data"),
            mock.patch.object(config, "EVAL_BASE_DIR", bundle),
            mock.patch.object(config, "RUNNER_MODE", "local"),
            mock.patch.object(workspace, "_ROSTER_CACHE", None),
            mock.patch.object(db, "_conn", None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("att1", "t1", "u1", "cb@outlier.ai", now, now))

    def _client(self):
        client = TestClient(portal.app)
        client.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "att1", "user_id": "u1", "iat": time.time()}))
        return client

    def test_me_exposes_15_personas_and_options(self):
        with mock.patch.object(portal.blocks, "disabled", lambda *a: None), \
                mock.patch.object(portal.assignment, "schedule", lambda *a: False):
            res = self._client().get("/api/me")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertFalse(data["universe"]["asks"])
        self.assertIsNone(data["task_setup"])
        opts = data["task_setup_options"]
        self.assertEqual(len(opts["personas"]), 15)
        self.assertEqual(len(opts["families"]), 4)
        self.assertEqual([c["id"] for c in opts["constraint_counts"]], ["Low", "High"])

    def test_save_and_read_task_setup(self):
        with mock.patch.object(portal.blocks, "disabled", lambda *a: None), \
                mock.patch.object(portal.assignment, "schedule", lambda *a: False):
            c = self._client()
            bad = c.post("/api/task-setup", json={
                "persona_email": "unknown@sonorra.com",
                "family": "Enterprise ops & connectors",
                "constraint_count": "Low",
            })
            self.assertEqual(bad.status_code, 400)

            res = c.post("/api/task-setup", json={
                "persona_email": "yusuf@sonorra.com",
                "family": "Enterprise ops & connectors",
                "constraint_count": "High",
            })
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["task_setup"]["persona_email"], "yusuf@sonorra.com")
            self.assertEqual(body["task_setup"]["family"], "Enterprise ops & connectors")
            self.assertEqual(body["task_setup"]["constraint_count"], "High")

            me = c.get("/api/me").json()
            self.assertEqual(me["task_setup"]["persona_email"], "yusuf@sonorra.com")
            saved_md = (workspace.task_inputs("att1") / "00_Task_Assigned_Inputs.md").read_text()
            self.assertIn("yusuf@sonorra.com", saved_md)
            self.assertIn("20+ constraints & 20+ rubric criteria; no upper limit", saved_md)


class PromptAndGuardTests(unittest.TestCase):
    def test_no_placeholders_and_no_hg_refs(self):
        self.assertEqual(plan.placeholders(), [])
        for bad in ("HarmonyGames", "Snowflake", "Confluence"):
            self.assertNotIn(bad, runner.GUARD)
            self.assertNotIn(bad, portal.HELP)
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_])/(?:hardness|oe)\b", runner.GUARD))
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_])/(?:hardness|oe)\b", portal.HELP))
        self.assertIn("2026-08-24T16:41:00-05:00", runner.GUARD)
        self.assertIn("/all-fail-validation", runner.GUARD)
        self.assertIn("/reference-notes", runner.GUARD)


if __name__ == "__main__":
    unittest.main()
