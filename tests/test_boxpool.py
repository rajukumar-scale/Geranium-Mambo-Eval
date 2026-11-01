"""Reused review boxes (2026-11-01): a box that ended a review cleanly is wiped and reused; anything else, and any box
past its reuse count, age or snapshot, is deleted; idle boxes go after BOX_IDLE_MIN."""

import asyncio
import base64
import itertools
import re
import unittest
from unittest import mock

from server import boxpool, config


class BoxPoolTest(unittest.TestCase):
    def setUp(self):
        self.created, self.deleted, self.ssh_calls = [], [], []
        self.snapshot, self.wipe_ok, self.ready = "snap-1", True, True
        ids = (f"bx_{i}" for i in itertools.count(1))

        async def create_box():
            box = next(ids)
            self.created.append(box)
            return box

        async def delete_box(box):
            self.deleted.append(box)

        async def ssh(box, command, timeout=300):
            packed = re.match(r"echo (\S+) \| base64 -d \| sudo bash$", command)
            if packed:  # the wipe: decode it so the test can read what it does
                command = base64.b64decode(packed.group(1)).decode()
                self.assertNotIn("memguard", re.sub(r"echo \S+", "", command.split("\n")[0]))
            self.ssh_calls.append((box, command))
            if "evp_clean && echo READY" in command:
                return (0, "READY") if self.ready else (1, "")
            if "SCRUB" in command or "pkill" in command:
                return (0, "CLEAN") if self.wipe_ok else (1, "NOT CLEAN")
            return 0, ""

        async def cli(*args, **kw):
            return 0, "{}", ""

        for p in (mock.patch.object(boxpool.boatrun, "create_box", create_box),
                  mock.patch.object(boxpool.boatrun, "delete_box", delete_box),
                  mock.patch.object(boxpool.boatrun, "ssh", ssh), mock.patch.object(boxpool.boatrun, "cli", cli),
                  mock.patch.object(boxpool.boatrun, "active_snapshot", lambda: self.snapshot),
                  mock.patch.object(config, "BOX_REUSE_MAX", 3), mock.patch.object(config, "BOX_MAX_AGE_H", 8.0),
                  mock.patch.object(config, "BOX_IDLE_MIN", 5.0),
                  mock.patch.object(boxpool, "_boxes", {}), mock.patch.object(boxpool, "_idle", []),
                  mock.patch.object(boxpool, "_lock", None)):
            p.start()
            self.addCleanup(p.stop)

    def run_(self, coro):
        return asyncio.run(coro)

    def test_a_clean_review_leaves_a_wiped_box_for_the_next_one(self):
        box = self.run_(boxpool.acquire())
        self.run_(boxpool.release(box, clean=True))
        wipe = [c for b, c in self.ssh_calls if b == box and "pkill -9 -u runner" in c]
        self.assertEqual(len(wipe), 1)
        for must in ("rm -rf /home/runner", "/tmp/payload", "/work/Evals", "crontab -r -u runner", "touch /tmp/evp_clean"):
            self.assertIn(must, wipe[0])
        self.assertEqual(self.run_(boxpool.acquire()), box)  # reused, no new start
        self.assertEqual(self.created, [box])
        self.assertEqual(self.deleted, [])

    def test_an_unclean_review_or_a_failed_wipe_deletes_the_box(self):
        box = self.run_(boxpool.acquire())
        self.run_(boxpool.release(box, clean=False))
        self.assertEqual(self.deleted, [box])
        box2 = self.run_(boxpool.acquire())
        self.wipe_ok = False
        self.run_(boxpool.release(box2, clean=True))
        self.assertEqual(self.deleted, [box, box2])
        self.assertEqual(boxpool._idle, [])

    def test_reuse_count_and_new_snapshot_retire_the_box(self):
        box = self.run_(boxpool.acquire())
        for _ in range(2):
            self.run_(boxpool.release(box, clean=True))
            self.assertEqual(self.run_(boxpool.acquire()), box)
        self.run_(boxpool.release(box, clean=True))  # its 3rd review: BOX_REUSE_MAX
        self.assertEqual(self.deleted, [box])
        box2 = self.run_(boxpool.acquire())
        self.run_(boxpool.release(box2, clean=True))
        self.snapshot = "snap-2"  # an eval update built a new runner image
        box3 = self.run_(boxpool.acquire())
        self.assertNotEqual(box3, box2)
        self.assertIn(box2, self.deleted)

    def test_a_box_that_stopped_answering_is_replaced(self):
        box = self.run_(boxpool.acquire())
        self.run_(boxpool.release(box, clean=True))
        self.ready = False
        self.assertNotEqual(self.run_(boxpool.acquire()), box)
        self.assertIn(box, self.deleted)

    def test_a_reused_box_is_claimed_in_one_ssh_and_keeps_its_bundle(self):
        box = self.run_(boxpool.acquire())
        self.assertFalse(boxpool.has_bundle(box, "bundle-a.tgz"))
        boxpool.note_bundle(box, "bundle-a.tgz")
        self.run_(boxpool.release(box, clean=True))
        self.ssh_calls.clear()
        self.assertEqual(self.run_(boxpool.acquire()), box)
        self.assertEqual([c for _, c in self.ssh_calls],
                         ["test -e /tmp/evp_clean && sudo rm -f /tmp/evp_clean && echo READY"])
        self.assertTrue(boxpool.has_bundle(box, "bundle-a.tgz"))
        self.assertFalse(boxpool.has_bundle(box, "bundle-b.tgz"))  # an eval update: send the new one
        self.run_(boxpool.release(box, clean=False))
        self.assertFalse(boxpool.has_bundle(box, "bundle-a.tgz"))  # deleted boxes hold nothing

    def test_the_wipe_removes_an_unused_bundle_upload(self):
        box = self.run_(boxpool.acquire())
        self.run_(boxpool.release(box, clean=True))
        wipe = [c for b, c in self.ssh_calls if "pkill -9 -u runner" in c][0]
        self.assertIn("/tmp/evp_bundle.tgz", wipe)
        self.assertNotIn("/var/cache/evp", wipe)  # the cached bundle stays for the next review

    def test_reuse_off_means_a_new_box_every_time(self):
        with mock.patch.object(config, "BOX_REUSE_MAX", 0):
            box = self.run_(boxpool.acquire())
            self.run_(boxpool.release(box, clean=True))
            self.assertEqual(self.deleted, [box])
            self.assertNotEqual(self.run_(boxpool.acquire()), box)


if __name__ == "__main__":
    unittest.main()


class ScrubCommandTest(unittest.TestCase):
    def test_the_wipe_never_shows_its_own_pkill_patterns_on_its_command_line(self):
        cmd = boxpool.scrub_command()
        self.assertNotIn("memguard", cmd)
        self.assertNotIn("runner", cmd)
        self.assertTrue(cmd.endswith("| base64 -d | sudo bash"))
