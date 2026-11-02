"""The taxonomy's rubric JSON view isn't valid JSON (unescaped quotes) and shortens links; the check still works."""

import importlib.util
import json
import unittest

PASSED = [
    {"id": "r1", "title": 'The Agent replies on the "Q3 payout" thread at <https://harmonygames.slack.com/archives/C0123ABCD/p1727700000123456>', "weight": 5,
     "annotations": {"rubric_type": "outcome", "title": "ignored nested field"}},
    {"id": "r2", "title": "The Agent names [SOURCE_ORG_0001] as the vendor", "weight": 3, "annotations": {"rubric_type": "outcome"}},
    {"id": "r3", "title": "The Agent emails [leo@harmonygames.co](mailto:leo@harmonygames.co)", "weight": 2, "annotations": {}},
]

JSON_VIEW = """[
  {
    "id": "r1",
    "title": "The Agent replies on the "Q3 payout" thread at https://harmonygames.slack.com/archives/…/N",
    "weight": 5,
    "annotations": {
      "rubric_type": "outcome",
      "title": "ignored nested field"
    }
  },
  {
    "id": "r2",
    "title": "The Agent names SOURCE_ORG_0001 as the vendor",
    "weight": 3,
    "annotations": {
      "rubric_type": "outcome"
    }
  },
  {
    "id": "r3",
    "title": "The Agent emails leo@harmonygames.co",
    "weight": 2,
    "annotations": {}
  }
]"""


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "server packages not installed here")
class JsonViewTest(unittest.TestCase):
    def setUp(self):
        from server import fingerprint, taxo_check
        self.fp, self.check = fingerprint, taxo_check

    def test_reads_the_json_view_with_unescaped_quotes(self):
        rows = self.check._criteria(JSON_VIEW)
        self.assertEqual([r["weight"] for r in rows], ["5", "3", "2"])
        self.assertEqual(rows[0]["title"][:41], 'The Agent replies on the "Q3 payout" thre')

    def test_shortened_links_still_match(self):
        r = self.check.compare("rubric", JSON_VIEW, self.fp.rubric(PASSED), json.dumps(PASSED))
        self.assertTrue(r["match"])
        self.assertTrue(r.get("links_ignored"))

    def test_a_real_edit_is_still_found(self):
        edited = JSON_VIEW.replace("as the vendor", "as the new vendor")
        r = self.check.compare("rubric", edited, self.fp.rubric(PASSED), json.dumps(PASSED))
        self.assertFalse(r["match"])
        self.assertEqual([(x["kind"], x["taxonomy_row"]) for x in r["rubric_rows"]["rows"]], [("changed", 2)])


if __name__ == "__main__":
    unittest.main()
