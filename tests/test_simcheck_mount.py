"""Docker reviews get the similarity runtime (2026-11-07): the runner image doesn't have /opt/simcheck, so every
/prompt run in docker ended PARTIAL "dataset similarity runtime is missing" (eval-portal.scale.com runs only docker)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server import config, workspace


class SimcheckMountTest(unittest.TestCase):
    def mounts(self, with_runtime):
        with tempfile.TemporaryDirectory() as d:
            sim = Path(d) / "simcheck"
            if with_runtime:
                (sim / "model").mkdir(parents=True)
                (sim / "model" / "model.onnx").write_bytes(b"x")
            with mock.patch.object(config, "SIMCHECK_DIR", sim), mock.patch.object(config, "DATA_DIR", Path(d) / "data"), \
                    mock.patch.object(config, "universe_dirs", lambda base: []):
                return " ".join(workspace.docker_mounts("a1")), sim

    def test_mounted_read_only_when_installed(self):
        args, sim = self.mounts(True)
        self.assertIn(f"type=bind,source={sim},target=/opt/simcheck,readonly", args)

    def test_skipped_with_a_warning_when_missing(self):
        workspace._simcheck_warned.clear()
        with self.assertLogs("portal.workspace", "WARNING"):
            args, _ = self.mounts(False)
        self.assertNotIn("/opt/simcheck", args)


if __name__ == "__main__":
    unittest.main()
