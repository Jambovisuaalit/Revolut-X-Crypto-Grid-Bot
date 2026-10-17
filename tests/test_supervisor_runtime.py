"""Offline supervisor lifecycle safety tests: never connect to Revolut X."""
from __future__ import annotations

from decimal import Decimal
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bot_main import GridBotSupervisor
from grid_executor import GridExecutor
from supervisor_runtime import (
    OfflineFixtureSource, PortfolioSample, SupervisorRuntime,
    SupervisorState, SupervisorBlocked,
)


class Venue:
    """All network mutations forbidden, even in error scenarios."""
    def __init__(self):
        self.active = []
        self.reads = 0
        self.mutations = 0

    def get_open_orders(self):
        self.reads += 1
        return list(self.active)

    def get_historical_orders(self):
        return []

    def place_order(self, *a, **kw):
        self.mutations += 1
        raise AssertionError("POST forbidden")

    def replace_order(self, *a, **kw):
        self.mutations += 1
        raise AssertionError("PUT forbidden")

    def cancel_all_orders(self):
        self.mutations += 1
        raise AssertionError("DELETE forbidden")


class Feed:
    def __init__(self, clock, equities):
        self.clock = clock
        self.equities = list(equities)
        self.index = 0

    def read(self):
        value = self.equities[min(self.index, len(self.equities)-1)]
        self.index += 1
        return PortfolioSample(self.clock[0], self.clock[0],
                               {"EUR": Decimal(value)}, {})


class OfflineSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"offline.sqlite"
        self.clock = [1_792_214_000_000]
        self.venue = Venue()
        self.executor = GridExecutor(
            self.venue, {}, database_path=str(self.path), live_trading=False
        )
        self.addCleanup(self.executor.close)

    def make(self, feed=None, owner=None):
        sup = GridBotSupervisor(self.executor, "EUR")
        return SupervisorRuntime(
            sup, feed or OfflineFixtureSource(lambda:self.clock[0]),
            clock_ms=lambda:self.clock[0], owner=owner,
        )

    def meta(self, key):
        return self.executor.connection.execute(
            "SELECT value FROM bot_metadata WHERE key=?", (key,)
        ).fetchone()

    def test_01_bounded_offline_stops_and_does_not_trade(self):
        runner = self.make(owner="one")
        result = runner.run_bounded(3, sleeper=lambda _:self.clock.__setitem__(0, self.clock[0]+1000))
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["ticks_completed"], 3)
        self.assertEqual(result["state"], "STOPPED")
        self.assertEqual(result["last_equity_eur"], "1000")
        self.assertEqual(result["exchange_orders_sent"], 0)
        self.assertEqual(result["authenticated_account_test"], "NOT_RUN")
        self.assertEqual(self.venue.mutations, 0)
        self.assertEqual(self.meta("supervisor_state")[0], "STOPPED")
        self.assertEqual(self.meta("supervisor_last_equity_eur")[0], "1000")
        self.assertEqual(self.venue.reads, 1)

    def test_02_restart_preserves_baseline_and_stopped_state(self):
        self.make(owner="one").run_bounded(1)
        self.assertEqual(self.meta("initial_equity")[0], "1000")
        other = self.make(owner="two")
        self.assertEqual(other.run_bounded(1)["result"], "PASS")
        self.assertEqual(self.meta("initial_equity")[0], "1000")

    def test_03_exact_three_percent_drawdown_sticky_after_restart(self):
        feed = Feed(self.clock,["1000","970"])
        runner = self.make(feed, owner="one")
        report = runner.run_bounded(2, sleeper=lambda _:self.clock.__setitem__(0,self.clock[0]+1000))
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(report["state"], "RISK_LOCKED")
        self.assertEqual(self.meta("supervisor_state")[0], "RISK_LOCKED")
        self.assertEqual(self.meta("kill_switch_triggered")[0], "true")
        self.assertEqual(self.venue.mutations, 0)
        self.assertEqual(self.make(owner="two").run_bounded(1)["state"], "RISK_LOCKED")

    def test_04_stale_sample_blocks_and_persists(self):
        past = self.clock[0] - 4000
        feed = type("Stale",(),{"read":lambda s: PortfolioSample(past,past,{"EUR":Decimal("1000")},{})})()
        report=self.make(feed).run_bounded(1)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(self.meta("supervisor_reason")[0], "STALE_PORTFOLIO_SAMPLE")
        self.assertEqual(self.venue.mutations,0)

    def test_05_foreign_provenance_is_rejected(self):
        feed=type("Other",(),{"read":lambda s:PortfolioSample(
            self.clock[0],self.clock[0],{"EUR":Decimal("1000")},{},"AUTHENTICATED_ACCOUNT")})()
        result=self.make(feed).run_bounded(1)
        self.assertEqual(result["state"],"BLOCKED")
        self.assertEqual(self.meta("supervisor_reason")[0],"NON_OFFLINE_SOURCE_FORBIDDEN")

    def test_06_multiple_instances_cannot_own_same_sqlite(self):
        first=self.make(owner="one")
        second=self.make(owner="two")
        first.start()
        with self.assertRaisesRegex(SupervisorBlocked,"ALREADY_OWNED"):
            second.start()
        self.assertEqual(self.meta("supervisor_owner")[0],"one")
        first.stop()
        self.assertEqual(self.meta("supervisor_owner")[0],"")
        self.assertEqual(second.run_bounded(1)["result"],"PASS")

    def test_07_crash_lease_expiry_and_no_foreign_lease_release(self):
        first=self.make(owner="one")
        first.start()
        self.clock[0]+=16_000
        second=self.make(owner="two")
        second.start()
        with self.assertRaisesRegex(SupervisorBlocked,"LEASE_LOST"):
            first.tick()
        first.stop()
        self.assertEqual(self.meta("supervisor_owner")[0],"two")
        second.stop()

    def test_08_unknown_exchange_order_blocks_startup(self):
        self.venue.active=[{"id":"unknown-order"}]
        report=self.make().run_bounded(1)
        self.assertEqual(report["state"],"BLOCKED")
        self.assertEqual(self.meta("supervisor_reason")[0],"STARTUP_RECONCILIATION_FAILED")
        self.assertEqual(self.venue.mutations,0)

    def test_09_live_executor_and_live_env_rejected(self):
        existing=self.executor.live_trading
        self.executor.live_trading=True
        with self.assertRaisesRegex(SupervisorBlocked,"LIVE_EXECUTOR_FORBIDDEN"):
            self.make()
        self.executor.live_trading=existing
        with patch.dict(os.environ,{"LIVE_TRADING":"true"}):
            with self.assertRaisesRegex(SupervisorBlocked,"LIVE_ENV_FORBIDDEN"):
                self.make()

    def test_10_nan_or_negative_balance_fails_closed(self):
        for raw in ["NaN","-1"]:
            with self.subTest(raw=raw):
                feed=Feed(self.clock,[raw])
                if self.meta("supervisor_state") is not None:
                    # Keep test samples in independent databases per case.
                    self.executor.close()
                    self.path=Path(self.temp.name)/("offline-"+raw+".sqlite")
                    self.executor=GridExecutor(self.venue,{},database_path=str(self.path),live_trading=False)
                    self.addCleanup(self.executor.close)
                result=self.make(feed).run_bounded(1)
                self.assertEqual(result["state"],"BLOCKED")
                self.assertEqual(self.venue.mutations,0)

    def test_11_clock_regression_fails_closed(self):
        feed=type("Backwards",(),{
            "i":0,
            "read":lambda s:self._regression_sample(s)})()
        report=self.make(feed).run_bounded(2,sleeper=lambda _:self.clock.__setitem__(0,self.clock[0]+1000))
        self.assertEqual(report["state"],"BLOCKED")
        self.assertEqual(self.meta("supervisor_reason")[0],"BACKWARDS_SAMPLE_TIMESTAMP")

    def _regression_sample(self, src):
        src.i+=1
        source_ms=self.clock[0] if src.i==1 else self.clock[0]-1500
        return PortfolioSample(source_ms,self.clock[0],{"EUR":Decimal("1000")},{})

    def test_12_unexpected_source_error_is_safe_and_redacted(self):
        def fail(_self):
            raise RuntimeError("SECRET-DO-NOT-LOG")
        feed=type("Fault",(),{"read":fail})()
        result=self.make(feed).run_bounded(1)
        self.assertEqual(result["state"],"BLOCKED")
        self.assertNotIn("SECRET",self.meta("supervisor_reason")[0])

    def test_13_unbounded_configuration_rejected(self):
        r=self.make()
        for bad in [0,-1,1001,True]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                r.run_bounded(bad)
        self.assertEqual(self.venue.reads,0)


if __name__=="__main__":
    unittest.main()
