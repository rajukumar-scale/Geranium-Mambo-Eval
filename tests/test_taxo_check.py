"""Checking the taxonomy field against a pass: same verdict as the fingerprint linter, and where text differs."""

import importlib.util
import json
import unittest


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
class CompareTest(unittest.TestCase):
    def setUp(self):
        from server import fingerprint, taxo_check
        self.fp, self.check = fingerprint, taxo_check

    def test_same_sql_matches_even_with_other_whitespace(self):
        sql = "BEGIN;\nINSERT INTO slack.messages VALUES ('a', 'b');\nCOMMIT;"
        r = self.check.compare("injection", "  BEGIN;\r\n\tINSERT INTO slack.messages VALUES ('a',  'b');\nCOMMIT;\n",
                               self.fp.text("injection", sql), sql)
        self.assertTrue(r["match"])

    def test_extra_statement_in_the_middle_is_found(self):
        passed = "BEGIN;\n" + "INSERT INTO t VALUES (1);\n" * 50 + "COMMIT;"
        taxo = passed.replace("INSERT INTO t VALUES (1);\n", "INSERT INTO t VALUES (1);\nDELETE FROM t;\n", 1)
        r = self.check.compare("injection", taxo, self.fp.text("injection", passed), passed)
        self.assertFalse(r["match"])
        d = r["difference"]
        self.assertEqual(d["taxonomy_text"][d["offset"]], "D")  # where DELETE starts
        self.assertGreater(d["taxonomy_chars"], d["passed_chars"])

    def test_rubric_json_view_with_builder_entities(self):
        typed = [{"title": "Replies on the Harmony Games <> Ludlow email", "weight": 3}]
        json_view = json.dumps({"criteria": [{"title": "Replies on the Harmony Games &lt;&gt; Ludlow email", "weight": 3}]})
        self.assertTrue(self.check.compare("rubric", json_view, self.fp.rubric(typed))["match"])
        with self.assertRaises(ValueError):
            self.check.compare("rubric", "not json", self.fp.rubric(typed))


if __name__ == "__main__":
    unittest.main()
