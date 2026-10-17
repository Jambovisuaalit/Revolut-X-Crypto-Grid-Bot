"""Offline integrity checks for venue/local orders and immutable fills."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from grid_executor import GridExecutor, ReconciliationError
from test_bot import FakeClient, rules, uid


class VenueIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = FakeClient()
        self.exe = GridExecutor(self.client, {"BTC/USD": rules()},
                                database_path=str(Path(self.tmp.name) / "bot.db"),
                                live_trading=True)
        self.exe.reconcile_open_orders()

    def tearDown(self):
        self.exe.close()
        self.tmp.cleanup()

    def order(self):
        return self.exe.create_order(
            client_order_id=uid(), symbol="BTC/USD", side="buy",
            price=Decimal("100"), quantity=Decimal(".01"),
            free_balances={"USD": Decimal("100000")},
        )

    def test_venue_order_drift_halts_new_trades(self):
        self.order()
        self.client.active[0]["price"] = "101"
        with self.assertRaises(ReconciliationError):
            self.exe.reconcile_open_orders()
        self.assertFalse(self.exe.reconciled)
        with self.assertRaises(ReconciliationError):
            self.order()
        self.assertEqual(len(self.client.placed), 1)

    def test_unknown_open_venue_order_halts_new_trades(self):
        self.order()
        unknown = dict(self.client.active[0], id=uid(), client_order_id=uid())
        self.client.active.append(unknown)
        with self.assertRaises(ReconciliationError):
            self.exe.reconcile_open_orders()
        with self.assertRaises(ReconciliationError):
            self.order()
        self.assertEqual(len(self.client.placed), 1)

    def test_fill_change_halts_reconciliation_and_new_risk(self):
        result = self.order()
        self.client.fills = [{"tid": uid(), "q": ".001", "p": "100",
                              "fee": ".0001", "fee_currency": "USD", "im": True}]
        self.exe.reconcile_fills(result["venue_order_id"])
        self.client.fills[0]["p"] = "101"
        # Force reconciliation to fetch fills for a partial execution.
        self.client.active[0]["filled_quantity"] = ".001"
        self.client.active[0]["leaves_quantity"] = ".009"
        self.client.active[0]["status"] = "partially_filled"
        with self.assertRaises(ReconciliationError):
            self.exe.reconcile_open_orders()
        with self.assertRaises(ReconciliationError):
            self.order()
        self.assertEqual(len(self.client.placed), 1)

    def test_identical_repeated_fills_are_idempotent(self):
        result = self.order()
        self.client.fills = [{"tid": uid(), "q": ".001", "p": "100",
                              "fee": ".0001", "fee_currency": "USD", "im": True}]
        self.exe.reconcile_fills(result["venue_order_id"])
        self.exe.reconcile_fills(result["venue_order_id"])
        self.assertEqual(self.exe.connection.execute(
            "SELECT COUNT(*) FROM fills_wire"
        ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
