"""Pages link their assets with ?v=<build>, and those are cached for good: every revalidation is a round trip over
boat's slow link. Unversioned asset URLs still revalidate, so an old page never runs a mix of builds."""

import re
import unittest

from fastapi.testclient import TestClient

from server import app as portal


class StaticCacheTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(portal.app)

    def test_pages_link_versioned_assets(self):
        for path in ("/", "/admin", "/status", "/verify"):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertEqual(page.headers["cache-control"], "no-cache")
                assets = re.findall(r'(?:src|href)="(/static/[^"]+)"', page.text)
                self.assertTrue(all(a.endswith(f"?v={portal.WEB_BUILD}") for a in assets), assets)

    def test_versioned_asset_is_immutable(self):
        r = self.client.get(f"/static/app.js?v={portal.WEB_BUILD}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r.headers["cache-control"])

    def test_other_builds_and_plain_urls_revalidate(self):
        for url in ("/static/app.js", "/static/app.js?v=000000000000"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).headers["cache-control"], "no-cache")

    def test_fonts_cached_for_a_week(self):
        r = self.client.get("/static/fonts/aeonik/Aeonik-Regular.woff2")
        self.assertEqual((r.status_code, r.headers["cache-control"]), (200, "public, max-age=604800"))

    def test_missing_asset_not_cached(self):
        r = self.client.get(f"/static/nope.js?v={portal.WEB_BUILD}")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("immutable", r.headers.get("cache-control", ""))


if __name__ == "__main__":
    unittest.main()
