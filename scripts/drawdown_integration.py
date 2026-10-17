"""Offline integration gate: persisted 3% drawdown halt and restart rejection."""
import json
import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from bot_main import GridBotSupervisor
from grid_executor import GridExecutor, ReconciliationError
from precision_engine import PairRules

CID = "00000000-0000-4000-8000-000000000099"


class Venue:
    def __init__(self):
        self.post_count = 0
        self.cancel_count = 0

    def get_open_orders(self):
        return []

    def get_historical_orders(self):
        return []

    def place_order(self, payload):
        self.post_count += 1
        raise AssertionError("order POST must never occur after halt")

    def cancel_all_orders(self):
        self.cancel_count += 1


def make_executor(path, venue):
    rules = PairRules("BTC/USD", Decimal("0.00000001"), Decimal("0.01"),
                      Decimal("0.0001"), Decimal("10"), "active")
    return GridExecutor(venue, {"BTC/USD": rules}, database_path=str(path), live_trading=True)


def attempt_order(executor):
    return executor.create_order(
        client_order_id=CID, symbol="BTC/USD", side="buy",
        price=Decimal("100"), quantity=Decimal("0.01"),
        free_balances={"USD": Decimal("100000"), "BTC": Decimal("10")},
    )


def run_case():
    venue = Venue()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "drawdown.sqlite"
        first = make_executor(db, venue)
        try:
            supervisor = GridBotSupervisor(first, "USD")
            first.reconcile_open_orders()
            assert supervisor.check_drawdown(Decimal("10000")) is False
            assert supervisor.check_drawdown(Decimal("9701")) is False
            assert supervisor.check_drawdown(Decimal("9690")) is True
            assert supervisor.kill_switch_triggered is True
            row = first.connection.execute(
                "SELECT value FROM bot_metadata WHERE key='kill_switch_triggered'"
            ).fetchone()
            assert row is not None and row[0] == "true", "SQLite kill switch not persisted"
            assert venue.cancel_count == 1, "cancel-all not invoked exactly once"
            try:
                attempt_order(first)
            except ReconciliationError:
                pass
            else:
                raise AssertionError("halted process allowed new order")
        finally:
            first.close()

        second = make_executor(db, venue)
        try:
            restarted = GridBotSupervisor(second, "USD")
            assert restarted.kill_switch_triggered is True
            try:
                restarted.start()
            except RuntimeError:
                pass
            else:
                raise AssertionError("restart ignored persisted kill switch")
            try:
                second.reconcile_open_orders()
                attempt_order(second)
            except ReconciliationError:
                pass
            else:
                raise AssertionError("restart allowed new order")
            assert venue.post_count == 0, "unexpected POST after kill switch"
            persisted = second.connection.execute(
                "SELECT value FROM bot_metadata WHERE key='kill_switch_triggered'"
            ).fetchone()
            assert persisted is not None and persisted[0] == "true"
        finally:
            second.close()
        return {
            "baseline_equity": "10000",
            "pre_threshold_equity": "9701",
            "breach_equity": "9690",
            "drawdown_percent": "3.10",
            "kill_switch_persisted": True,
            "restart_blocked": True,
            "post_count": venue.post_count,
            "cancel_all_count": venue.cancel_count,
            "status": "PASS",
        }


def main():
    out = Path("recovery-artifacts")
    out.mkdir(exist_ok=True)
    try:
        result = run_case()
    except Exception as exc:
        result = {"status": "FAIL", "error": repr(exc)}
    target = out / ("drawdown-" + sys.platform + ".json")
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
