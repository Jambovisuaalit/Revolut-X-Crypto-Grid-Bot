"""Pure Decimal grid and synthetic quantity/price mathematics."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from .config import Config, number

D = Decimal


def quant(value: Decimal, step: Decimal, upward: bool = False) -> Decimal:
    """Round to arbitrary exchange step, not just decimal places."""
    if step <= 0 or not step.is_finite() or not value.is_finite():
        raise ValueError("invalid price or step")
    return (value / step).to_integral_value(
        rounding=ROUND_UP if upward else ROUND_DOWN) * step


@dataclass(frozen=True)
class PairRules:
    """Offline pair constraints; production pair metadata is NOT inferred."""
    status: str
    region: str
    symbol: str
    base_step: Decimal
    quote_step: Decimal
    min_notional: Decimal
    max_notional: Decimal
    source: str

    @classmethod
    def from_dict(cls, raw: dict) -> "PairRules":
        expected = {"status", "region", "symbol", "base_step", "quote_step",
                    "min_notional", "max_notional", "source"}
        if set(raw) != expected:
            raise ValueError("pair metadata incomplete")
        values = {key: number(raw[key], key) for key in
                  ("base_step", "quote_step", "min_notional", "max_notional")}
        result = cls(raw["status"], raw["region"], raw["symbol"], source=raw["source"], **values)
        if any(x <= 0 for x in (result.base_step, result.quote_step, result.min_notional, result.max_notional)):
            raise ValueError("pair metadata nonpositive")
        if result.max_notional < result.min_notional:
            raise ValueError("pair notional range invalid")
        if result.source != "synthetic_fixture":
            # An authenticated/live adapter is deliberately not provided by v1.
            raise ValueError("v1 offline runner accepts synthetic pair metadata only")
        return result

    def active(self) -> bool:
        return self.status == "active" and self.region == "EEA" and self.symbol == "ETH-EUR"


def levels(anchor: Decimal, cfg: Config, rules: PairRules) -> tuple[tuple[Decimal, Decimal], ...]:
    """Return ((buy, sell), ...) using the locked anchor and real step argument."""
    if anchor <= 0:
        raise ValueError("anchor must be positive")
    step = cfg["grid_step"]
    return tuple((
        quant(anchor * (1 - step * i), rules.quote_step),
        quant(anchor * (1 - step * (i - 1)), rules.quote_step, upward=True),
    ) for i in range(1, cfg["grid_levels"] + 1))


def buy_math(buy_limit: Decimal, cfg: Config, rules: PairRules) -> tuple[Decimal, Decimal, Decimal]:
    """Return (qty, total debited, fee) for a synthetic 100 EUR buy."""
    executed_price = buy_limit * (1 + cfg["slippage_rate"])
    qty = quant(cfg["lot_budget_eur"] / (executed_price * (1 + cfg["fee_rate"])), rules.base_step)
    gross = qty * executed_price
    fee = gross * cfg["fee_rate"]
    return qty, gross + fee, fee


def sell_math(qty: Decimal, target: Decimal, cfg: Config) -> tuple[Decimal, Decimal, Decimal]:
    """Return (execution price, credited proceeds, fee) for a local sale."""
    price = target * (1 - cfg["slippage_rate"])
    gross = qty * price
    fee = gross * cfg["fee_rate"]
    return price, gross - fee, fee
