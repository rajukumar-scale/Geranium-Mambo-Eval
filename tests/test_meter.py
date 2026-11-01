"""The metering proxy as its own process: it knows a run's token from the runs table, only while the run lives."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from server import config, db, main, proxy


class MeterTokenTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, name, value in ((config, "DATA_DIR", Path(tmp.name)), (db, "_conn", None),
                                    (proxy, "ACTIVE_TOKENS", {})):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_row(self, run_id, status, token, error=None):
        db.ex("insert into runs(id, attempt_id, kind, status, error, proxy_token, created_at) values (?,?,?,?,?,?,?)",
              (run_id, "a1", "review", status, error, token, time.time()))

    def test_token_from_the_runs_table(self):
        self.run_row("r1", "running", "evp-live")
        self.run_row("r2", "queued", "evp-queued")
        self.run_row("r3", "done", "evp-done")
        self.run_row("r4", "running", "evp-cancelling", error=proxy.CANCELLED)
        self.run_row("r5", "running", None)
        self.assertEqual(proxy.run_for("evp-live"), "r1")
        self.assertEqual(proxy.run_for("evp-queued"), "r2")
        for token in ("evp-done", "evp-cancelling", "evp-unknown", "", None):
            self.assertIsNone(proxy.run_for(token), token)

    def test_same_process_shortcut(self):
        proxy.ACTIVE_TOKENS["evp-mem"] = "r9"
        self.assertEqual(proxy.run_for("evp-mem"), "r9")

    def test_unknown_token_refused_and_health(self):
        client = TestClient(proxy.app)
        r = client.post("/v1/messages", headers={"authorization": "Bearer evp-nope"}, json={"model": "x"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(client.get("/__meter_health").json(), {"ok": True})

    def test_cancelled_marker_matches_runner(self):
        from server import runner
        self.assertEqual(proxy.CANCELLED, runner.CANCELLED)


class BudgetFallbackTest(unittest.TestCase):
    """2026-11-01: when the main key's budget is spent, calls go through the backup key, and back once it works."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.main_over = True
        self.seen, self.slack = [], []

        def upstream(request):
            key = request.headers["authorization"].split()[-1]
            self.seen.append(key)
            if key == "sk-main" and self.main_over:
                return httpx.Response(400, json={"error": {"message": "Budget has been exceeded! Current cost: 501, Max budget: 500",
                                                           "type": "budget_exceeded", "code": "400"}})
            if key == "sk-main" and request.headers.get("x-test") == "bad":
                return httpx.Response(400, json={"error": {"message": "messages: field required", "type": "invalid_request_error"}})
            return httpx.Response(200, json={"id": "m", "type": "message", "content": [], "usage": {"input_tokens": 3, "output_tokens": 1}})

        for target, name, value in ((config, "DATA_DIR", Path(tmp.name)), (db, "_conn", None), (proxy, "ACTIVE_TOKENS", {}),
                                    (config, "LITELLM_API_KEY", "sk-main"), (config, "LITELLM_FALLBACK_API_KEY", "sk-backup"),
                                    (proxy, "_client", httpx.AsyncClient(transport=httpx.MockTransport(upstream))),
                                    (proxy, "KEY_STATE", {"primary_out_until": 0.0, "on_fallback": False}),
                                    (proxy.notify, "slack", mock.AsyncMock(side_effect=lambda t: self.slack.append(t)))):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db.ex("insert into runs(id, attempt_id, kind, status, proxy_token, created_at) values ('r1','a1','review','running','evp-1',?)",
              (time.time(),))
        self.client = TestClient(proxy.app)

    def call(self, **headers):
        return self.client.post("/v1/messages", headers={"authorization": "Bearer evp-1", **headers},
                                json={"model": "claude-opus-5-5", "messages": []})

    def test_over_budget_main_key_falls_back_then_returns(self):
        self.assertEqual(self.call().status_code, 200)  # the review never sees the budget error
        self.assertEqual(self.seen, ["sk-main", "sk-backup"])
        self.assertEqual(self.call().status_code, 200)  # then straight to the backup until the recheck
        self.assertEqual(self.seen[2:], ["sk-backup"])
        self.main_over = False
        proxy.KEY_STATE["primary_out_until"] = 0.0  # recheck time
        self.assertEqual(self.call().status_code, 200)
        self.assertEqual(self.seen[-1], "sk-main")
        self.assertEqual(len(self.slack), 2)
        self.assertIn("over budget", self.slack[0])
        self.assertIn("works again", self.slack[1])

    def test_other_errors_are_not_retried_on_the_backup(self):
        self.main_over = False
        r = self.call(**{"x-test": "bad"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("field required", r.text)
        self.assertEqual(self.seen, ["sk-main"])

    def test_no_backup_configured_passes_the_error_through(self):
        with mock.patch.object(config, "LITELLM_FALLBACK_API_KEY", ""):
            r = self.call()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.seen, ["sk-main"])


class MainModeTest(unittest.TestCase):
    def test_modes(self):
        started = []

        async def fake_serve(self):
            started.append(self.config.app)

        with mock.patch("uvicorn.Server.serve", fake_serve):
            for mode, apps in (("meter", ["server.proxy:app"]), ("portal", ["server.app:app"]),
                               ("all", ["server.app:app", "server.proxy:app"])):
                started.clear()
                main.asyncio.run(main.serve(mode))
                self.assertEqual(started, apps, mode)


if __name__ == "__main__":
    unittest.main()
