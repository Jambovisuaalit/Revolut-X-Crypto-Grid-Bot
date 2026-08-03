"""Decimal-only precision, pair rules, and pre-flight risk checks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Mapping, Any

ZERO = Decimal("0")
MAX_SINGLE_ORDER_PCT = Decimal("5")


class PrecisionError(ValueError):
    """Raised when an order fails precision or exchange-rule validation."""


@dataclass(frozen=True, slots=True)
class PairRules:
    """Revolut X pair-specific order constraints."""

    symbol: str
    base: str
    quote: str
    base_step: Decimal
    quote_step: Decimal
    min_order_size: Decimal
    max_order_size: Decimal
    min_order_size_quote: Decimal
    status: str

    @classmethod
    def from_api(cls, symbol: str, payload: Mapping[str, Any]) -> "PairRules":
        """Build strict pair rules from Revolut X configuration data."""
        required = (
            "base",
            "quote",
            "base_step",
            "quote_step",
            "min_order_size",
            "max_order_size",
            "min_order_size_quote",
            "status",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise PrecisionError(f"pair rules missing fields: {', '.join(missing)}")
        return cls(
            symbol=symbol,
            base=str(payload["base"]),
            quote=str(payload["quote"]),
            base_step=Decimal(str(payload["base_step"])),
            quote_step=Decimal(str(payload["quote_step"])),
            min_order_size=Decimal(str(payload["min_order_size"])),
            max_order_size=Decimal(str(payload["max_order_size"])),
            min_order_size_quote=Decimal(str(payload["min_order_size_quote"])),
            status=str(payload["status"]).lower(),
        )


def round_down(value: Decimal, step: Decimal) -> Decimal:
    """Round a Decimal down to the nearest positive exchange step."""
    if step <= ZERO:
        raise PrecisionError("step must be positive")
    if value < ZERO:
        raise PrecisionError("value must be non-negative")
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def decimal_string(value: Decimal) -> str:
    """Serialize Decimal in non-scientific notation."""
    return format(value, "f")


def validate_post_only(side: str, price: Decimal, best_bid: Decimal, best_ask: Decimal) -> None:
    """Reject prices that would immediately cross the current spread."""
    normalized = side.lower()
    if best_bid <= ZERO or best_ask <= ZERO or best_bid >= best_ask:
        raise PrecisionError("invalid order book spread")
    if normalized == "buy" and price >= best_ask:
        raise PrecisionError("post-only buy would cross the ask")
    if normalized == "sell" and price <= best_bid:
        raise PrecisionError("post-only sell would cross the bid")
    if normalized not in {"buy", "sell"}:
        raise PrecisionError("side must be buy or sell")


def validate_order(
    *,
    rules: PairRules,
    side: str,
    price: Decimal,
    base_size: Decimal,
    available_base: Decimal,
    available_quote: Decimal,
    allocation_pct: Decimal,
) -> tuple[Decimal, Decimal]:
    """Round and validate an order against pair rules and the 5% capital guard."""
    if rules.status != "active":
        raise PrecisionError(f"pair {rules.symbol} is not active")
    if allocation_pct <= ZERO or allocation_pct > MAX_SINGLE_ORDER_PCT:
        raise PrecisionError("allocation_pct must be > 0 and <= 5")

    rounded_price = round_down(price, rules.quote_step)
    rounded_size = round_down(base_size, rules.base_step)
    if rounded_price <= ZERO or rounded_size <= ZERO:
        raise PrecisionError("rounded price and size must be positive")
    if rounded_size < rules.min_order_size:
        raise PrecisionError("base size below pair minimum")
    if rounded_size > rules.max_order_size:
        raise PrecisionError("base size above pair maximum")

    notional = rounded_price * rounded_size
    if notional < rules.min_order_size_quote:
        raise PrecisionError("quote notional below pair minimum")

    normalized = side.lower()
    fraction = allocation_pct / Decimal("100")
    if normalized == "buy":
        if notional > available_quote * fraction:
            raise PrecisionError("buy order exceeds configured free-balance allocation")
    elif normalized == "sell":
        if rounded_size > available_base * fraction:
            raise PrecisionError("sell order exceeds configured free-balance allocation")
    else:
        raise PrecisionError("side must be buy or sell")

    return rounded_price, rounded_size
