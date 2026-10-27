"""Every server module compiles (and imports, where the server's packages are installed); every web script parses."""

import importlib
import importlib.util
import pathlib
import py_compile
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class SmokeTest(unittest.TestCase):
    def test_server_compiles(self):
        for path in sorted((ROOT / "server").glob("*.py")):
            with self.subTest(file=path.name):
                py_compile.compile(str(path), doraise=True)

    @unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
    def test_server_imports(self):
        for path in sorted((ROOT / "server").glob("*.py")):
            if path.stem in ("__init__", "main", "notify_cli"):  # main starts the server; notify_cli posts to Slack
                continue
            with self.subTest(module=path.stem):
                importlib.import_module(f"server.{path.stem}")

    @unittest.skipUnless(shutil.which("node"), "node not installed here")
    def test_web_scripts_parse(self):
        for path in sorted((ROOT / "web").glob("*.js")):
            with self.subTest(file=path.name):
                subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
