"""P0 offline cancellation verification: no venue mutations except first cancel."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from bot_main import GridBotSupervisor
from grid_executor import GridExecutor, ReconciliationError
from test_bot import rules, uid


class Venue:
    def __init__(self, active=None, error=None):
        self.active = [] if active is None else active
        self.error = error
        self.cancel_calls = 0
        self.cancel_attempted = False
        self.post_calls = 0

    def get_open_orders(self):
        if self.error and self.cancel_attempted:
            raise self.error
        return self.active if self.cancel_attempted else []

    def get_historical_orders(self):
        return []

    def cancel_all_orders(self):
        self.cancel_calls += 1
        self.cancel_attempted = True

    def place_order(self, payload):
        self.post_calls += 1
        raise AssertionError("POST must be blocked")


class CancellationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "bot.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def make(self, venue):
        return GridExecutor(venue, {"BTC/USD": rules()}, database_path=self.db, live_trading=True)

    def assert_blocked(self, exe):
        with self.assertRaises(ReconciliationError):
            exe.create_order(client_order_id=uid(), symbol="BTC/USD", side="buy",
                             price=Decimal("100"), quantity=Decimal(".01"),
                             free_balances={"USD": Decimal("100000")})

    def exercise(self, venue, expected):
        exe = self.make(venue)
        try:
            exe.reconcile_open_orders()
            supervisor = GridBotSupervisor(exe, "USD")
            self.assertFalse(supervisor.check_drawdown(Decimal("10000")))
            self.assertTrue(supervisor.check_drawdown(Decimal("9690")))
            self.assertEqual(supervisor._metadata("cancellation_verification_status"), expected)
            self.assertEqual(supervisor._metadata("kill_switch_triggered"), "true")
            self.assert_blocked(exe)
            self.assertEqual(venue.cancel_calls, 1)
        finally:
            exe.close()
        restarted = self.make(venue)
        try:
            supervisor = GridBotSupervisor(restarted, "USD")
            with self.assertRaises(RuntimeError):
                supervisor.start()
            self.assert_blocked(restarted)
            self.assertEqual(supervisor._metadata("cancellation_verification_status"), expected)
            self.assertEqual(venue.post_calls, 0)
            self.assertEqual(venue.cancel_calls, 1)
        finally:
            restarted.close()

    def test_no_active_orders_verified_and_halt_remains(self):
        self.exercise(Venue(), "VERIFIED_NO_ACTIVE_ORDERS")

    def test_active_orders_unverified_and_halt_remains(self):
        self.exercise(Venue(active=[{"id": "still-open"}]), "CANCELLATION_UNVERIFIED")

    def test_read_failure_unverified_and_halt_remains(self):
        self.exercise(Venue(error=TimeoutError("venue unavailable")), "CANCELLATION_UNVERIFIED")

    def test_cancel_timeout_is_persisted_before_exception(self):
        venue = Venue()
        def fail():
            venue.cancel_calls += 1
            venue.cancel_attempted = True
            raise TimeoutError("DELETE acknowledgement lost")
        venue.cancel_all_orders = fail
        exe = self.make(venue)
        try:
            exe.reconcile_open_orders()
            sup = GridBotSupervisor(exe, "USD")
            sup.check_drawdown(Decimal("10000"))
            with self.assertRaises(TimeoutError):
                sup.check_drawdown(Decimal("9690"))
            self.assertEqual(sup._metadata("cancellation_verification_status"), "CANCELLATION_UNVERIFIED")
            self.assert_blocked(exe)
        finally:
            exe.close()
        second = self.make(venue)
        try:
            self.assert_blocked(second)
            self.assertEqual(GridBotSupervisor(second, "USD")._metadata("kill_switch_triggered"), "true")
            self.assertEqual(venue.cancel_calls, 1)
        finally:
            second.close()


if __name__ == "__main__":
    unittest.main()
