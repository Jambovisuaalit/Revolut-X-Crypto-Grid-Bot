"""Offline accounting projection tests. All market events and fills synthetic."""
from __future__ import annotations

from pathlib import Path
from contextlib import closing
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

    def test_open_virtual_position_reconciles_equity_and_cost_basis(self):
        other = Path(self.temp.name) / "open-lot.sqlite"
        payload = fixture()
        cfg = Config.load(CONFIG_PATH)
        rules = PairRules.from_dict(payload["pair_rules"])
        with Journal(other, "open-lot-v05", cfg, rules) as journal:
            for event in payload["events"][:3]:
                journal.process(event)
            report = journal.report()
        snapshot = inspect_shadow_journal(other)
        self.assertEqual(snapshot["status"], "READY", snapshot)
        self.assertGreater(len(snapshot["positions"]), 0)
        self.assertGreater(Decimal(snapshot["eth_qty"]), Decimal("0"))
        self.assertEqual(
            sum(Decimal(position["quantity"]) for position in snapshot["positions"]),
            Decimal(snapshot["eth_qty"]),
        )
        self.assertEqual(
            sum(Decimal(position["cost_basis_eur"]) for position in snapshot["positions"]),
            Decimal(snapshot["cost_basis_eur"]),
        )
        self.assertLessEqual(Decimal(snapshot["cost_basis_eur"]), Decimal("400"))
        self.assertAlmostEqual(
            Decimal(snapshot["equity_eur"]), Decimal(report["final_equity_eur"]),
            delta=Decimal("0.01"),
        )

    def test_modified_indexed_fill_fails_closed(self):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT fill_id, details_json FROM synthetic_fills LIMIT 1").fetchone()
            details = __import__("json").loads(row[1])
            details["fee_eur"] = "123.45"
            db.execute("UPDATE synthetic_fills SET details_json=? WHERE fill_id=?",
                       (__import__("json").dumps(details), row[0]))
            db.commit()
        blocked = inspect_shadow_journal(self.path)
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIsNone(blocked["equity_eur"])

    def test_corruption_fails_closed_without_balance_leak(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE decisions SET state_json=? WHERE seq=1", ('{"tampered":true}',))
            db.commit()
        view = inspect_shadow_journal(self.path)
        self.assertEqual(view["status"], "BLOCKED")
        self.assertIsNone(view["equity_eur"])
        self.assertEqual(view["fills"], [])

    def test_non_shadow_schema_is_not_misidentified(self):
        other = Path(self.temp.name) / "supervisor.sqlite"
        with closing(sqlite3.connect(other)) as db:
            db.execute("CREATE TABLE bot_metadata(key TEXT, value TEXT)")
            db.commit()
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
