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

        async def create_box(**kw):
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
            if command.endswith("echo WARM"):
                return (0, "WARM") if self.ready else (1, "")
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
                  mock.patch.object(boxpool, "_lock", None), mock.patch.object(boxpool, "_demand", {"last": 0.0}),
                  mock.patch.object(boxpool, "_warming", set()), mock.patch.object(config, "BOX_WARM_MIN", 3),
                  mock.patch.object(config, "BOX_WARM_BUSY_MIN", 20.0)):
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

    # Warm boxes (2026-11-02): while reviews come in, BOX_WARM_MIN clean boxes wait ready, so a review neither waits
    # on boat's boot nor fails on boat's failed boots.
    def tick_and_settle(self, now=None):
        async def go():
            await boxpool.tick(now)
            while boxpool._warming:
                await asyncio.gather(*list(boxpool._warming))
        self.run_(go())

    def test_no_warm_boxes_while_nobody_is_reviewing(self):
        self.tick_and_settle()
        self.assertEqual(self.created, [])

    def test_busy_hours_keep_the_warm_minimum_ready_and_a_review_takes_one(self):
        first = self.run_(boxpool.acquire())  # a review asked for a box: reviews are coming in
        for _ in range(3):
            self.tick_and_settle()  # at most 2 start per pass
        self.assertEqual(len(boxpool._idle), 3)
        warm = list(boxpool._idle)
        self.assertTrue(all(boxpool._boxes[b]["warm"] for b in warm))
        self.assertTrue(any(c == "sudo touch /tmp/evp_clean && echo WARM" for b, c in self.ssh_calls if b in warm))
        made = len(self.created)
        got = self.run_(boxpool.acquire())
        self.assertIn(got, warm)  # no new start for this review
        self.assertEqual(len(self.created), made)
        self.tick_and_settle()  # topped back up
        self.assertEqual(len(boxpool._idle), 3)
        self.assertNotIn(first, self.deleted)

    def test_a_failed_warm_boot_costs_no_review_and_leaks_no_box(self):
        self.run_(boxpool.acquire())
        self.ready = False  # boat made the box but it never answers
        self.tick_and_settle()
        self.assertEqual(boxpool._idle, [])
        self.assertEqual(sorted(self.deleted), sorted(self.created[1:]))
        self.assertEqual(boxpool._stats["warm_failed"] >= 2, True)

        async def boom():
            raise RuntimeError("boat new failed: readiness command did not succeed")
        with mock.patch.object(boxpool.boatrun, "create_box", boom):
            self.tick_and_settle()  # logged only
        self.assertEqual(boxpool._idle, [])

    def test_idle_boxes_beyond_the_minimum_go_and_all_go_when_reviews_stop(self):
        import time as _t
        now = _t.time()
        boxes = [self.run_(boxpool.acquire()) for _ in range(5)]
        for b in boxes:
            self.run_(boxpool.release(b, clean=True))
        for i, b in enumerate(boxes):
            boxpool._boxes[b]["idle_since"] = now - 600 - i  # all idle past BOX_IDLE_MIN
        self.tick_and_settle(now)
        self.assertEqual(len(boxpool._idle), 3)  # busy: the 3 newest stay
        self.assertEqual(sorted(self.deleted), sorted(boxes[3:]))
        later = now + 21 * 60  # no review for over BOX_WARM_BUSY_MIN
        self.tick_and_settle(later)
        self.assertEqual(boxpool._idle, [])
        self.assertEqual(sorted(self.deleted), sorted(boxes))

    def test_a_kept_warm_box_on_an_old_snapshot_is_replaced(self):
        self.run_(boxpool.acquire())
        for _ in range(2):
            self.tick_and_settle()
        old = list(boxpool._idle)
        self.snapshot = "snap-2"
        self.tick_and_settle()
        self.assertTrue(set(old) <= set(self.deleted))
        self.assertTrue(all(boxpool._boxes[b]["snapshot"] == "snap-2" for b in boxpool._idle))


if __name__ == "__main__":
    unittest.main()


class ScrubCommandTest(unittest.TestCase):
    def test_the_wipe_never_shows_its_own_pkill_patterns_on_its_command_line(self):
        cmd = boxpool.scrub_command()
        self.assertNotIn("memguard", cmd)
        self.assertNotIn("runner", cmd)
        self.assertTrue(cmd.endswith("| base64 -d | sudo bash"))
