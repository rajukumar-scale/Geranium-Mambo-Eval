"""Old chats' sessions to S3 by hand (archive.py, 2026-11-02): pack -> upload -> link (checked) -> remove, and the
automatic restore when the chat is used again. A local web server stands in for the S3 links, with and without range
requests."""

import http.server
import json
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from server import archive, config, db, workspace


class Files(http.server.SimpleHTTPRequestHandler):
    ranges = True

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = Path(self.directory) / self.path.lstrip("/")
        data = path.read_bytes()
        rng = self.headers.get("Range")
        if rng and self.ranges:
            lo, hi = (int(x) for x in rng.split("=")[1].split("-"))
            body, code = data[lo:hi + 1], 206
        else:
            body, code = data, 200
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        self.web = Path(tmp.name) / "web"
        self.web.mkdir()
        for p in (mock.patch.object(config, "DATA_DIR", self.data), mock.patch.object(db, "_conn", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: db._local.__dict__.clear())
        handler = type("H", (Files,), {"directory": str(self.web)})
        handler.__init__ = lambda s, *a, **k: Files.__init__(s, *a, directory=str(self.web), **k)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.handler = handler
        old = time.time() - 20 * 86400
        for i, attempt in enumerate(("a1", "a2", "a3")):
            db.ex("insert into chats(attempt_id, task_id, user_id, email, session_id, created_at, last_activity) "
                  "values (?,?,?,?,?,?,?)", (attempt, "t", "u", "x@y.z", f"S{i}", old, old if attempt != "a3" else time.time()))
            root = workspace.ensure_attempt(attempt)
            (root / "home" / ".claude" / "session.jsonl").write_text(f"session of {attempt}\n" * 2000)
            (root / "task_inputs" / "reports" / "r.md").write_text("report")
            self.assertGreater(workspace.freeze(attempt), 0)

    def upload(self, bundle, n):
        part = archive.load(bundle)["parts"][n]
        (self.web / part["file"]).write_bytes((archive.root() / bundle / part["file"]).read_bytes())
        return f"http://127.0.0.1:{self.server.server_address[1]}/{part['file']}"

    def test_pack_upload_remove_then_a_used_chat_comes_back_exactly(self):
        bundle = archive.pack(days=14)
        man = archive.load(bundle)
        self.assertEqual(sorted(man["chats"]), ["a1", "a2"])  # a3 is recent
        part = archive.root() / bundle / man["parts"][0]["file"]
        original = (archive.attempt_dir("a1") / "cold.tgz").read_bytes()
        with tarfile.open(part) as tar:
            self.assertEqual(tar.extractfile("a1.tgz").read(), original)  # a plain tar of the chats' cold.tgz
        archive.link(bundle, 0, self.upload(bundle, 0))
        freed = archive.remove(bundle)
        self.assertGreater(freed, 0)
        self.assertFalse(part.exists())
        self.assertTrue(archive.is_archived("a1"))
        self.assertTrue(workspace.is_cold("a1"))
        self.assertTrue((archive.attempt_dir("a1") / "task_inputs" / "reports" / "r.md").exists())  # pages still read it
        workspace.ensure_attempt("a1", unpack=False)  # empty dirs made meanwhile must not be packed over the archive
        self.assertEqual(workspace.freeze("a1"), 0)
        workspace.ensure_attempt("a1")  # a CB writes in it again: restored from the link
        self.assertFalse(archive.is_archived("a1"))
        self.assertIn("session of a1", (archive.attempt_dir("a1") / "home" / ".claude" / "session.jsonl").read_text())

    def test_restore_works_when_the_host_ignores_ranges(self):
        bundle = archive.pack(days=14)
        archive.link(bundle, 0, self.upload(bundle, 0))
        archive.remove(bundle)
        self.handler.ranges = False
        try:
            workspace.thaw("a2")
        finally:
            self.handler.ranges = True
        self.assertIn("session of a2", (archive.attempt_dir("a2") / "home" / ".claude" / "session.jsonl").read_text())

    def test_a_wrong_upload_is_refused_and_nothing_is_removed(self):
        bundle = archive.pack(days=14)
        part = archive.load(bundle)["parts"][0]
        (self.web / part["file"]).write_bytes(b"something else")
        url = f"http://127.0.0.1:{self.server.server_address[1]}/{part['file']}"
        with self.assertRaises(RuntimeError):
            archive.link(bundle, 0, url)
        self.assertEqual(archive.remove(bundle), 0)
        self.assertTrue((archive.attempt_dir("a1") / "cold.tgz").exists())

    def test_a_chat_used_after_packing_stays_on_the_server(self):
        bundle = archive.pack(days=14)
        url = self.upload(bundle, 0)
        workspace.thaw("a1")  # the CB came back before the upload was checked
        with open(archive.attempt_dir("a1") / "home" / ".claude" / "session.jsonl", "a") as f:
            f.write("a new message\n")
        workspace.freeze("a1")  # and left again: a different cold.tgz
        archive.link(bundle, 0, url)
        archive.remove(bundle)
        self.assertFalse(archive.is_archived("a1"))
        self.assertTrue(archive.is_archived("a2"))

    def test_parts_stay_under_the_size_limit(self):
        bundle = archive.pack(days=14, part_gb=1e-9)  # every chat its own part
        man = archive.load(bundle)
        self.assertEqual(len(man["parts"]), 2)
        self.assertEqual({c["part"] for c in man["chats"].values()}, {0, 1})
        for n in (0, 1):
            archive.link(bundle, n, self.upload(bundle, n))
        archive.remove(bundle)
        for a in ("a1", "a2"):
            workspace.thaw(a)
            self.assertIn(f"session of {a}", (archive.attempt_dir(a) / "home" / ".claude" / "session.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
