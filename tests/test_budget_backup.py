"""The admin budget panel shows the backup key too, and which key the meter is on (the backup once the main key is
at its cap, 2026-11-01)."""

import asyncio
import time
import unittest
from unittest import mock

from server import budget, config


def key_row(alias, cap, spent, spend_24h=0.0):
    now = time.time()
    return {"KEY_ALIAS": alias, "MAX_BUDGET": cap, "SPEND": spent, "LITELLM_SPEND": 0.0, "SMIG_SPEND": spent,
            "BUDGET_DURATION": "7d", "BUDGET_RESET_AT": now + 4 * 3600, "BLOCKED": None, "KEY_SYNCED_AT": now,
            "SPEND_1H": spend_24h / 24, "SPEND_3H": spend_24h / 8, "SPEND_24H": spend_24h,
            "LAST_REQUEST_AT": now, "CHECKED_AT": now}


class BackupBudgetTest(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        self.portal_lookups = 0

        async def redash_rows(query_id, parameters=None, max_age=0, timeout=90):
            rows = self.rows.get(query_id, [])
            if isinstance(rows, Exception):
                raise rows
            return rows

        def q(sql, args=()):
            self.portal_lookups += 1
            return []

        for p in (mock.patch.object(budget.auth, "redash_rows", redash_rows), mock.patch.object(budget.db, "q", q),
                  mock.patch.object(budget, "daily", lambda rows, summary: []),
                  mock.patch.object(budget, "log_gap", lambda summary: None),
                  mock.patch.object(config, "BUDGET_QUERY_ID", 1), mock.patch.object(config, "BUDGET_DAILY_QUERY_ID", 2),
                  mock.patch.object(config, "BACKUP_BUDGET_QUERY_ID", 3),
                  mock.patch.object(budget, "_cache", {"at": 0.0, "data": None})):
            p.start()
            self.addCleanup(p.stop)

    def status(self):
        return asyncio.run(budget.status(force=True))

    def test_main_key_in_use_while_under_its_cap(self):
        self.rows = {1: [key_row("main", 60000, 30000, 2400)], 3: [key_row("backup", 15000, 0.5)]}
        data = self.status()
        self.assertEqual(data["active"], "main")
        self.assertEqual(data["backup"]["key_alias"], "backup")
        self.assertEqual(data["backup"]["pct"], 0.0)

    def test_backup_in_use_once_the_main_key_is_at_its_cap(self):
        self.rows = {1: [key_row("main", 60000, 60000.11)], 3: [key_row("backup", 15000, 2415.52, 2400)]}
        data = self.status()
        self.assertEqual(data["active"], "backup")
        b = data["backup"]
        self.assertEqual((b["spend"], b["max_budget"], b["left"]), (2415.52, 15000, 15000 - 2415.52))
        self.assertTrue(b["query_url"].endswith("/queries/3"))
        self.assertIsNone(b["portal_cycle"])  # the portal's own spend is shown once, on the main key
        self.assertEqual(self.portal_lookups, 1)

    def test_a_failed_backup_lookup_keeps_the_main_key(self):
        self.rows = {1: [key_row("main", 60000, 60000.11)], 3: RuntimeError("redash down")}
        data = self.status()
        self.assertEqual(data["key_alias"], "main")
        self.assertIn("redash down", data["backup"]["error"])
        self.assertEqual(data["active"], "main")

    def test_no_backup_query_means_no_backup_section(self):
        self.rows = {1: [key_row("main", 60000, 100)]}
        with mock.patch.object(config, "BACKUP_BUDGET_QUERY_ID", 0):
            data = self.status()
        self.assertNotIn("backup", data)
        self.assertNotIn("active", data)


if __name__ == "__main__":
    unittest.main()
