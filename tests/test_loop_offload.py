"""Database work stays off the event loop (2026-11-09): with boat's background snapshots the disk is often slow, and
every synchronous db read or write on the loop froze the whole portal ("Eval Portal unavailable", up to 53 s). These
were the call sites in the saved stall reports; each now runs in a worker thread."""

import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"


class OffloadTest(unittest.TestCase):
    def assert_in(self, file, text):
        self.assertIn(text, (SERVER / file).read_text(), f"{file}: {text}")

    def test_the_stall_report_call_sites_run_in_threads(self):
        self.assert_in("runner.py", "for run in await asyncio.to_thread(self._orphans):")
        self.assert_in("runner.py", "cost = await asyncio.to_thread(run_cost, run, cc_cost)")
        self.assert_in("proxy.py", "run = await asyncio.to_thread(db.get_run, run_id)")
        self.assertEqual((SERVER / "proxy.py").read_text().count("await asyncio.to_thread(_record,"), 3)
        self.assert_in("status.py", "await asyncio.to_thread(beat)")
        self.assert_in("budget.py", "await asyncio.to_thread(daily, day_rows, data)")
        self.assert_in("cost.py", "data = await asyncio.to_thread(build, ")
        self.assert_in("cost.py", "await asyncio.to_thread(_save)")
        self.assert_in("app.py", "same = await asyncio.to_thread(rerun_guard.same_as_last, attempt_id, command)")

    def test_no_bare_record_call_left_in_async_code(self):
        src = (SERVER / "proxy.py").read_text()
        bare = [line for line in src.splitlines() if line.strip().startswith("_record(")]
        self.assertEqual(bare, [])


if __name__ == "__main__":
    unittest.main()
