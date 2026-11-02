"""Riding out a saturated disk (2026-11-02: boat snapshots froze the portal three times in a day): database reads from
memory, WAL checkpoints off the request path, web files from memory, the spend figure reused, and a record of any
stall."""

import asyncio
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from server import config, db, stallwatch


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(config, "DATA_DIR", Path(tmp.name)), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())

    def test_commits_never_checkpoint_and_the_background_checkpoint_does(self):
        db.ex("create table t(x)")
        for i in range(3000):  # well past sqlite's default 1,000-page automatic checkpoint
            db.ex("insert into t values (?)", ("x" * 2000,))
        self.assertEqual(db.conn().execute("pragma wal_autocheckpoint").fetchone()[0], 0)
        wal = config.DATA_DIR / "portal.db-wal"
        self.assertGreater(wal.stat().st_size, 4 * 1024 * 1024)
        busy, pages, done = db.checkpoint_once()
        self.assertEqual(busy, 0)
        self.assertEqual(pages, done)
        self.assertEqual(sqlite3.connect(config.DATA_DIR / "portal.db").execute("select count(*) from t").fetchone()[0], 3000)

    def test_the_database_is_pinned_in_memory_and_follows_its_growth(self):
        db.ex("create table t(x)")
        db.ex("insert into t values ('a')")
        db.checkpoint_once("TRUNCATE")
        pin = db._Pin()
        pin.refresh()
        first = pin.maps["portal.db"][0]
        self.assertEqual(first, (config.DATA_DIR / "portal.db").stat().st_size)
        for i in range(500):
            db.ex("insert into t values (?)", ("y" * 4000,))
        db.checkpoint_once()
        pin.refresh()
        self.assertGreater(pin.maps["portal.db"][0], first)  # re-mapped at the new size
        self.assertEqual(set(pin.locked()) >= {"portal.db"}, True)
        with mock.patch.object(pin.libc, "mlock", lambda a, n: -1):  # no mlock right: still kept warm, no error
            db.ex("insert into t values ('z')")
            db.checkpoint_once()
            pin.refresh()

    def test_reads_get_a_big_cache(self):
        db.ex("create table t(x)")
        with db.reading():
            self.assertEqual(db._local.reader.execute("pragma cache_size").fetchone()[0], -db.READER_CACHE_KB)
        self.assertEqual(db.conn().execute("pragma cache_size").fetchone()[0], -db.CACHE_KB)


class StallWatchTest(unittest.TestCase):
    def test_a_blocked_event_loop_is_written_down_with_every_threads_stack(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DATA_DIR", Path(tmp)), \
                mock.patch.dict(stallwatch._state, {"started": False}), mock.patch.object(stallwatch, "STALL_S", 1.0), \
                mock.patch.dict(os.environ, {"EVP_NO_STALLWATCH": ""}):
            async def scenario():
                stallwatch.start(asyncio.get_running_loop())
                await asyncio.sleep(0.6)
                time.sleep(2.6)  # a synchronous call blocking the loop, like a slow database read
                await asyncio.sleep(0.3)
            asyncio.run(scenario())
            files = list((Path(tmp) / "stalls").glob("*.txt"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text()
            self.assertIn("event loop stalled", text)
            self.assertIn("time.sleep(2.6)", text)  # the main thread's stack shows what blocked it


class WebFromMemoryTest(unittest.TestCase):
    def test_web_files_and_spend_come_from_memory(self):
        from fastapi.testclient import TestClient
        from server import app as portal
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            (web / "x.js").write_text("console.log(1)")
            static = portal.Static(directory=web)
            static._memory = {}
            from starlette.applications import Starlette
            from starlette.routing import Mount
            client = TestClient(Starlette(routes=[Mount("/static", static)]))
            first = client.get("/static/x.js")
            self.assertEqual(first.status_code, 200)
            (web / "x.js").unlink()  # the disk is gone: still served
            again = client.get("/static/x.js")
            self.assertEqual((again.status_code, again.text), (200, "console.log(1)"))
            self.assertEqual(again.headers["content-type"].split(";")[0], "text/javascript")
            cached = client.get("/static/x.js", headers={"if-none-match": first.headers["etag"]})
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(client.get("/static/nope.js").status_code, 404)
            self.assertEqual(client.get("/static/../app.py").status_code, 404)


if __name__ == "__main__":
    unittest.main()
