"""Supervisor loop for the Revolut X post-only grid bot MVP."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from grid_executor import GridExecutor
from precision_engine import PairRules
from revolut_x_client import RevolutXClient, RevolutXError

LOGGER = logging.getLogger(__name__)
KILL_SWITCH_DRAWDOWN_PCT = Decimal("3.0")


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Environment-backed runtime configuration."""

    api_key: str
    private_key_path: str
    symbol: str
    quote_currency: str
    region: str | None
    levels_per_side: int
    spacing_bps: Decimal
    allocation_pct: Decimal
    recenter_threshold_bps: Decimal
    loop_interval_seconds: int
    max_market_age_ms: int
    dry_run: bool
    db_path: str

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Load and validate runtime configuration from environment variables."""
        dry_run = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes"}
        live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() in {"1", "true", "yes"}
        if not dry_run and not live_enabled:
            raise ValueError("LIVE_TRADING_ENABLED=true is required when DRY_RUN=false")
        allocation_pct = Decimal(os.getenv("ORDER_ALLOCATION_PCT", "1.0"))
        if allocation_pct <= Decimal("0") or allocation_pct > Decimal("5"):
            raise ValueError("ORDER_ALLOCATION_PCT must be > 0 and <= 5")
        return cls(
            api_key=os.environ["REVX_API_KEY"],
            private_key_path=os.environ["REVX_PRIVATE_KEY_PATH"],
            symbol=os.getenv("REVX_SYMBOL", "BTC-USD"),
            quote_currency=os.getenv("QUOTE_CURRENCY", "USD"),
            region=os.getenv("REVX_REGION") or None,
            levels_per_side=int(os.getenv("GRID_LEVELS_PER_SIDE", "3")),
            spacing_bps=Decimal(os.getenv("GRID_SPACING_BPS", "25")),
            allocation_pct=allocation_pct,
            recenter_threshold_bps=Decimal(os.getenv("RECENTER_THRESHOLD_BPS", "75")),
            loop_interval_seconds=int(os.getenv("LOOP_INTERVAL_SECONDS", "5")),
            max_market_age_ms=int(os.getenv("MAX_MARKET_AGE_MS", "5000")),
            dry_run=dry_run,
            db_path=os.getenv("BOT_STATE_DB", "bot_state.db"),
        )


def _pair_key(symbol: str) -> str:
    return symbol.replace("-", "/")


def _balances_total(balances: list[Mapping[str, Any]]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for item in balances:
        currency = str(item.get("currency", ""))
        if currency:
            result[currency] = Decimal(str(item.get("total", "0")))
    return result


def _ticker_map(tickers: list[Mapping[str, Any]]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for ticker in tickers:
        symbol = str(ticker.get("symbol", ""))
        mid = ticker.get("mid")
        if symbol and mid is not None:
            result[symbol] = Decimal(str(mid))
    return result


def portfolio_equity(
    *,
    balances: list[Mapping[str, Any]],
    tickers: list[Mapping[str, Any]],
    quote_currency: str,
) -> Decimal:
    """Mark all non-zero balances to one quote currency using current mid prices."""
    totals = _balances_total(balances)
    prices = _ticker_map(tickers)
    equity = Decimal("0")
    for currency, amount in totals.items():
        if amount == Decimal("0"):
            continue
        if currency == quote_currency:
            equity += amount
            continue
        direct = f"{currency}/{quote_currency}"
        inverse = f"{quote_currency}/{currency}"
        if direct in prices:
            equity += amount * prices[direct]
        elif inverse in prices and prices[inverse] > Decimal("0"):
            equity += amount / prices[inverse]
        else:
            raise RevolutXError(f"cannot mark {currency} balance to {quote_currency}; missing ticker")
    if equity <= Decimal("0"):
        raise RevolutXError("portfolio equity is not positive")
    return equity


def drawdown_pct(initial: Decimal, current: Decimal) -> Decimal:
    """Calculate drawdown percentage using Decimal only."""
    if initial <= Decimal("0"):
        raise ValueError("initial equity must be positive")
    return (initial - current) / initial * Decimal("100")


def _market_snapshot(client: RevolutXClient, symbol: str, max_age_ms: int) -> tuple[Decimal, Decimal, Decimal, list[dict[str, Any]]]:
    tickers, exchange_ts = client.get_tickers()
    local_now_ms = int(time.time() * 1000)
    if exchange_ts > local_now_ms + 5_000:
        raise RevolutXError("exchange timestamp is unexpectedly ahead of local clock")
    if local_now_ms - exchange_ts > max_age_ms:
        raise RevolutXError("market data is stale")
    pair = _pair_key(symbol)
    ticker = next((item for item in tickers if str(item.get("symbol")) == pair), None)
    if ticker is None:
        raise RevolutXError(f"ticker not found for {pair}")
    bid = Decimal(str(ticker["bid"]))
    ask = Decimal(str(ticker["ask"]))
    mid = Decimal(str(ticker["mid"]))
    if bid <= Decimal("0") or ask <= Decimal("0") or mid <= Decimal("0") or bid >= ask:
        raise RevolutXError("invalid ticker spread")
    return bid, ask, mid, tickers


def run() -> int:
    """Run the supervisor until stopped or the kill-switch fires."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = BotConfig.from_env()
    except (KeyError, ValueError) as exc:
        LOGGER.error("configuration error: %s", exc)
        return 2

    client = RevolutXClient(config.api_key, config.private_key_path)
    executor = GridExecutor(client, db_path=config.db_path, dry_run=config.dry_run)
    stop = False

    def _stop_handler(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    try:
        if executor.get_meta("kill_switch_triggered") == "true":
            LOGGER.critical("kill-switch is already persisted; refusing to start")
            return 3

        pair_config = client.get_pair_configuration(config.region)
        pair_key = _pair_key(config.symbol)
        raw_rules = pair_config.get(pair_key)
        if raw_rules is None:
            raise RevolutXError(f"pair configuration not found for {pair_key}")
        rules = PairRules.from_api(pair_key, raw_rules)

        executor.reconcile(config.symbol)
        LOGGER.info("Starting bot symbol=%s dry_run=%s", config.symbol, config.dry_run)

        while not stop:
            bid, ask, mid, tickers = _market_snapshot(client, config.symbol, config.max_market_age_ms)
            balances = client.get_balances()
            current_equity = portfolio_equity(
                balances=balances,
                tickers=tickers,
                quote_currency=config.quote_currency,
            )
            initial_equity = executor.get_or_set_initial_equity(current_equity)
            dd = drawdown_pct(initial_equity, current_equity)
            LOGGER.info("equity=%s %s drawdown=%s%%", current_equity, config.quote_currency, dd)

            if dd >= KILL_SWITCH_DRAWDOWN_PCT:
                executor.trigger_kill_switch()
                return 10

            executor.reconcile(config.symbol)
            executor.maintain_grid(
                symbol=config.symbol,
                rules=rules,
                center=mid,
                best_bid=bid,
                best_ask=ask,
                levels_per_side=config.levels_per_side,
                spacing_bps=config.spacing_bps,
                allocation_pct=config.allocation_pct,
                recenter_threshold_bps=config.recenter_threshold_bps,
            )
            time.sleep(config.loop_interval_seconds)
        return 0
    except (RevolutXError, ValueError, ArithmeticError) as exc:
        LOGGER.error("fail-closed stop: %s", exc)
        return 1
    finally:
        executor.close()


if __name__ == "__main__":
    sys.exit(run())
