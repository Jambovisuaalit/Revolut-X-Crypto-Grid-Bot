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

    def check_drawdown(self, current_equity: Decimal) -> bool:
        """Persist initial equity and immediately cancel/halt at 3.0% drawdown."""
        if current_equity < 0:
            raise ValueError("equity cannot be negative")
        initial_text = self._metadata("initial_equity")
        if initial_text is None:
            if current_equity <= 0:
                raise ValueError("initial equity must be positive")
            self._set_metadata("initial_equity", decimal_text(current_equity))
            return False
        initial = decimal_from_api(initial_text, "initial_equity")
        drawdown = (initial - current_equity) / initial * Decimal("100")
        if drawdown >= self.DRAWDOWN_LIMIT_PERCENT:
            # A failed/uncertain cancellation still halts the local engine.
            self.kill_switch_triggered = True
            self._set_metadata("kill_switch_triggered", "true")
            if self.executor.live_trading:
                self.executor.client.cancel_all_orders()
            LOGGER.critical("drawdown kill-switch triggered; execution halted")
            return True
        return False


def live_trading_enabled() -> bool:
    """Return true only for the explicit opt-in environment value ``true``."""
    return os.environ.get("LIVE_TRADING", "false").strip().lower() == "true"

