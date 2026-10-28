"""The metering proxy as its own process: it knows a run's token from the runs table, only while the run lives."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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
