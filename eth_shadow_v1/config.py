"""Immutable v1 config; changing parameters creates a distinct SHA256 experiment."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
from pathlib import Path
import tomllib

D = Decimal
DECIMAL_KEYS = (
    "initial_cash_eur", "initial_eth", "grid_step", "lot_budget_eur",
    "max_cost_basis_eur", "fee_rate", "slippage_rate", "daily_loss_eur",
    "soft_drawdown_eur", "hard_drawdown_eur", "absolute_equity_floor_eur",
    "soft_recovery_drawdown_eur", "max_spread_rate", "min_depth_eur_per_side",
    "depth_band_rate", "min_atr_rate", "max_atr_rate", "max_adx",
    "max_ema_divergence", "max_24h_change", "reanchor_move",
)
INTEGER_KEYS = ("grid_levels", "soft_recovery_seconds", "poll_seconds",
                "max_book_age_seconds", "reanchor_seconds")
STRING_KEYS = ("version", "mode", "symbol", "region")
KEYS = set(DECIMAL_KEYS + INTEGER_KEYS + STRING_KEYS)


def number(value: object, name: str) -> Decimal:
    """Forbid binary floats at all financial boundaries."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a decimal string")
    try:
        result = D(value)
    except Exception as exc:
        raise ValueError(f"invalid {name}") from exc
    if not result.is_finite():
        raise ValueError(f"nonfinite {name}")
    return result


@dataclass(frozen=True)
class Config:
    """Complete, hashed, single-purpose shadow experiment configuration."""
    values: dict[str, object]
    sha256: str

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        raw = Path(path).read_bytes()
        parsed = tomllib.loads(raw.decode("utf-8"))
        if set(parsed) != KEYS:
            raise ValueError(f"unexpected/missing config keys: {sorted(set(parsed) ^ KEYS)}")
        for key in STRING_KEYS:
            if type(parsed[key]) is not str:
                raise ValueError(f"{key} must be text")
        if (parsed["version"], parsed["mode"], parsed["symbol"], parsed["region"]) != (
                "1.0", "shadow", "ETH-EUR", "EEA"):
            raise ValueError("v1 requires shadow-only ETH-EUR/EEA")
        for key in DECIMAL_KEYS:
            parsed[key] = number(parsed[key], key)
        for key in INTEGER_KEYS:
            if type(parsed[key]) is not int or parsed[key] <= 0:
                raise ValueError(f"invalid integer {key}")
        # Fail closed on safety-critical config drift; new experiments require v2.
        locked = {
            "initial_cash_eur": "1000.00", "initial_eth": "0",
            "grid_step": "0.0075", "lot_budget_eur": "100.00",
            "max_cost_basis_eur": "400.00", "hard_drawdown_eur": "30.00",
            "absolute_equity_floor_eur": "970.00", "fee_rate": "0.0009",
            "slippage_rate": "0.0005",
        }
        if any(parsed[key] != D(value) for key, value in locked.items()):
            raise ValueError("v1 capital, fees or hard risk parameters changed")
        if parsed["grid_levels"] != 4 or parsed["poll_seconds"] != 5:
            raise ValueError("v1 grid and poll cadence changed")
        for key in DECIMAL_KEYS:
            if parsed[key] < 0:
                raise ValueError(f"negative {key}")
        if parsed["max_cost_basis_eur"] > parsed["initial_cash_eur"]:
            raise ValueError("allocation greater than cash")
        return cls(parsed, hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest())

    def __getitem__(self, key: str) -> object:
        return self.values[key]
