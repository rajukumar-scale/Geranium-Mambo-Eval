"""Contributor guidelines: every review workspace gets server/guidelines at work/guidelines, and the assistant is told to use them."""

import pathlib
import shutil
import tarfile
import tempfile
import unittest
from unittest import mock

from server import boatrun, config, runner, workspace

NAMES = {"hardness_v2.md", "tool_call_minimum.md"}


class GuidelinesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.base = self.tmp / "release"
        for d in ("Evals/references", "claude/skills/prompt", ".claude"):
            (self.base / d).mkdir(parents=True)
        (self.base / "Evals/references/x.json").write_text("{}")
        (self.base / "claude/skills/prompt/SKILL.md").write_text("s")
        self.guides = self.tmp / "guides"
        self.guides.mkdir()
        for name in NAMES:
            (self.guides / name).write_text(f"# {name}\n")
        (self.guides / "README.md").write_text("readme")
        patches = [mock.patch.object(config, "EVAL_BASE_DIR", self.base), mock.patch.object(config, "DATA_DIR", self.tmp / "data"),
                   mock.patch.object(config, "GUIDELINES_DIR", self.guides), mock.patch.dict(boatrun._bundle_cache, clear=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def names(self):
        with tarfile.open(boatrun._bundle()) as tar:
            return {n.removeprefix("work/guidelines/") for n in tar.getnames() if n.startswith("work/guidelines/")}

    def test_the_repo_ships_both_guidelines(self):
        real = {p.name for p in (pathlib.Path(__file__).resolve().parent.parent / "server" / "guidelines").glob("*.md")}
        self.assertTrue(NAMES <= real)

    def test_boat_bundle_carries_the_guidelines(self):
        self.assertEqual(self.names(), NAMES | {"README.md"})

    def test_a_guideline_edit_makes_a_new_bundle(self):
        first = boatrun._bundle()
        (self.guides / "hardness_v2.md").write_text("# changed\n")
        second = boatrun._bundle()
        self.assertNotEqual(first, second)
        with tarfile.open(second) as tar:
            self.assertEqual(tar.extractfile("work/guidelines/hardness_v2.md").read(), b"# changed\n")

    def test_docker_mounts_them_read_only(self):
        with mock.patch.object(workspace, "ensure_attempt", lambda a: self.tmp / "attempt"):
            args = workspace.docker_mounts("att1")
        self.assertIn(f"type=bind,source={self.guides},target=/work/guidelines,readonly", args)

    def test_the_assistant_is_pointed_at_them(self):
        for name in NAMES:
            self.assertIn(f"guidelines/{name}", runner.GUARD)
        self.assertIn("never change a review's verdict or score", runner.GUARD)


if __name__ == "__main__":
    unittest.main()
