"""Persistent drawdown and multi-connection risk boundary tests (fake venue only)."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from bot_main import GridBotSupervisor
from grid_executor import GridExecutor, ReconciliationError
from test_bot import FakeClient, rules, uid


class PersistentRiskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "bot.db")
        self.venue = FakeClient()
        self.a = GridExecutor(self.venue, {"BTC/USD": rules()},
                              database_path=self.path, live_trading=True)
        self.b = GridExecutor(self.venue, {"BTC/USD": rules()},
                              database_path=self.path, live_trading=True)
        self.a.reconcile_open_orders()
        self.b.reconcile_open_orders()

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.temp.cleanup()

    def submit(self, executor):
        return executor.create_order(
            client_order_id=uid(), symbol="BTC/USD", side="buy",
            price=Decimal("100"), quantity=Decimal(".01"),
            free_balances={"USD": Decimal("100000")},
        )

    def test_two_process_equivalent_supervisors_share_initial_baseline(self):
        first = GridBotSupervisor(self.a, "USD")
        second = GridBotSupervisor(self.b, "USD")
        barrier = threading.Barrier(2)
        def check(supervisor, value):
            barrier.wait(timeout=5)
            return supervisor.check_drawdown(value)
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(check, first, Decimal("2000"))
            two = pool.submit(check, second, Decimal("2000"))
            self.assertFalse(one.result(timeout=5))
            self.assertFalse(two.result(timeout=5))
        self.assertEqual(self.a.connection.execute(
            "SELECT value FROM bot_metadata WHERE key='initial_equity'"
        ).fetchone()[0], "2000")

    def test_breach_survives_restart_and_blocks_second_connection(self):
        first = GridBotSupervisor(self.a, "USD")
        self.assertFalse(first.check_drawdown(Decimal("2000")))
        self.assertTrue(first.check_drawdown(Decimal("1940")))
        self.assertTrue(GridBotSupervisor(self.b, "USD").check_drawdown(Decimal("2000")))
        with self.assertRaises(ReconciliationError):
            self.submit(self.b)
        self.a.close()
        self.a = GridExecutor(self.venue, {"BTC/USD": rules()},
                              database_path=self.path, live_trading=True)
        with self.assertRaises(ReconciliationError):
            self.submit(self.a)
        self.assertEqual(len(self.venue.placed), 0)

    def test_nonfinite_nav_never_resets_baseline(self):
        sup = GridBotSupervisor(self.a, "USD")
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-1")):
            with self.assertRaises(ValueError):
                sup.check_drawdown(value)
        self.assertIsNone(self.a.connection.execute(
            "SELECT value FROM bot_metadata WHERE key='initial_equity'"
        ).fetchone())
        self.assertFalse(sup.check_drawdown(Decimal("1000")))
        self.assertTrue(sup.check_drawdown(Decimal("970")))
        with self.assertRaises(ValueError):
            sup.check_drawdown(Decimal("NaN"))
        self.assertTrue(GridBotSupervisor(self.b, "USD").check_drawdown(Decimal("1000")))

    def test_cancel_failure_keeps_sticky_halt_after_restart(self):
        sup = GridBotSupervisor(self.a, "USD")
        sup.check_drawdown(Decimal("1000"))
        self.venue.cancel_all_orders = lambda: (_ for _ in ()).throw(RuntimeError("lost DELETE"))
        with self.assertRaises(RuntimeError):
            sup.check_drawdown(Decimal("970"))
        self.assertEqual(self.b.connection.execute(
            "SELECT value FROM bot_metadata WHERE key='kill_switch_triggered'"
        ).fetchone()[0], "true")
        with self.assertRaises(ReconciliationError):
            self.submit(self.b)


if __name__ == "__main__":
    unittest.main()
