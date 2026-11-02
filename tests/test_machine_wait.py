"""Waiting for a review machine (2026-11-02): a cancel stops the wait at once (the machine goes to the pool), the CB
hears progress while boat retries or keeps booting, and 6 warm machines wait during busy hours."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import boatrun, boxpool, config, db, runner


class MachineWaitTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None),
                  mock.patch.object(boxpool, "_boxes", {}), mock.patch.object(boxpool, "_idle", []),
                  mock.patch.object(boxpool, "_lock", None), mock.patch.object(config, "BOX_REUSE_MAX", 15)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        db.ex("insert into runs(id, attempt_id, kind, command, status, created_at) values ('r1','a1','review','rubric','running',?)",
              (time.time(),))
        self.r = runner.Runner()
        self.activity = []
        self.r._push_activity = lambda run_id, attempt, text: self.activity.append(text)
        self.ssh = []

        async def ssh(box, command, timeout=300):
            self.ssh.append((box, command))
            return 0, "WARM"
        p = mock.patch.object(boatrun, "ssh", ssh)
        p.start()
        self.addCleanup(p.stop)

    def test_a_cancel_during_a_slow_boot_ends_the_wait_at_once_and_the_machine_is_kept(self):
        async def slow_acquire(progress=None):
            await asyncio.sleep(3)  # boat still booting
            return "bx_late"

        async def scenario():
            with mock.patch.object(boxpool, "acquire", slow_acquire):
                waiting = asyncio.ensure_future(self.r._acquire_machine("r1", "a1"))
                await asyncio.sleep(0.5)
                db.update("runs", "id", "r1", error=runner.CANCELLED)
                t0 = time.time()
                with self.assertRaises(RuntimeError):
                    await waiting
                self.assertLess(time.time() - t0, 1.6)  # not the rest of the boot
                await asyncio.sleep(3.2)  # the boot finishes: into the pool, clean
        asyncio.run(scenario())
        self.assertEqual(boxpool._idle, ["bx_late"])
        self.assertIn(("bx_late", "sudo touch /tmp/evp_clean && echo WARM"), self.ssh)

    def test_the_wait_between_machine_tries_stops_on_a_cancel(self):
        async def scenario():
            waiting = asyncio.ensure_future(self.r._wait_unless_cancelled("r1", 60))
            await asyncio.sleep(0.3)
            db.update("runs", "id", "r1", error=runner.CANCELLED)
            t0 = time.time()
            with self.assertRaises(RuntimeError):
                await waiting
            return time.time() - t0
        self.assertLess(asyncio.run(scenario()), 1.6)

    def test_the_cb_hears_that_boat_is_still_starting_the_machine(self):
        async def slow_acquire(progress=None):
            progress("↺ boat is busy and didn't start the machine; trying again in 15 s (1 of 10)")
            await asyncio.sleep(2.5)
            return "bx_1"
        with mock.patch.object(boxpool, "acquire", slow_acquire), mock.patch.object(runner.time, "time",
                                                                                    side_effect=_clock(step=40)):
            box = asyncio.run(self.r._acquire_machine("r1", "a1"))
        self.assertEqual(box, "bx_1")
        self.assertIn("boat is busy", self.activity[0])
        self.assertTrue(any(a.startswith("⏳ Still waiting for boat to start the machine (1 min)") for a in self.activity))

    def test_boat_refusals_are_reported_to_the_progress_callback(self):
        calls = {"n": 0}

        async def cli(*args, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return 1, '{"event":"error","error":"Rate limit hit: 30 starts per minute"}', ""
            return 0, '{"id":"bx_9"}\n{"event":"ready","id":"bx_9"}', ""
        told = []

        async def no_wait(*a):
            return None
        with mock.patch.object(boatrun, "cli", cli), mock.patch.object(boatrun, "_start_slot", no_wait), \
                mock.patch.object(boatrun.asyncio, "sleep", no_wait):
            box = asyncio.run(boatrun.create_box(progress=told.append))
        self.assertEqual(box, "bx_9")
        self.assertEqual(len(told), 1)
        self.assertIn("trying again", told[0])

    def test_a_machine_that_cannot_be_kept_is_deleted(self):
        deleted = []

        async def bad_ssh(box, command, timeout=300):
            return 255, ""

        async def delete(box):
            deleted.append(box)
        with mock.patch.object(boatrun, "ssh", bad_ssh), mock.patch.object(boatrun, "delete_box", delete):
            asyncio.run(boxpool.give_back("bx_bad"))
        self.assertEqual(deleted, ["bx_bad"])
        self.assertEqual(boxpool._idle, [])

    def test_six_warm_machines_by_default(self):
        import importlib
        import os
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOX_WARM_MIN", None)
            self.assertEqual(config._int("BOX_WARM_MIN", 6), 6)
        self.assertIn('BOX_WARM_MIN = _int("BOX_WARM_MIN", 6)', Path(config.__file__).read_text())
        del importlib


def _clock(step):
    """time.time() that jumps `step` seconds on every call: a minute passes in a couple of polls."""
    t = {"now": time.time()}

    def tick():
        t["now"] += step
        return t["now"]
    return tick


if __name__ == "__main__":
    unittest.main()
