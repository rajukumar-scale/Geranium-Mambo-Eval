"""REVIEW_SH: a review whose model connection drops mid-reply is resumed once on the same machine."""

import pathlib
import shutil
import subprocess
import tempfile
import unittest

from server import boatrun

INIT = '{"type":"system","subtype":"init"}'
DROP = "API Error: Connection closed mid-response. The response above may be incomplete."
DROPPED = [INIT, '{"type":"assistant","message":{"content":[{"type":"text","text":"%s"}]}}' % DROP,
           '{"type":"result","is_error":true,"result":"%s"}' % DROP]
FINISHED = [INIT, '{"type":"result","is_error":false,"result":"done"}']


def _script(lines, rc):
    return "#!/bin/bash\n" + "".join("printf '%s\\n' '" + line.replace("'", "'\\''") + "'\n" for line in lines) + f"exit {rc}\n"


class ReviewShTest(unittest.TestCase):
    def run_review(self, first, first_rc=1, second=FINISHED, second_rc=0, with_resume=True):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        payload, out, err, done = d / "payload", d / "out.jsonl", d / "err.txt", d / "claude.exit"
        payload.mkdir()
        (payload / "cmd.sh").write_text(_script(first, first_rc))
        if with_resume:
            (payload / "resume.sh").write_text(_script(second, second_rc))
        sh = (boatrun.REVIEW_SH.replace("/tmp/payload", str(payload)).replace("/tmp/out.jsonl", str(out))
              .replace("/tmp/err.txt", str(err)).replace("/tmp/claude.exit", str(done))
              .replace("sudo -u runner -H ", "").replace("sleep 5", "true"))
        (d / "review.sh").write_text(sh)
        subprocess.run(["bash", str(d / "review.sh")], check=True)
        return out.read_text().splitlines(), done.read_text().strip(), err.read_text() if err.exists() else ""

    def test_dropped_connection_resumes_once(self):
        lines, rc, err = self.run_review(DROPPED)
        self.assertEqual((len(lines), rc), (5, "0"))
        self.assertIn("resuming", err)

    def test_second_drop_fails(self):
        lines, rc, _ = self.run_review(DROPPED, second=DROPPED, second_rc=1)
        self.assertEqual((len(lines), rc), (6, "1"))

    def test_other_api_error_is_not_resumed(self):
        lines, rc, err = self.run_review([INIT, '{"type":"result","is_error":true,"result":"API Error: 500 overloaded"}'])
        self.assertEqual((len(lines), rc), (2, "1"))
        self.assertNotIn("resuming", err)

    def test_success_mentioning_the_phrase_is_not_resumed(self):
        ok = [INIT, '{"type":"result","is_error":false,"result":"quoting %s"}' % DROP]
        lines, rc, _ = self.run_review(ok, first_rc=0)
        self.assertEqual((len(lines), rc), (2, "0"))

    def test_no_resume_script_no_resume(self):  # load tests and anything without a Claude session
        lines, rc, _ = self.run_review(DROPPED, with_resume=False)
        self.assertEqual((len(lines), rc), (3, "1"))


if __name__ == "__main__":
    unittest.main()
