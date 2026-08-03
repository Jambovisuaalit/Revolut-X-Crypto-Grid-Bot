"""Supervisor loop for the Revolut X post-only grid bot MVP."""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from grid_executor import GridExecutor
from precision_engine import PairRules, decimal_from_api
from revolut_x_client import RevolutXClient, RevolutXError

LOGGER = logging.getLogger(__name__)

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
BPS_BASE = Decimal("10000")
KILL_SWITCH_DRAWDOWN_PCT = Decimal("3.0")
MAX_CLOCK_SKEW_MS = 5_000


def _require_decimal(value: object, *, field_name: str) -> Decimal:
    """Require a finite Decimal for an in-memory financial calculation."""
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal; got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _parse_bool_env(name: str, default: str) -> bool:
    raw = os.getenv(name, default).strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true/false")


def _parse_decimal_env(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal string") from exc
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def _parse_positive_int_env(name: str, default: str) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _order_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("/", "-")
    parts = normalized.split("-")
    if len(parts) != 2 or not all(parts):
        raise ValueError("REVX_SYMBOL must be a currency pair such as BTC-USD")
    return normalized


def _pair_key(symbol: str) -> str:
    """Convert order symbol form to configuration/ticker pair form."""
    return _order_symbol(symbol).replace("-", "/")


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

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if not self.private_key_path:
            raise ValueError("private_key_path must not be empty")
        if not self.db_path:
            raise ValueError("db_path must not be empty")
        if self.symbol != _order_symbol(self.symbol):
            raise ValueError("symbol must be normalized to BASE-QUOTE")
        if not self.quote_currency or self.quote_currency != self.quote_currency.upper():
            raise ValueError("quote_currency must be uppercase")
        if self.levels_per_side < 1:
            raise ValueError("levels_per_side must be >= 1")
        if self.loop_interval_seconds <= 0:
            raise ValueError("loop_interval_seconds must be positive")
        if self.max_market_age_ms <= 0:
            raise ValueError("max_market_age_ms must be positive")

        spacing = _require_decimal(self.spacing_bps, field_name="spacing_bps")
        allocation = _require_decimal(self.allocation_pct, field_name="allocation_pct")
        recenter = _require_decimal(
            self.recenter_threshold_bps,
            field_name="recenter_threshold_bps",
        )
        if spacing <= ZERO:
            raise ValueError("spacing_bps must be positive")
        if spacing * Decimal(self.levels_per_side) >= BPS_BASE:
            raise ValueError("outer grid level must remain below 100% from center")
        if allocation <= ZERO or allocation > Decimal("5"):
            raise ValueError("allocation_pct must be > 0 and <= 5")
        if recenter <= ZERO:
            raise ValueError("recenter_threshold_bps must be positive")

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Load and strictly validate runtime configuration from environment."""
        dry_run = _parse_bool_env("DRY_RUN", "true")
        live_enabled = _parse_bool_env("LIVE_TRADING_ENABLED", "false")
        if not dry_run and not live_enabled:
            raise ValueError(
                "LIVE_TRADING_ENABLED=true is required when DRY_RUN=false"
            )

        api_key = os.environ["REVX_API_KEY"].strip()
        private_key_path = os.environ["REVX_PRIVATE_KEY_PATH"].strip()
        symbol = _order_symbol(os.getenv("REVX_SYMBOL", "BTC-USD"))
        quote_currency = os.getenv("QUOTE_CURRENCY", "USD").strip().upper()
        region_raw = os.getenv("REVX_REGION", "").strip()
        levels_per_side = _parse_positive_int_env("GRID_LEVELS_PER_SIDE", "3")

        return cls(
            api_key=api_key,
            private_key_path=private_key_path,
            symbol=symbol,
            quote_currency=quote_currency,
            region=region_raw or None,
            levels_per_side=levels_per_side,
            spacing_bps=_parse_decimal_env("GRID_SPACING_BPS", "25"),
            allocation_pct=_parse_decimal_env("ORDER_ALLOCATION_PCT", "1.0"),
            recenter_threshold_bps=_parse_decimal_env(
                "RECENTER_THRESHOLD_BPS",
                "75",
            ),
            loop_interval_seconds=_parse_positive_int_env(
                "LOOP_INTERVAL_SECONDS",
                "5",
            ),
            max_market_age_ms=_parse_positive_int_env(
                "MAX_MARKET_AGE_MS",
                "5000",
            ),
            dry_run=dry_run,
            db_path=os.getenv("BOT_STATE_DB", "bot_state.db").strip(),
        )


def _balances_total(
    balances: list[Mapping[str, Any]],
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for item in balances:
        currency_raw = item.get("currency")
        if not isinstance(currency_raw, str) or not currency_raw:
            raise RevolutXError("balance entry missing currency")
        currency = currency_raw.upper()
        if currency in result:
            raise RevolutXError(f"duplicate balance currency: {currency}")
        if "total" not in item:
            raise RevolutXError(f"balance entry missing total for {currency}")
        total = decimal_from_api(
            item["total"],
            field_name=f"{currency}.total",
        )
        if total < ZERO:
            raise RevolutXError(f"negative total balance for {currency}")
        result[currency] = total
    return result


def _ticker_map(
    tickers: list[Mapping[str, Any]],
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for ticker in tickers:
        symbol_raw = ticker.get("symbol")
        if not isinstance(symbol_raw, str) or not symbol_raw:
            raise RevolutXError("ticker entry missing symbol")
        symbol = _pair_key(symbol_raw)
        if symbol in result:
            raise RevolutXError(f"duplicate ticker symbol: {symbol}")
        if "mid" not in ticker:
            raise RevolutXError(f"ticker entry missing mid for {symbol}")
        mid = decimal_from_api(
            ticker["mid"],
            field_name=f"{symbol}.mid",
        )
        if mid <= ZERO:
            raise RevolutXError(f"ticker mid must be positive for {symbol}")
        result[symbol] = mid
    return result


def portfolio_equity(
    *,
    balances: list[Mapping[str, Any]],
    tickers: list[Mapping[str, Any]],
    quote_currency: str,
) -> Decimal:
    """Mark all non-zero balances to one quote currency using current mid prices."""
    quote = quote_currency.upper()
    totals = _balances_total(balances)
    prices = _ticker_map(tickers)
    equity = ZERO

    for currency, amount in totals.items():
        if amount == ZERO:
            continue
        if currency == quote:
            equity += amount
            continue

        direct = f"{currency}/{quote}"
        inverse = f"{quote}/{currency}"
        if direct in prices:
            equity += amount * prices[direct]
        elif inverse in prices:
            equity += amount / prices[inverse]
        else:
            raise RevolutXError(
                f"cannot mark {currency} balance to {quote}; missing ticker"
            )

    if equity <= ZERO:
        raise RevolutXError("portfolio equity is not positive")
    return equity


def drawdown_pct(initial: Decimal, current: Decimal) -> Decimal:
    """Calculate drawdown percentage using Decimal only and the locked formula."""
    initial = _require_decimal(initial, field_name="initial")
    current = _require_decimal(current, field_name="current")
    if initial <= ZERO:
        raise ValueError("initial equity must be positive")
    if current < ZERO:
        raise ValueError("current equity must be non-negative")
    return (initial - current) / initial * ONE_HUNDRED


def _market_snapshot(
    client: RevolutXClient,
    symbol: str,
    max_age_ms: int,
) -> tuple[Decimal, Decimal, Decimal, list[dict[str, Any]]]:
    if max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")

    tickers, exchange_ts = client.get_tickers()
    local_now_ms = time.time_ns() // 1_000_000
    if exchange_ts > local_now_ms + MAX_CLOCK_SKEW_MS:
        raise RevolutXError("exchange timestamp is unexpectedly ahead of local clock")
    if local_now_ms - exchange_ts > max_age_ms:
        raise RevolutXError("market data is stale")

    pair = _pair_key(symbol)
    matches = [
        item
        for item in tickers
        if isinstance(item.get("symbol"), str)
        and _pair_key(str(item["symbol"])) == pair
    ]
    if len(matches) != 1:
        raise RevolutXError(
            f"expected exactly one ticker for {pair}; found {len(matches)}"
        )

    ticker = matches[0]
    for field_name in ("bid", "ask", "mid"):
        if field_name not in ticker:
            raise RevolutXError(f"ticker {pair} missing {field_name}")

    bid = decimal_from_api(ticker["bid"], field_name=f"{pair}.bid")
    ask = decimal_from_api(ticker["ask"], field_name=f"{pair}.ask")
    mid = decimal_from_api(ticker["mid"], field_name=f"{pair}.mid")
    if bid <= ZERO or ask <= ZERO or mid <= ZERO or bid >= ask:
        raise RevolutXError("invalid ticker spread")

    return bid, ask, mid, tickers


def run() -> int:
    """Run the supervisor until stopped or the hard kill-switch fires."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        config = BotConfig.from_env()
    except (KeyError, TypeError, ValueError) as exc:
        LOGGER.error("configuration error: %s", exc)
        return 2

    executor: GridExecutor | None = None
    try:
        client = RevolutXClient(config.api_key, config.private_key_path)
        executor = GridExecutor(
            client,
            db_path=config.db_path,
            dry_run=config.dry_run,
        )
    except (OSError, TypeError, ValueError, sqlite3.Error, RevolutXError) as exc:
        LOGGER.error("startup error: %s", exc)
        if executor is not None:
            executor.close()
        return 1

    stop = False

    def _stop_handler(signum: int, frame: object) -> None:
        del signum, frame
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    try:
        if executor.get_meta("kill_switch_triggered") == "true":
            LOGGER.critical(
                "kill-switch is already persisted; refusing to resume execution"
            )
            try:
                executor.trigger_kill_switch()
            except RevolutXError as exc:
                LOGGER.critical(
                    "kill-switch remains active but cancel-all retry failed: %s",
                    exc,
                )
                return 4
            return 3

        pair_config = client.get_account_pair_configuration()
        pair_key = _pair_key(config.symbol)
        raw_rules = pair_config.get(pair_key)
        if raw_rules is None:
            raise RevolutXError(
                f"account pair configuration not found for {pair_key}"
            )
        rules = PairRules.from_api(pair_key, raw_rules)

        executor.reconcile(config.symbol)
        LOGGER.info(
            "Starting bot symbol=%s dry_run=%s",
            config.symbol,
            config.dry_run,
        )

        while not stop:
            bid, ask, mid, tickers = _market_snapshot(
                client,
                config.symbol,
                config.max_market_age_ms,
            )
            balances = client.get_balances()
            current_equity = portfolio_equity(
                balances=balances,
                tickers=tickers,
                quote_currency=config.quote_currency,
            )
            initial_equity = executor.get_or_set_initial_equity(current_equity)
            dd = drawdown_pct(initial_equity, current_equity)
            LOGGER.info(
                "equity=%s %s drawdown=%s%%",
                current_equity,
                config.quote_currency,
                dd,
            )

            if dd >= KILL_SWITCH_DRAWDOWN_PCT:
                try:
                    executor.trigger_kill_switch()
                except RevolutXError as exc:
                    LOGGER.critical(
                        "kill-switch persisted but cancel-all failed: %s",
                        exc,
                    )
                    return 11
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

        if not config.dry_run:
            LOGGER.warning(
                "Supervisor stopped gracefully; existing live orders are not "
                "cancelled automatically"
            )
        return 0
    except (
        ArithmeticError,
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
        RevolutXError,
    ) as exc:
        LOGGER.error("fail-closed stop: %s", exc)
        return 1
    finally:
        executor.close()


if __name__ == "__main__":
    sys.exit(run())
