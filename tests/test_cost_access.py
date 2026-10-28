"""The Cost page and its data are for owners (ADMIN_EMAILS, i.e. atlasadmin) only; other admins are turned away."""

import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from server import app as portal, auth


class CostAccessTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(portal.app)
        roles = {"atlasadmin": "owner", "helper@scale.com": "admin"}
        p = mock.patch.object(portal, "admin_role", lambda email: roles.get(email))
        p.start()
        self.addCleanup(p.stop)

    def as_admin(self, email):
        self.client.cookies.set(auth.ADMIN_COOKIE, auth.sign({"email": email, "iat": time.time()}))

    def test_owner_gets_page_and_data(self):
        self.as_admin("atlasadmin")
        self.assertEqual(self.client.get("/admin/cost", follow_redirects=False).status_code, 200)
        with mock.patch.object(portal.cost, "report", mock.AsyncMock(return_value={"loading": True})), \
                mock.patch.object(portal.budget, "status", mock.AsyncMock(return_value={})):
            self.assertEqual(self.client.get("/api/admin/cost").status_code, 200)

    def test_other_admin_is_turned_away(self):
        self.as_admin("helper@scale.com")
        page = self.client.get("/admin/cost", follow_redirects=False)
        self.assertEqual((page.status_code, page.headers["location"]), (303, "/admin"))
        self.assertEqual(self.client.get("/api/admin/cost").status_code, 403)

    def test_signed_out(self):
        self.assertEqual(self.client.get("/admin/cost", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/api/admin/cost").status_code, 401)


if __name__ == "__main__":
    unittest.main()
