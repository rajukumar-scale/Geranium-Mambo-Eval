"""REVIEW_SH: a review whose model connection drops mid-reply, is refused or can't resolve the proxy is resumed on
the same machine, up to 3 times."""

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
REFUSE = "API Error: Connection refused — a firewall or proxy may be blocking it (ECONNREFUSED)"
REFUSED = [INIT, '{"type":"result","is_error":true,"result":"%s"}' % REFUSE]
NOT_FOUND = [INIT, '{"type":"result","is_error":true,'
                   '"result":"API Error: Can\'t reach the API server — check your internet or DNS (ENOTFOUND)"}']
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
              .replace("sudo -u runner -H ", "").replace("sleep $((60 * n))", "true"))
        (d / "review.sh").write_text(sh)
        subprocess.run(["bash", str(d / "review.sh")], check=True)
        return out.read_text().splitlines(), done.read_text().strip(), err.read_text() if err.exists() else ""

    def test_dropped_connection_resumes_once(self):
        lines, rc, err = self.run_review(DROPPED)
        self.assertEqual((len(lines), rc), (5, "0"))
        self.assertIn("resuming", err)

    def test_refused_connection_resumes(self):
        lines, rc, err = self.run_review(REFUSED)
        self.assertEqual((len(lines), rc), (4, "0"))
        self.assertIn("resuming the session (1 of 3)", err)

    def test_unresolvable_proxy_resumes(self):
        lines, rc, err = self.run_review(NOT_FOUND)
        self.assertEqual((len(lines), rc), (4, "0"))
        self.assertIn("resuming the session (1 of 3)", err)

    def test_keeps_failing_stops_after_three_resumes(self):
        lines, rc, err = self.run_review(DROPPED, second=REFUSED, second_rc=1)
        self.assertEqual((len(lines), rc), (3 + 3 * 2, "1"))
        self.assertIn("(3 of 3)", err)
        self.assertNotIn("(4 of 3)", err)

    def test_other_api_error_is_not_resumed(self):
        # a request the API refused (4xx) is not temporary; 5xx server errors are resumed since 2026-11-02
        lines, rc, err = self.run_review([INIT, '{"type":"result","is_error":true,"result":"API Error: 400 invalid request"}'])
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
