from decimal import Decimal
import tempfile
import unittest
import uuid
from pathlib import Path

from grid_executor import GridExecutor
from precision_engine import PairRules, PrecisionError
from revolut_x_client import RevolutXError


class FakeClient:
    def __init__(self) -> None:
        self.balances = [
            {"currency": "BTC", "available": "10"},
            {"currency": "USD", "available": "10000"},
        ]
        self.active_orders = []
        self.cancel_calls = 0
        self.place_calls = []
        self.replace_calls = []

    def get_balances(self):
        return list(self.balances)

    def get_active_orders(self, symbols=None):
        return list(self.active_orders)

    def place_limit_order(self, **kwargs):
        self.place_calls.append(kwargs)
        return {
            "venue_order_id": str(uuid.uuid4()),
            "client_order_id": kwargs["client_order_id"],
            "state": "new",
        }

    def replace_limit_order(self, **kwargs):
        self.replace_calls.append(kwargs)
        return {
            "venue_order_id": str(uuid.uuid4()),
            "client_order_id": kwargs["client_order_id"],
            "state": "new",
        }

    def cancel_all_orders(self):
        self.cancel_calls += 1


class FailingCancelClient(FakeClient):
    def cancel_all_orders(self):
        self.cancel_calls += 1
        raise RevolutXError("network failure")


class GridExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = PairRules(
            symbol="BTC/USD",
            base="BTC",
            quote="USD",
            base_step=Decimal("0.0001"),
            quote_step=Decimal("0.01"),
            min_order_size=Decimal("0.0001"),
            max_order_size=Decimal("100"),
            min_order_size_quote=Decimal("0.01"),
            status="active",
        )

    def _executor(self, client=None, *, dry_run=True):
        tmp = tempfile.TemporaryDirectory()
        executor = GridExecutor(
            client or FakeClient(),
            db_path=str(Path(tmp.name) / "state.db"),
            dry_run=dry_run,
        )
        self.addCleanup(executor.close)
        self.addCleanup(tmp.cleanup)
        return executor

    def test_build_grid_requires_decimal(self) -> None:
        executor = self._executor()
        with self.assertRaises(TypeError):
            executor.build_grid(100.0, 1, Decimal("25"), self.rules)  # type: ignore[arg-type]

    def test_build_grid_rounds_prices_down(self) -> None:
        executor = self._executor()
        orders = executor.build_grid(
            Decimal("100.123"),
            1,
            Decimal("25"),
            self.rules,
        )
        self.assertEqual(orders[0].price, Decimal("99.87"))
        self.assertEqual(orders[1].price, Decimal("100.37"))
        self.assertEqual(orders[0].logical_id, "BTC/USD:buy:1")

    def test_balance_float_is_rejected_fail_closed(self) -> None:
        client = FakeClient()
        client.balances[1]["available"] = 10000.0
        executor = self._executor(client)
        with self.assertRaises(PrecisionError):
            executor.maintain_grid(
                symbol="BTC-USD",
                rules=self.rules,
                center=Decimal("100"),
                best_bid=Decimal("99.5"),
                best_ask=Decimal("100.5"),
                levels_per_side=1,
                spacing_bps=Decimal("100"),
                allocation_pct=Decimal("1"),
                recenter_threshold_bps=Decimal("75"),
            )

    def test_dry_run_grid_is_idempotent_with_same_center(self) -> None:
        client = FakeClient()
        executor = self._executor(client)
        kwargs = dict(
            symbol="BTC-USD",
            rules=self.rules,
            center=Decimal("100"),
            best_bid=Decimal("99.5"),
            best_ask=Decimal("100.5"),
            levels_per_side=1,
            spacing_bps=Decimal("100"),
            allocation_pct=Decimal("1"),
            recenter_threshold_bps=Decimal("75"),
        )
        executor.maintain_grid(**kwargs)
        first_rows = executor._conn.execute(
            "SELECT logical_id, client_order_id, price, base_size, state FROM grid_orders ORDER BY logical_id"
        ).fetchall()
        executor.maintain_grid(**kwargs)
        second_rows = executor._conn.execute(
            "SELECT logical_id, client_order_id, price, base_size, state FROM grid_orders ORDER BY logical_id"
        ).fetchall()
        self.assertEqual([tuple(row) for row in first_rows], [tuple(row) for row in second_rows])
        self.assertEqual(len(first_rows), 2)

    def test_strategy_change_fails_with_active_orders(self) -> None:
        executor = self._executor()
        executor.maintain_grid(
            symbol="BTC-USD",
            rules=self.rules,
            center=Decimal("100"),
            best_bid=Decimal("99.5"),
            best_ask=Decimal("100.5"),
            levels_per_side=1,
            spacing_bps=Decimal("100"),
            allocation_pct=Decimal("1"),
            recenter_threshold_bps=Decimal("75"),
        )
        with self.assertRaises(RevolutXError):
            executor.maintain_grid(
                symbol="BTC-USD",
                rules=self.rules,
                center=Decimal("100"),
                best_bid=Decimal("99.5"),
                best_ask=Decimal("100.5"),
                levels_per_side=2,
                spacing_bps=Decimal("100"),
                allocation_pct=Decimal("1"),
                recenter_threshold_bps=Decimal("75"),
            )

    def test_reconcile_uses_active_order_id_field(self) -> None:
        client = FakeClient()
        executor = self._executor(client, dry_run=False)
        client_id = str(uuid.uuid4())
        venue_id = str(uuid.uuid4())
        executor._conn.execute(
            """
            INSERT INTO grid_orders(
                logical_id,symbol,side,client_order_id,venue_order_id,
                previous_client_order_id,previous_venue_order_id,
                price,base_size,state,updated_at_ms
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "BTC/USD:buy:1","BTC-USD","buy",client_id,None,None,None,
                "100.00","0.1000","new",1
            ),
        )
        executor._conn.commit()
        client.active_orders = [{
            "id": venue_id,
            "client_order_id": client_id,
            "symbol": "BTC/USD",
            "side": "buy",
            "quantity": "0.1000",
            "price": "100.00",
            "status": "new",
        }]
        executor.reconcile("BTC-USD")
        row = executor._conn.execute(
            "SELECT venue_order_id,state FROM grid_orders WHERE logical_id='BTC/USD:buy:1'"
        ).fetchone()
        self.assertEqual(row["venue_order_id"], venue_id)
        self.assertEqual(row["state"], "new")

    def test_pending_submit_missing_remote_fails_closed(self) -> None:
        executor = self._executor(FakeClient(), dry_run=False)
        executor._conn.execute(
            """
            INSERT INTO grid_orders(
                logical_id,symbol,side,client_order_id,venue_order_id,
                previous_client_order_id,previous_venue_order_id,
                price,base_size,state,updated_at_ms
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "BTC/USD:buy:1","BTC-USD","buy",str(uuid.uuid4()),None,None,None,
                "100","0.1","pending_submit",1
            ),
        )
        executor._conn.commit()
        with self.assertRaises(RevolutXError):
            executor.reconcile("BTC-USD")

    def test_live_place_passes_decimal_to_client(self) -> None:
        client = FakeClient()
        executor = self._executor(client, dry_run=False)
        executor.maintain_grid(
            symbol="BTC-USD",
            rules=self.rules,
            center=Decimal("100"),
            best_bid=Decimal("99.5"),
            best_ask=Decimal("100.5"),
            levels_per_side=1,
            spacing_bps=Decimal("100"),
            allocation_pct=Decimal("1"),
            recenter_threshold_bps=Decimal("75"),
        )
        self.assertEqual(len(client.place_calls), 2)
        for call in client.place_calls:
            self.assertIsInstance(call["base_size"], Decimal)
            self.assertIsInstance(call["price"], Decimal)

    def test_kill_switch_persists_before_cancel_failure(self) -> None:
        client = FailingCancelClient()
        executor = self._executor(client, dry_run=False)
        with self.assertRaises(RevolutXError):
            executor.trigger_kill_switch()
        self.assertEqual(executor.get_meta("kill_switch_triggered"), "true")
        self.assertIsNotNone(executor.get_meta("kill_switch_triggered_at_ms"))
        self.assertEqual(client.cancel_calls, 1)

    def test_kill_switch_blocks_future_execution(self) -> None:
        executor = self._executor()
        executor.trigger_kill_switch()
        with self.assertRaises(RevolutXError):
            executor.maintain_grid(
                symbol="BTC-USD",
                rules=self.rules,
                center=Decimal("100"),
                best_bid=Decimal("99.5"),
                best_ask=Decimal("100.5"),
                levels_per_side=1,
                spacing_bps=Decimal("100"),
                allocation_pct=Decimal("1"),
                recenter_threshold_bps=Decimal("75"),
            )


if __name__ == "__main__":
    unittest.main()
