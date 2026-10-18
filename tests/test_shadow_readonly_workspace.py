"""Offline accounting projection tests. All market events and fills synthetic."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from decimal import Decimal

from eth_shadow_v1.config import Config
from eth_shadow_v1.grid import PairRules
from eth_shadow_v1.journal import Journal
from product_shell.shadow_readonly import CONFIG_PATH, inspect_shadow_journal
from scripts.run_eth_eur_shadow import fixture


class ShadowReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic-shadow.sqlite"
        payload = fixture()
        cfg = Config.load(CONFIG_PATH)
        rules = PairRules.from_dict(payload["pair_rules"])
        with Journal(self.path, "offline-v05-test", cfg, rules) as journal:
            for event in payload["events"]:
                journal.process(event)
            self.reference = journal.report()

    def test_accounting_and_read_only_integrity(self):
        before = self.path.read_bytes()
        view = inspect_shadow_journal(self.path)
        self.assertEqual(view["status"], "READY", view)
        self.assertEqual(view["journal_integrity"], "PASS")
        self.assertEqual(view["source"], "SYNTHETIC_FIXTURE_ONLY")
        self.assertEqual(view["observations"], 5)
        self.assertEqual(view["live_orders"], "DISABLED")
        self.assertEqual(view["account_connection"], "NOT_CONNECTED")
        for actual, expected in (
            ("equity_eur", "final_equity_eur"),
            ("cash_eur", "cash_eur"),
            ("eth_qty", "eth_qty"),
            ("fees_eur", "simulated_fees_eur"),
            ("realized_pnl_eur", "realized_pnl_eur"),
            ("total_pnl_eur", "simulated_total_pnl_eur"),
            ("max_drawdown_eur", "max_drawdown_eur"),
        ):
            self.assertAlmostEqual(Decimal(view[actual]), Decimal(self.reference[expected]),delta=Decimal("0.01"))
        self.assertEqual(
            Decimal(view["realized_pnl_eur"]) + Decimal(view["unrealized_pnl_eur"]),
            Decimal(view["total_pnl_eur"])
        )
        self.assertTrue(all(x["synthetic"] for x in view["fills"]))
        self.assertEqual(len(view["equity_history"]), 5)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corruption_fails_closed_without_balance_leak(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE decisions SET state_json=? WHERE seq=1", ('{"tampered":true}',))
        view = inspect_shadow_journal(self.path)
        self.assertEqual(view["status"], "BLOCKED")
        self.assertIsNone(view["equity_eur"])
        self.assertEqual(view["fills"], [])

    def test_non_shadow_schema_is_not_misidentified(self):
        other = Path(self.temp.name) / "supervisor.sqlite"
        with sqlite3.connect(other) as db:
            db.execute("CREATE TABLE bot_metadata(key TEXT, value TEXT)")
        view = inspect_shadow_journal(other)
        self.assertEqual(view["status"], "BLOCKED")
        self.assertIsNone(view["total_pnl_eur"])

    def test_missing_journal_does_not_create_file(self):
        missing = Path(self.temp.name) / "missing.sqlite"
        view = inspect_shadow_journal(missing)
        self.assertEqual(view["status"], "BLOCKED")
        self.assertFalse(missing.exists())
        self.assertIsNone(view["cash_eur"])

    def test_no_file_selected_is_not_configured(self):
        self.assertEqual(inspect_shadow_journal(None)["status"], "NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
