"""The rubric check lists the rows that really differ and ignores formatting-only differences."""

import importlib.util
import json
import unittest


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
class RubricRowsTest(unittest.TestCase):
    def setUp(self):
        from server import taxo_check
        self.check = taxo_check
        self.taxonomy = [
            {"title": "The Agent emails [leonard@harmonygames.co](mailto:leonard@harmonygames.co) about the \\\\*Q3\\\\* report", "weight": 5},
            {"title": "The Agent posts in #eng-ops on the \"Harmony Games &lt;&gt; Ludlow\" thread", "weight": 3},
            {"title": "The Agent closes ZOM-387", "weight": 1},
            {"title": "The Agent leaves the Q4 card alone", "weight": 3},
        ]

    def prettified(self):
        """What a model 'making it prettier' tends to produce: unescaped, plain links, plain <>."""
        return [
            {"title": "The Agent emails leonard@harmonygames.co about the *Q3* report", "weight": 5},
            {"title": 'The Agent posts in #eng-ops on the "Harmony Games <> Ludlow" thread', "weight": 3},
            {"title": "The Agent closes ZOM-387", "weight": 1},
            {"title": "The Agent leaves the Q4 card alone", "weight": 3},
        ]

    def test_formatting_only_is_not_a_difference(self):
        r = self.check.rubric_rows(self.taxonomy, self.prettified())
        self.assertEqual(r["total"], 0)

    def test_real_changes_are_listed_by_row(self):
        passed = self.prettified()
        passed[2]["title"] = "The Agent closes ZOM-388"   # wording changed
        del passed[3]                                       # row dropped
        passed[0]["weight"] = 3                             # weight changed (total now differs)
        r = self.check.rubric_rows(self.taxonomy, passed)
        kinds = [(x["kind"], x["taxonomy_row"], x["passed_row"]) for x in r["rows"]]
        self.assertIn(("weight", 1, 1), kinds)
        self.assertIn(("changed", 3, 3), kinds)
        self.assertIn(("extra", 4, None), kinds)
        self.assertEqual((r["taxonomy_count"], r["passed_count"]), (4, 3))

    def test_compare_returns_rows_when_saved_is_known(self):
        from server import fingerprint
        passed = self.prettified()
        passed[1]["title"] = "The Agent posts in #eng-ops"
        out = self.check.compare("rubric", json.dumps(self.taxonomy), fingerprint.rubric(passed), json.dumps(passed))
        self.assertFalse(out["match"])
        self.assertEqual([x["kind"] for x in out["rubric_rows"]["rows"]], ["changed"])


if __name__ == "__main__":
    unittest.main()
