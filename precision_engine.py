"""Decimal-only validation and pair-specific precision rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Mapping


class PrecisionError(ValueError):
    """Raised when an order cannot be represented under exchange pair rules."""


def decimal_from_api(value: object, field: str) -> Decimal:
    """Parse an API decimal without ever passing through binary floating point."""
    if isinstance(value, float) or isinstance(value, bool):
        raise PrecisionError(f"{field} must be a decimal string or integer")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PrecisionError(f"invalid {field}") from exc
    if not parsed.is_finite():
        raise PrecisionError(f"{field} must be finite")
    return parsed


def decimal_text(value: Decimal) -> str:
    """Serialize a Decimal in fixed-point notation for JSON and SQLite."""
    return format(value, "f")


@dataclass(frozen=True, slots=True)
class PairRules:
    """Trading constraints supplied by the Revolut X pair configuration."""

    symbol: str
    base_step: Decimal
    quote_step: Decimal
    min_base_size: Decimal
    max_base_size: Decimal
    status: str

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]) -> "PairRules":
        """Build validated rules from a configuration response item."""
        required = ("symbol", "base_step", "quote_step", "min_base_size", "max_base_size", "status")
        if any(key not in payload for key in required):
            raise PrecisionError("pair configuration is missing required fields")
        rules = cls(
            symbol=SymbolNormalizer.to_display_symbol(str(payload["symbol"])),
            base_step=decimal_from_api(payload["base_step"], "base_step"),
            quote_step=decimal_from_api(payload["quote_step"], "quote_step"),
            min_base_size=decimal_from_api(payload["min_base_size"], "min_base_size"),
            max_base_size=decimal_from_api(payload["max_base_size"], "max_base_size"),
            status=str(payload["status"]).lower(),
        )
        if min(rules.base_step, rules.quote_step, rules.min_base_size) <= 0:
            raise PrecisionError("pair steps and minimum size must be positive")
        if rules.max_base_size < rules.min_base_size:
            raise PrecisionError("maximum size is below minimum size")
        return rules

    def normalize(self, price: Decimal, quantity: Decimal) -> tuple[Decimal, Decimal]:
        """Round price and quantity down and enforce pair availability and bounds."""
        if self.status not in {"active", "online", "enabled", "trading"}:
            raise PrecisionError(f"pair {self.symbol} is not tradable")
        if price <= 0 or quantity <= 0:
            raise PrecisionError("price and quantity must be positive")
        rounded_price = (
            (price / self.quote_step).to_integral_value(rounding=ROUND_DOWN) * self.quote_step
        ).quantize(self.quote_step)
        rounded_quantity = (
            (quantity / self.base_step).to_integral_value(rounding=ROUND_DOWN) * self.base_step
        ).quantize(self.base_step)
        if rounded_price <= 0:
            raise PrecisionError("price rounds to zero")
        if not self.min_base_size <= rounded_quantity <= self.max_base_size:
            raise PrecisionError("quantity is outside pair limits")
        return rounded_price, rounded_quantity


class SymbolNormalizer:
    """Normalize exchange symbols at API and persistence boundaries."""

    @staticmethod
    def to_api_symbol(symbol: str) -> str:
        """Return ``BTC-USD`` form for mutations and URL paths."""
        return symbol.replace("/", "-").replace("_", "-").upper()

    @staticmethod
    def to_display_symbol(symbol: str) -> str:
        """Return ``BTC/USD`` form for ticker data and SQLite keys."""
        return symbol.replace("-", "/").replace("_", "/").upper()
