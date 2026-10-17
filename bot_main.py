"""Fail-closed supervisor and portfolio-equity drawdown kill-switch."""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Mapping

from grid_executor import GridExecutor
from precision_engine import decimal_from_api, decimal_text

LOGGER = logging.getLogger(__name__)


class GridBotSupervisor:
    """Own startup reconciliation and the hard drawdown guard."""

    DRAWDOWN_LIMIT_PERCENT = Decimal("3.0")

    def __init__(self, executor: GridExecutor, quote_currency: str) -> None:
        """Initialize a supervisor whose equity is denominated in one quote currency."""
        self.executor = executor
        self.quote_currency = quote_currency.upper()
        self.kill_switch_triggered = self._metadata("kill_switch_triggered") == "true"

    def _metadata(self, key: str) -> str | None:
        row = self.executor.connection.execute("SELECT value FROM bot_metadata WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def _set_metadata(self, key: str, value: str) -> None:
        self.executor.connection.execute(
            "INSERT INTO bot_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def start(self) -> None:
        """Reconcile exchange state before any order operation is enabled."""
        if self.kill_switch_triggered:
            raise RuntimeError("kill-switch is persisted; manual intervention required")
        self.executor.reconcile_open_orders()

    def mark_to_market(self, balances: Mapping[str, Decimal], quote_prices: Mapping[str, Decimal]) -> Decimal:
        """Calculate total portfolio equity in the configured quote currency."""
        equity = Decimal("0")
        for currency, amount in balances.items():
            if amount < 0:
                raise ValueError("balance cannot be negative")
            currency = currency.upper()
            if currency == self.quote_currency:
                equity += amount
            else:
                pair = f"{currency}/{self.quote_currency}"
                if pair not in quote_prices or quote_prices[pair] <= 0:
                    raise ValueError(f"missing valid mark for {pair}")
                equity += amount * quote_prices[pair]
        return equity

    def verify_cancellation(self) -> bool:
        """Read-only verification of the venue's active orders after a halt."""
        if self._metadata("kill_switch_triggered") != "true":
            raise RuntimeError("verification requires a persisted kill switch")
        try:
            active = self.executor.client.get_open_orders()
            if not isinstance(active, list) or active:
                raise ValueError("active orders remain or snapshot is invalid")
        except Exception:
            with self.executor._write_transaction():
                self._set_metadata("cancellation_verification_status", "CANCELLATION_UNVERIFIED")
            LOGGER.exception("cancellation unverified; trading remains halted")
            return False
        with self.executor._write_transaction():
            self._set_metadata("cancellation_verification_status", "VERIFIED_NO_ACTIVE_ORDERS")
        return True

    def check_drawdown(self, current_equity: Decimal) -> bool:
        """Atomically persist baseline and sticky halt across SQLite processes."""
        if not isinstance(current_equity, Decimal) or not current_equity.is_finite() or current_equity < 0:
            raise ValueError("equity must be a finite nonnegative Decimal")
        with self.executor._write_transaction():
            initial_text = self._metadata("initial_equity")
            if initial_text is None:
                if current_equity <= 0:
                    raise ValueError("initial equity must be positive")
                self._set_metadata("initial_equity", decimal_text(current_equity))
                return False
            initial = decimal_from_api(initial_text, "initial_equity")
            if initial <= 0:
                raise ValueError("persisted initial equity must be positive")
            breached = (initial - current_equity) / initial * Decimal("100") >= self.DRAWDOWN_LIMIT_PERCENT
            already_halted = self._metadata("kill_switch_triggered") == "true"
            if breached and not already_halted:
                self._set_metadata("kill_switch_triggered", "true")
                self._set_metadata("cancellation_verification_status", "CANCELLATION_UNVERIFIED")
            halted = breached or already_halted
        self.kill_switch_triggered = halted
        if halted:
            if breached and not already_halted and self.executor.live_trading:
                try:
                    self.executor.client.cancel_all_orders()
                except Exception:
                    LOGGER.exception("cancel-all uncertain; kill switch remains persisted")
                    raise
                self.verify_cancellation()
            LOGGER.critical("persistent drawdown kill-switch triggered; execution halted")
        return halted


def live_trading_enabled() -> bool:
    """Return true only for the explicit opt-in environment value ``true``."""
    return os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"




def offline_main(argv: list[str] | None = None) -> int:
    """Explicit, bounded fixture CLI; never constructs the exchange client."""
    import argparse
    import json
    import pathlib
    import sys
    import time

    from supervisor_runtime import OfflineFixtureSource, SupervisorRuntime

    parser = argparse.ArgumentParser(
        description="GridBotSupervisor offline lifecycle ONLY. No API, no live orders."
    )
    parser.add_argument("--offline-fixture", action="store_true",
                        help="Required explicit synthetic-only execution mode")
    parser.add_argument("--db", type=pathlib.Path, required=False,
                        help="Dedicated NEW/fixture-only SQLite path, never production bot_state.db")
    parser.add_argument("--ticks", type=int, default=3,
                        help="Bounded observations (1..1000), never daemon mode")
    parser.add_argument("--interval-ms", type=int, default=100,
                        help="Delay between fixture observations, 0..60000")
    parser.add_argument("--fixture-equity", type=str, default="1000",
                        help="Synthetic EUR balance, not customer money")
    parser.add_argument("--report", type=pathlib.Path)
    args = parser.parse_args(argv)
    if not args.offline_fixture or args.db is None:
        parser.error("--offline-fixture and --db must both be explicitly supplied")
    if not 1 <= args.ticks <= 1000 or not 0 <= args.interval_ms <= 60000:
        parser.error("invalid bounded ticks or interval")
    if args.db.name.lower() in ("bot_state.db", "collector.sqlite"):
        parser.error("refusing live or collector DB filename")
    if live_trading_enabled() or os.getenv("LIVE_TRADING","false").strip().lower() != "false":
        parser.error("LIVE_TRADING must be false")
    try:
        equity = Decimal(args.fixture_equity)
    except Exception:
        parser.error("fixture equity must be Decimal")
    if not equity.is_finite() or equity <= 0:
        parser.error("fixture equity must be positive")

    # Read-only fake venue: active order reconciliation is still exercised;
    # this CLI has *no authenticated exchange client*, not even via imports.
    class OfflineVenue:
        def get_open_orders(self) -> list:
            return []

        def get_historical_orders(self) -> list:
            return []

        def place_order(self, *_args, **_kwargs) -> None:
            raise RuntimeError("OFFLINE_ORDERS_FORBIDDEN")

        def replace_order(self, *_args, **_kwargs) -> None:
            raise RuntimeError("OFFLINE_REPLACE_FORBIDDEN")

        def cancel_all_orders(self) -> None:
            raise RuntimeError("OFFLINE_CANCEL_FORBIDDEN")

    # Explicit safety check before GridExecutor can create/alter any schema.
    # An existing file without the offline marker may belong to another bot.
    if args.db.exists():
        import sqlite3
        from contextlib import closing
        try:
            with closing(sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True)) as connection:
                marker=connection.execute(
                    "SELECT value FROM bot_metadata WHERE key='supervisor_runtime_kind'"
                ).fetchone()
                if marker is None or marker[0] != "SYNTHETIC_OFFLINE_ONLY":
                    print("BLOCKED: existing DB is not an offline fixture", file=sys.stderr)
                    return 2
                occupied=connection.execute("SELECT COUNT(*) FROM order_lineage").fetchone()[0]
                if occupied:
                    print("BLOCKED: existing DB contains order history", file=sys.stderr)
                    return 2
        except sqlite3.Error:
            print("BLOCKED: existing DB could not be verified", file=sys.stderr)
            return 2
    args.db.parent.mkdir(parents=True, exist_ok=True)
    from grid_executor import GridExecutor
    executor = GridExecutor(OfflineVenue(), {}, database_path=str(args.db), live_trading=False)
    try:
        # Never use fixture controller on an existing real order journal.
        existing = executor.connection.execute("SELECT COUNT(*) FROM order_lineage").fetchone()[0]
        if existing:
            print("BLOCKED: fixture runner refuses a DB with order history", file=sys.stderr)
            return 2
        mode = executor.connection.execute(
            "SELECT value FROM bot_metadata WHERE key='supervisor_runtime_kind'"
        ).fetchone()
        if mode is not None and mode[0] != "SYNTHETIC_OFFLINE_ONLY":
            print("BLOCKED: DB belongs to a different runtime", file=sys.stderr)
            return 2
        with executor._write_transaction():
            executor.connection.execute(
                "INSERT INTO bot_metadata(key,value) VALUES ('supervisor_runtime_kind','SYNTHETIC_OFFLINE_ONLY') "
                "ON CONFLICT(key) DO NOTHING"
            )
        clock = lambda: time.time_ns() // 1_000_000
        supervisor = GridBotSupervisor(executor, "EUR")
        runtime = SupervisorRuntime(
            supervisor, OfflineFixtureSource(clock, equity), clock_ms=clock
        )
        report = runtime.run_bounded(
            args.ticks, poll_interval_seconds=args.interval_ms / 1000
        )
    finally:
        executor.close()

    if args.report:
        # A report is evidence only for the offline fixture and never claims live readiness.
        import tempfile
        args.report.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.report.with_name(args.report.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(report, handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, args.report)
        finally:
            if tmp.exists():
                tmp.unlink()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(offline_main())
