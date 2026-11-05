"""The portal's own icon (2026-11-04): every page links it, and /favicon.ico serves it."""

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server import app as portal

WEB = Path(__file__).resolve().parent.parent / "web"


class IconTest(unittest.TestCase):
    def test_every_page_links_the_icon(self):
        for page in WEB.glob("*.html"):
            text = page.read_text()
            self.assertIn('href="/static/img/icon.svg"', text, page.name)
            self.assertIn('href="/static/img/icon-32.png"', text, page.name)
        for name in ("icon.svg", "icon-32.png", "apple-touch-icon.png"):
            self.assertTrue((WEB / "img" / name).is_file(), name)

    def test_favicon_ico_serves_it(self):
        r = TestClient(portal.app).get("/favicon.ico")
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/png"))
        self.assertTrue(r.content.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
