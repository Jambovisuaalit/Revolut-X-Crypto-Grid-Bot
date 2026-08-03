"""Decimal-only precision, pair rules, and pre-flight risk checks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Mapping

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
MAX_SINGLE_ORDER_PCT = Decimal("5")


class PrecisionError(ValueError):
    """Raised when an order fails precision or exchange-rule validation."""


def _require_decimal(value: object, *, field_name: str) -> Decimal:
    """Require an in-memory financial value to already be Decimal."""
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal; got {type(value).__name__}")
    if not value.is_finite():
        raise PrecisionError(f"{field_name} must be finite")
    return value


def decimal_from_api(value: object, *, field_name: str) -> Decimal:
    """Parse a documented decimal string into Decimal, rejecting float input."""
    if isinstance(value, float):
        raise PrecisionError(f"{field_name} must not be float")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise PrecisionError(f"{field_name} is not a valid decimal string") from exc
    else:
        raise PrecisionError(f"{field_name} must be a decimal string")
    if not parsed.is_finite():
        raise PrecisionError(f"{field_name} must be finite")
    return parsed


@dataclass(frozen=True, slots=True)
class PairRules:
    """Revolut X pair-specific order constraints, stored only as Decimal."""

    symbol: str
    base: str
    quote: str
    base_step: Decimal
    quote_step: Decimal
    min_order_size: Decimal
    max_order_size: Decimal
    min_order_size_quote: Decimal
    status: str

    def __post_init__(self) -> None:
        """Fail closed if any financial rule is non-Decimal or invalid."""
        for field_name in (
            "base_step",
            "quote_step",
            "min_order_size",
            "max_order_size",
            "min_order_size_quote",
        ):
            value = _require_decimal(getattr(self, field_name), field_name=field_name)
            if value <= ZERO:
                raise PrecisionError(f"{field_name} must be positive")
        if self.min_order_size > self.max_order_size:
            raise PrecisionError("min_order_size must not exceed max_order_size")
        if not self.symbol or not self.base or not self.quote:
            raise PrecisionError("pair symbol/base/quote must not be empty")

    @classmethod
    def from_api(cls, symbol: str, payload: Mapping[str, Any]) -> "PairRules":
        """Build strict pair rules from Revolut X decimal-string configuration."""
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
            base_step=decimal_from_api(payload["base_step"], field_name="base_step"),
            quote_step=decimal_from_api(payload["quote_step"], field_name="quote_step"),
            min_order_size=decimal_from_api(payload["min_order_size"], field_name="min_order_size"),
            max_order_size=decimal_from_api(payload["max_order_size"], field_name="max_order_size"),
            min_order_size_quote=decimal_from_api(
                payload["min_order_size_quote"], field_name="min_order_size_quote"
            ),
            status=str(payload["status"]).lower(),
        )


def round_down(value: Decimal, step: Decimal) -> Decimal:
    """Round a Decimal down to the nearest positive exchange step."""
    value = _require_decimal(value, field_name="value")
    step = _require_decimal(step, field_name="step")
    if step <= ZERO:
        raise PrecisionError("step must be positive")
    if value < ZERO:
        raise PrecisionError("value must be non-negative")
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def decimal_string(value: Decimal) -> str:
    """Serialize an in-memory Decimal in non-scientific notation."""
    value = _require_decimal(value, field_name="value")
    return format(value, "f")


def validate_post_only(side: str, price: Decimal, best_bid: Decimal, best_ask: Decimal) -> None:
    """Reject prices that would immediately cross the current spread."""
    price = _require_decimal(price, field_name="price")
    best_bid = _require_decimal(best_bid, field_name="best_bid")
    best_ask = _require_decimal(best_ask, field_name="best_ask")
    normalized = side.lower()
    if normalized not in {"buy", "sell"}:
        raise PrecisionError("side must be buy or sell")
    if price <= ZERO:
        raise PrecisionError("price must be positive")
    if best_bid <= ZERO or best_ask <= ZERO or best_bid >= best_ask:
        raise PrecisionError("invalid order book spread")
    if normalized == "buy" and price >= best_ask:
        raise PrecisionError("post-only buy would cross the ask")
    if normalized == "sell" and price <= best_bid:
        raise PrecisionError("post-only sell would cross the bid")


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
    price = _require_decimal(price, field_name="price")
    base_size = _require_decimal(base_size, field_name="base_size")
    available_base = _require_decimal(available_base, field_name="available_base")
    available_quote = _require_decimal(available_quote, field_name="available_quote")
    allocation_pct = _require_decimal(allocation_pct, field_name="allocation_pct")

    if rules.status != "active":
        raise PrecisionError(f"pair {rules.symbol} is not active")
    if available_base < ZERO or available_quote < ZERO:
        raise PrecisionError("available balances must be non-negative")
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
    fraction = allocation_pct / ONE_HUNDRED
    if normalized == "buy":
        if notional > available_quote * fraction:
            raise PrecisionError("buy order exceeds configured free-balance allocation")
    elif normalized == "sell":
        if rounded_size > available_base * fraction:
            raise PrecisionError("sell order exceeds configured free-balance allocation")
    else:
        raise PrecisionError("side must be buy or sell")

    return rounded_price, rounded_size
