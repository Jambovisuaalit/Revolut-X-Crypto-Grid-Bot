"""Deterministic drawdown/kill-switch and reserve-limit boundary tests."""
from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from bot_main import GridBotSupervisor
from grid_executor import GridExecutor, ReconciliationError
from precision_engine import PrecisionError
from test_bot import FakeClient, rules, uid


class SafetyBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "state.sqlite")
        self.venue = FakeClient()
        self.executor = GridExecutor(
            self.venue, {"BTC/USD": rules()},
            database_path=self.db, live_trading=True,
        )
        self.executor.reconcile_open_orders()
        self.supervisor = GridBotSupervisor(self.executor, "USD")

    def tearDown(self):
        self.executor.close()
        self.tmp.cleanup()

    def place(self, quantity=Decimal(".01"), balances=None):
        return self.executor.create_order(
            client_order_id=uid(), symbol="BTC/USD", side="buy",
            price=Decimal("100"), quantity=quantity,
            free_balances=balances or {"USD": Decimal("1000")},
        )

    def test_drawdown_threshold_just_below_and_at_three_percent(self):
        self.assertFalse(self.supervisor.check_drawdown(Decimal("2000")))
        self.assertFalse(self.supervisor.check_drawdown(Decimal("1940.01")))
        self.assertFalse(self.venue.cancelled)
        self.assertTrue(self.supervisor.check_drawdown(Decimal("1940.00")))
        self.assertTrue(self.venue.cancelled)
        with self.assertRaises(ReconciliationError):
            self.place()
        self.assertEqual(len(self.venue.placed), 0)

    def test_cancel_network_uncertainty_cannot_unlock_persistent_kill(self):
        self.assertFalse(self.supervisor.check_drawdown(Decimal("1000")))
        def uncertain():
            raise RuntimeError("DELETE result uncertain")
        self.venue.cancel_all_orders = uncertain
        with self.assertRaises(RuntimeError):
            self.supervisor.check_drawdown(Decimal("970"))
        self.assertEqual(self.supervisor._metadata("kill_switch_triggered"), "true")
        with self.assertRaises(ReconciliationError):
            self.place()
        self.executor.close()
        self.executor = GridExecutor(
            self.venue, {"BTC/USD": rules()},
            database_path=self.db, live_trading=True,
        )
        with self.assertRaises(ReconciliationError):
            self.executor.create_order(
                client_order_id=uid(), symbol="BTC/USD", side="buy",
                price=Decimal("100"), quantity=Decimal(".01"),
                free_balances={"USD": Decimal("1000")},
            )

    def test_single_order_limit_five_percent(self):
        self.place(Decimal(".5"), {"USD": Decimal("1000")})  # $50 exactly
        with self.assertRaises(PrecisionError):
            self.place(Decimal(".50000001"), {"USD": Decimal("1000")})
        self.assertEqual(len(self.venue.placed), 1)

    def test_nav_mark_to_market_requires_quotes(self):
        self.assertEqual(
            self.supervisor.mark_to_market(
                {"USD": Decimal("100"), "BTC": Decimal(".01")},
                {"BTC/USD": Decimal("100000")},
            ), Decimal("1100"),
        )
        with self.assertRaises(ValueError):
            self.supervisor.mark_to_market(
                {"USD": Decimal("100"), "BTC": Decimal(".01")}, {}
            )
        with self.assertRaises(ValueError):
            self.supervisor.mark_to_market(
                {"USD": Decimal("100"), "BTC": Decimal("-.01")},
                {"BTC/USD": Decimal("100000")},
            )

    def test_uncertain_order_intent_blocks_every_new_order_on_second_connection(self):
        cid = uid()
        original = self.venue.place_order
        def lost(payload):
            original(payload)
            raise RuntimeError("acknowledgement dropped")
        self.venue.place_order = lost
        with self.assertRaises(RuntimeError):
            self.executor.create_order(
                client_order_id=cid, symbol="BTC/USD", side="buy",
                price=Decimal("100"), quantity=Decimal(".01"),
                free_balances={"USD": Decimal("1000")},
            )
        other = GridExecutor(
            self.venue, {"BTC/USD": rules()},
            database_path=self.db, live_trading=True,
        )
        try:
            with self.assertRaises(ReconciliationError):
                other.create_order(
                    client_order_id=uid(), symbol="BTC/USD", side="buy",
                    price=Decimal("100"), quantity=Decimal(".01"),
                    free_balances={"USD": Decimal("1000")},
                )
            self.assertEqual(len(self.venue.placed), 1)
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
