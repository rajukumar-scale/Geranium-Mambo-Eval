"""Reading .env: inline comments are dropped, including after an empty value as in .env.example."""

import os
import pathlib
import tempfile
import unittest
from unittest import mock

from server import config


class DotenvTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / ".env"
            path.write_text(text)
            with mock.patch.dict(os.environ, {}, clear=True):
                config._load_dotenv(path)
                return dict(os.environ)

    def test_empty_value_with_comment(self):
        env = self.load("MODAL_AWS_PROFILE=                       # empty = the default AWS credentials\n")
        self.assertEqual(env["MODAL_AWS_PROFILE"], "")

    def test_value_with_comment(self):
        env = self.load("MODAL_PROJECT_ID=6a5595596cf3031de47f0ea2  # the Scale project\n")
        self.assertEqual(env["MODAL_PROJECT_ID"], "6a5595596cf3031de47f0ea2")

    def test_hash_inside_value_kept(self):
        env = self.load("ADMIN_PASSWORD=ab#cd\nQUOTED='# not a comment'\n")
        self.assertEqual(env["ADMIN_PASSWORD"], "ab#cd")
        self.assertEqual(env["QUOTED"], "# not a comment")


if __name__ == "__main__":
    unittest.main()
