"""An admin override pass carries the saved inputs like any pass, so a later input change marks it outdated
(attempt 6aed99b3..., 2026-11-08: a /prompt override stayed "ready to use" after the contributor changed the SQL)."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth, config, db, staleness

OWNER = "owner@scale.com"
ATT = "att1"


class OverrideStalenessTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.saved = {"injection": "sql-v1", "prompt": "prompt-v1", "universe": "date-1"}
        patches = [mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                   mock.patch.object(config, "ADMIN_EMAILS", [OWNER]),
                   mock.patch.object(portal.blocks, "disabled", lambda *a: None),
                   mock.patch.object(portal.blocks, "chat_blocked", lambda *a: None),
                   mock.patch.object(staleness, "snapshot", lambda attempt_id: dict(self.saved)),
                   mock.patch.object(portal, "_latest_reports", lambda attempt_id: {"prompt": self.report}),
                   mock.patch.object(portal.fingerprint, "for_attempt", lambda attempt_id, stage: "fp-1")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db._local.__dict__.clear()
        now = time.time()
        db.ex("insert into chats(attempt_id, task_id, user_id, email, admin_open, created_at, last_activity) "
              "values (?,?,?,?,?,?,?)", (ATT, "task1", "user1", "cb@x.com", 0, now, now))
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at, started_at, finished_at) "
              "values (?,?,?,?,?,?,?,?)", ("run1", ATT, "review", "prompt", "done", now, now - 60, now))
        self.report = {"run_id": "run1", "stage": "prompt", "verdict": "NON-FAIL", "score": None,
                       "exec_status": "PARTIAL", "fingerprint": "fp-1", "file": "1_prompt.md"}

    def override(self):
        c = TestClient(portal.app)
        c.cookies.set(auth.ADMIN_COOKIE, auth.sign({"email": OWNER, "iat": time.time()}))
        with mock.patch.object(portal.proofs, "create", wraps=self.fake_create) as create:
            res = c.post(f"/api/admin/chat/{ATT}/override-pass",
                         json={"run_id": "run1", "stage": "prompt", "reason": "admin pass"})
        self.assertEqual(res.status_code, 200, res.text)
        return create.call_args

    def fake_create(self, run, chat, report, override=None, inputs=None):
        payload = {"code": "EVP-TEST", "fingerprint": "fp-1", "stage": report["stage"], "verdict": report["verdict"],
                   "attempt_id": run["attempt_id"], "admin_override": override, "inputs": inputs}
        db.ex("insert into proofs(code, attempt_id, run_id, payload, created_at) values (?,?,?,?,?)",
              (payload["code"], run["attempt_id"], run["id"], portal.json.dumps(payload), time.time()))
        return payload

    def test_override_stores_the_saved_inputs(self):
        call = self.override()
        self.assertEqual(call.kwargs["inputs"], {"injection": "sql-v1", "prompt": "prompt-v1", "universe": "date-1"})

    def test_override_goes_outdated_when_the_sql_changes(self):
        self.override()
        prompt = lambda: next(s for s in staleness.gate(ATT)["stages"] if s["stage"] == "prompt")
        self.assertEqual(prompt()["status"], "current")
        self.saved["injection"] = "sql-v2"
        self.assertEqual(prompt()["status"], "outdated")
        self.assertTrue(prompt()["locked"])


if __name__ == "__main__":
    unittest.main()
