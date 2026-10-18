"""Offline forward preflight cannot manufacture 24h coverage or financial approval."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import sqlite3

from eth_shadow_v1.collector_store import CollectorStore
from eth_shadow_v1.forward_preflight import inspect_forward_preflight

NOW = 1_800_000_000_000


class ForwardPreflightTests(TestCase):
    def test_absent_data_fails_closed(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "absent.sqlite"
            response = inspect_forward_preflight(path, NOW)
            self.assertEqual(response["status"], "BLOCKED")
            self.assertEqual(response["data_24h"], "NO_GO")
            self.assertEqual(response["forward_30d"], "NOT_STARTED")
            self.assertEqual(response["real_mutations"], 0)
            self.assertFalse(path.exists())

    def test_short_collector_history_never_counts_as_24h(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "short.sqlite"
            with CollectorStore(path, "v06-test") as store:
                store.acquire(NOW - 30000)
                store.record_book(NOW - 25000, None, None, None, "SYNTHETIC_REJECT")
                store.release(NOW - 20000)
            before = path.read_bytes()
            result = inspect_forward_preflight(path, NOW)
            self.assertEqual(result["status"], "BLOCKED", result)
            self.assertEqual(result["data_24h"], "NO_GO")
            self.assertEqual(result["forward_30d"], "NOT_STARTED")
            self.assertEqual(result["economic_approval"], "NO_GO")
            self.assertLess(result["accepted_unique_slots"], result["expected_slots"])
            self.assertEqual(result["collector_audit"], "PASS")
            self.assertEqual(path.read_bytes(), before)

    def test_malformed_db_fails_closed_and_is_not_rewritten(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "malformed.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE fake (id INTEGER)")
            before = path.read_bytes()
            result = inspect_forward_preflight(path, NOW)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["real_mutations"], 0)
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_clock_fails_closed(self):
        with TemporaryDirectory() as folder:
            result = inspect_forward_preflight(Path(folder) / "none.sqlite", -1)
            self.assertEqual(result["reasons"], ["INVALID_AUDIT_TIME"])


if __name__ == "__main__":
    import unittest
    unittest.main()
