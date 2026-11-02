import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server import dbq


class DbqTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "portal.db"
        c = sqlite3.connect(self.path)
        c.execute("pragma journal_mode=wal")
        c.execute("create table runs(id text primary key, status text)")
        c.execute("insert into runs values ('r1','running')")
        c.commit()
        c.close()

    def status(self):
        return sqlite3.connect(self.path).execute("select status from runs where id='r1'").fetchone()[0]

    def test_read_is_read_only(self):
        out = io.StringIO()
        dbq.run("select id, status from runs", path=self.path, out=out)
        self.assertEqual(out.getvalue().splitlines(), ["id\tstatus", "r1\trunning"])
        with self.assertRaises(sqlite3.OperationalError):
            dbq.run("update runs set status='failed'", path=self.path, out=io.StringIO())
        self.assertEqual(self.status(), "running")

    def test_write_commits(self):
        out = io.StringIO()
        dbq.run("update runs set status='failed' where id='r1' and status='running'", write=True, path=self.path, out=out)
        self.assertEqual(self.status(), "failed")
        self.assertIn("1 row(s) changed", out.getvalue())

    def test_failed_script_rolls_back_whole(self):
        with self.assertRaises(sqlite3.OperationalError):
            dbq.run("update runs set status='failed'; update nope set x=1;", write=True, path=self.path,
                    out=io.StringIO())
        self.assertEqual(self.status(), "running")


if __name__ == "__main__":
    unittest.main()
