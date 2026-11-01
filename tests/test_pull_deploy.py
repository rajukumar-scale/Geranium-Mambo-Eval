"""deploy/pull_deploy.sh: deploys a new commit on main once, reports each outcome to Slack once, and waits when busy.
Runs the real script against a throwaway local "GitHub" repo, a stand-in Python (records Slack messages, fakes the
test result) and a stand-in deploy step."""

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

FAKE_PYTHON = """#!/bin/bash
if [ "$1" = "-m" ] && [ "$2" = "server.notify_cli" ]; then shift 2; echo "$*" >> "$NOTIFY_LOG"; exit 0; fi
if [ "$1" = "-m" ] && [ "$2" = "unittest" ]; then exit "${FAKE_TESTS_RC:-0}"; fi
exit 0
"""
FAKE_DEPLOY = """#!/bin/bash
case "${FAKE_DEPLOY:-ok}" in
  ok) echo "deployed 20261027T000000Z: 2 running review(s) re-attached, 0 queued run(s) resumed";;
  busy) echo "still 1 busy after 5 min; deploy aborted, nothing changed"; exit 1;;
  rollback) echo "new code failed its health check; rolling back to 20261027T000000Z"; exit 1;;
esac
"""

FAKE_CURL = """#!/bin/bash
# Records each GitHub API call as "METHOD path state description" and answers a new deployment with an id.
method=""; url=""; data=""
while [ $# -gt 0 ]; do case "$1" in -X) method=$2; shift 2;; -d) data=$2; shift 2;; -H|-m) shift 2;; -*) shift;; *) url=$1; shift;; esac; done
path=$(echo "$url" | sed -E 's#^.*/(deployments(/.*)?)$#\\1#')
state=$(echo "$data" | grep -oE '"state":"[a-z_]+"' | cut -d'"' -f4)
ref=$(echo "$data" | grep -oE '"ref":"[0-9a-f]+"' | cut -d'"' -f4)
echo "$method $path ${ref:0:7}${state}" >> "$GITHUB_LOG"
if [ "$path" = "deployments" ]; then n=$(( $(cat "$GITHUB_LOG.n" 2>/dev/null || echo 0) + 1 )); echo $n > "$GITHUB_LOG.n"; echo "{\\"id\\": $n}"; fi
"""


class PullDeployTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app, self.data, self.origin, self.work, self.src = (self.tmp / n for n in ("app", "data", "origin.git", "work", "src"))
        (self.app / ".venv" / "bin").mkdir(parents=True)
        self.data.mkdir()
        (self.app / ".env").write_text(f"PORTAL_DATA_DIR={self.data}\nPUBLIC_BASE_URL=https://portal.example\n")
        curl = self.tmp / "curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        py = self.app / ".venv" / "bin" / "python"
        py.write_text(FAKE_PYTHON)
        py.chmod(0o755)
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git("clone", "-q", str(self.origin), str(self.work))
        (self.work / "deploy").mkdir()
        shutil.copy(ROOT / "deploy" / "run_tests.sh", self.work / "deploy" / "run_tests.sh")
        (self.work / "deploy" / "fake_deploy.sh").write_text(FAKE_DEPLOY)
        self.first = self.commit("first version")
        self.git("clone", "-q", str(self.origin), str(self.src))
        (self.data / "deployed_commit").write_text(self.first + "\n")

    def git(self, *args, cwd=None):
        env = {**os.environ, "GIT_AUTHOR_NAME": "Evan", "GIT_AUTHOR_EMAIL": "e@x", "GIT_COMMITTER_NAME": "Evan",
               "GIT_COMMITTER_EMAIL": "e@x"}
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        (self.work / "change.txt").write_text(message)
        self.git("add", "-A", cwd=self.work)
        self.git("commit", "-q", "-m", message, cwd=self.work)
        self.git("push", "-q", "origin", "HEAD:main", cwd=self.work)
        return self.git("rev-parse", "HEAD", cwd=self.work)

    def pull(self, tests_rc=0, deploy="ok"):
        env = {**os.environ, "EVP_APP": str(self.app), "EVP_SRC": str(self.src), "EVP_KEY": str(self.tmp / "nokey"),
               "EVP_LOCK": str(self.tmp / "lock"), "EVP_DEPLOY": "fake_deploy.sh", "NOTIFY_LOG": str(self.tmp / "slack"),
               "FAKE_TESTS_RC": str(tests_rc), "FAKE_DEPLOY": deploy, "EVP_CURL": str(self.tmp / "curl"),
               "GITHUB_LOG": str(self.tmp / "github")}
        return subprocess.run(["bash", str(ROOT / "deploy" / "pull_deploy.sh")], env=env, capture_output=True, text=True)

    def slack(self):
        path = self.tmp / "slack"
        return path.read_text().splitlines() if path.exists() else []

    def github(self):
        path = self.tmp / "github"
        return path.read_text().splitlines() if path.exists() else []

    def with_token(self):
        with open(self.app / ".env", "a") as env:
            env.write("GITHUB_DEPLOY_TOKEN=github_pat_test\n")

    def merge_pr(self, message):
        """A PR merged with a merge commit; returns (merge commit, PR head)."""
        self.git("checkout", "-q", "-b", "pr", cwd=self.work)
        (self.work / "pr.txt").write_text(message)
        self.git("add", "-A", cwd=self.work)
        self.git("commit", "-q", "-m", message, cwd=self.work)
        head = self.git("rev-parse", "HEAD", cwd=self.work)
        self.git("checkout", "-q", "main", cwd=self.work)
        self.git("merge", "-q", "--no-ff", "-m", f"Merge pull request #1 from pr", "pr", cwd=self.work)
        self.git("branch", "-q", "-D", "pr", cwd=self.work)
        self.git("push", "-q", "origin", "HEAD:main", cwd=self.work)
        return self.git("rev-parse", "HEAD", cwd=self.work), head

    def deployed(self):
        return (self.data / "deployed_commit").read_text().strip()

    def test_nothing_new_does_nothing(self):
        self.assertEqual(self.pull().returncode, 0)
        self.assertEqual(self.slack(), [])

    def test_new_commit_is_deployed_and_reported_once(self):
        new = self.commit("Review machines: wait for Claude Code")
        self.assertEqual(self.pull().returncode, 0)
        self.assertEqual(self.deployed(), new)
        self.assertEqual(self.slack(), [f'✅ Portal updated: {new[:7]} "Review machines: wait for Claude Code" (Evan). '
                                        '2 running review(s) kept going.'])
        self.pull()
        self.assertEqual(len(self.slack()), 1)

    def test_several_merges_are_one_deploy(self):
        self.commit("one")
        self.commit("two")
        new = self.commit("three")
        self.pull()
        self.assertEqual(self.deployed(), new)
        self.assertIn(f'3 changes, latest {new[:7]} "three"', self.slack()[0])

    def test_failing_tests_block_the_deploy_and_report_once(self):
        new = self.commit("broken change")
        self.assertEqual(self.pull(tests_rc=1).returncode, 1)
        self.assertEqual(self.deployed(), self.first)
        msg = self.slack()[0]
        self.assertTrue(msg.startswith(f'❌ Portal NOT updated: tests failed on {new[:7]} "broken change"'), msg)
        self.assertIn(f"Live is still on {self.first[:7]}", msg)
        self.assertEqual(self.pull(tests_rc=1).returncode, 0)  # same commit: skipped, not reported again
        self.assertEqual(len(self.slack()), 1)
        fixed = self.commit("fix")
        self.pull()
        self.assertEqual(self.deployed(), fixed)
        self.assertFalse((self.data / "failed_commit").exists())

    def test_rollback_is_reported(self):
        new = self.commit("bad restart")
        self.pull(deploy="rollback")
        self.assertEqual(self.deployed(), self.first)
        msg = self.slack()[0]
        self.assertTrue(msg.startswith(f'⚠️ Portal update rolled back: {new[:7]} "bad restart"'), msg)
        self.assertIn(f"previous version ({self.first[:7]}) was put back", msg)

    def test_busy_waits_quietly_and_retries_after_a_backoff(self):
        new = self.commit("while busy")
        self.assertEqual(self.pull(deploy="busy").returncode, 0)
        self.assertEqual((self.deployed(), self.slack()), (self.first, []))
        self.assertFalse((self.data / "failed_commit").exists())
        self.pull()  # within the 30 minutes: not tried again, so CBs aren't held every minute
        self.assertEqual(self.deployed(), self.first)
        (self.data / "deploy_backoff").write_text("0\n")  # the 30 minutes are up
        self.pull()
        self.assertEqual(self.deployed(), new)
        self.assertFalse((self.data / "deploy_backoff").exists())


    def test_no_token_no_github_calls(self):
        self.commit("quiet")
        self.pull()
        self.assertEqual(self.github(), [])

    def test_github_shows_the_merge_and_the_pr_as_live(self):
        self.with_token()
        merge, head = self.merge_pr("Rubric progress")
        self.assertEqual(self.pull().returncode, 0)
        self.assertEqual(self.deployed(), merge)
        calls = self.github()
        self.assertIn(f"POST deployments {merge[:7]}", calls)
        self.assertIn(f"POST deployments {head[:7]}", calls)  # so the PR page shows it
        self.assertEqual([c for c in calls if c.endswith("in_progress")], ["POST deployments/1/statuses in_progress",
                                                                           "POST deployments/2/statuses in_progress"])
        self.assertEqual(sorted(c for c in calls if c.endswith("success")), ["POST deployments/1/statuses success",
                                                                             "POST deployments/2/statuses success"])
        self.assertEqual(list(self.data.glob("github_deployment_*")), [])

    def test_github_hears_busy_then_live_on_the_same_deployment(self):
        self.with_token()
        new = self.commit("while busy")
        self.pull(deploy="busy")
        self.assertEqual(self.github()[-1], "POST deployments/1/statuses queued")
        (self.data / "deploy_backoff").write_text("0\n")
        self.pull()
        self.assertEqual(self.deployed(), new)
        self.assertEqual(sum(c.startswith("POST deployments ") for c in self.github()), 1)  # one deployment, reused
        self.assertEqual(self.github()[-1], "POST deployments/1/statuses success")

    def test_github_hears_failures_and_the_replacement_clears_them(self):
        self.with_token()
        self.commit("broken change")
        self.pull(tests_rc=1)
        self.assertEqual(self.github()[-1], "POST deployments/1/statuses failure")
        self.commit("fix")
        self.pull()
        calls = self.github()
        self.assertIn("POST deployments/2/statuses success", calls)
        self.assertEqual(calls[-1], "POST deployments/1/statuses inactive")  # replaced by the deploy that went live

    def test_rollback_and_deploy_errors_reach_github(self):
        self.with_token()
        self.commit("bad restart")
        self.pull(deploy="rollback")
        self.assertEqual(self.github()[-1], "POST deployments/1/statuses failure")


if __name__ == "__main__":
    unittest.main()
