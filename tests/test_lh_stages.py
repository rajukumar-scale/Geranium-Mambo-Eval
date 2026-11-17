"""Phase 1 Long Horizon stage registry, aliases, single-universe clock wiring, and JS hyphen-command guards."""

import json
import pathlib
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, workspace

ROOT = pathlib.Path(__file__).resolve().parent.parent


class LhStageRegistryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for p in (
            mock.patch.object(config, "DATA_DIR", self.tmp),
            mock.patch.object(db, "_conn", None),
            mock.patch.object(portal.blocks, "disabled", lambda *a: None),
            mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None),
            mock.patch.object(portal.assignment, "schedule", lambda *a: None),
            mock.patch.object(portal.assignment, "ensure_safe", lambda *a: _noop()),
            mock.patch.object(portal.plan, "maybe_kickoff", lambda *a: None),
            mock.patch.object(portal.spend, "maybe_notice", lambda *a: None),
            mock.patch.object(portal.RUNNER, "submit", lambda *a: None),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, created_at, last_activity) values (?,?,?,?,?,?)",
              ("a1", "t1", "u1", "a1@x.com", now, now))
        (self.tmp / "attempts" / "a1" / "logs").mkdir(parents=True)

    def client(self):
        c = TestClient(portal.app)
        c.cookies.set(auth.USER_COOKIE, auth.sign({"attempt_id": "a1", "user_id": "u1", "iat": time.time()}))
        return c

    def test_me_exposes_six_lh_stages_and_no_date_picker(self):
        res = self.client().get("/api/me")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertFalse(data["universe"]["asks"])
        self.assertEqual([s["id"] for s in data["stages"]],
                         ["injection", "prompt", "rubric", "stumping", "all-fail-validation", "reference-notes"])
        self.assertEqual(data["final_stage"], "reference-notes")
        self.assertEqual(data["aliases"]["refnotes"], "reference-notes")
        self.assertEqual(data["aliases"]["af"], "all-fail-validation")

    def test_send_canonicalizes_aliases_and_rejects_removed_base_commands(self):
        c = self.client()
        for typed, canonical in (("/refnotes", "reference-notes"), ("/all-fail", "all-fail-validation"),
                                ("/af check", "all-fail-validation")):
            r = c.post("/api/send", json={"text": typed, "files": []})
            self.assertEqual(r.status_code, 200, r.text)
            run = r.json()["run"]
            self.assertEqual(run["command"], canonical)
            db.ex("delete from runs where id=?", (run["id"],))
        for old_cmd in ("/oe", "/hardness", "/bogus"):
            r = c.post("/api/send", json={"text": old_cmd, "files": []})
            self.assertEqual(r.status_code, 400, old_cmd)
            self.assertIn("/reference-notes", r.json()["detail"])

    def test_workspace_stage_map_and_single_universe_setting(self):
        self.assertFalse(workspace.dated_release())
        self.assertEqual(workspace.STAGE_BY_FILE, {
            "0_injection.md": "injection",
            "1_prompt.md": "prompt",
            "2_rubric.md": "rubric",
            "3_stumping.md": "stumping",
            "4_all_fail.md": "all-fail-validation",
            "5_refnotes.md": "reference-notes",
        })
        self.assertEqual(config.universe_dirs(), ["Universe_Sonorra"])

    def test_web_app_js_handles_hyphenated_commands(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        # Ensure no bare \w+ or [a-z]* command regexes remain that would truncate /all-fail-validation or /reference-notes.
        self.assertNotRegex(js, r"replace\(/\^\\\/\\w\+\\s\*/")
        self.assertNotRegex(js, r"match\(/\^\\\/\(\[a-z\]\*\)\$/i\)")
        for cmd in ("injection", "prompt", "rubric", "stumping", "all-fail-validation", "reference-notes"):
            self.assertIn(cmd, js)


async def _noop():
    return None


if __name__ == "__main__":
    unittest.main()
