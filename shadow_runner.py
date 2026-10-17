"""Isolated, read-only market-data shadow runner. No trading client is imported.

Input: JSONL snapshots {"timestamp":"...Z","bids":[["100","1"]],"asks":[["101","1"]]}.
Offline by default; optional live mode fetches only a hardcoded public GET.
Outputs deterministic JSONL decisions and a JSON report. No exchange mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

from precision_engine import decimal_from_api as dec, decimal_text as fmt

PUBLIC_URL = "https://revx.revolut.com/api/2.0/public/order-book/BTC-USD"


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("snapshot timestamp requires timezone")
    return parsed.astimezone(timezone.utc)


def parse_book(payload: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("invalid book envelope")
    def level(side: str) -> tuple[Decimal, Decimal]:
        rows = data.get(side)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"missing {side}")
        item = rows[0]
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            p, q = item[:2]
        elif isinstance(item, dict):
            p = item.get("price")
            q = item.get("quantity", item.get("size"))
        else:
            raise ValueError("invalid level")
        price, size = dec(p, side + ".price"), dec(q, side + ".size")
        if price <= 0 or size <= 0:
            raise ValueError("nonpositive book level")
        return price, size
    bid, bid_qty = level("bids")
    ask, ask_qty = level("asks")
    if bid >= ask:
        raise ValueError("crossed or locked book")
    return bid, bid_qty, ask, ask_qty


@dataclass
class ShadowConfig:
    initial_usd: Decimal = Decimal("1000")
    max_allocation: Decimal = Decimal("0.10")
    max_order: Decimal = Decimal("0.05")
    max_drawdown: Decimal = Decimal("0.03")
    fee_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.0005")
    max_spread: Decimal = Decimal("0.01")
    max_age_seconds: int = 30

    def validate(self) -> None:
        if self.initial_usd <= 0 or not self.initial_usd.is_finite():
            raise ValueError("initial_usd must be positive")
        for key in ("max_allocation", "max_order", "max_drawdown", "fee_rate", "slippage_rate", "max_spread"):
            value = getattr(self, key)
            if not value.is_finite() or value < 0 or value > 1:
                raise ValueError(f"invalid {key}")
        if self.max_order > self.max_allocation or self.max_age_seconds <= 0:
            raise ValueError("invalid limits")


class ShadowRunner:
    def __init__(self, config: ShadowConfig | None = None):
        self.cfg = config or ShadowConfig()
        self.cfg.validate()
        self.cash = self.cfg.initial_usd
        self.btc = Decimal("0")
        self.costs = Decimal("0")
        self.trades = 0
        self.violations = 0
        self.halted = False
        self.previous_hash = "0" * 64
        self.last_time: datetime | None = None
        self.last_mark: Decimal | None = None
        self.peak = self.cfg.initial_usd

    def evaluate(self, payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        timestamp = parse_timestamp(payload["timestamp"])
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now must have timezone")
        reasons = []
        action = "HOLD"
        price = None
        try:
            bid, bid_size, ask, ask_size = parse_book(payload)
            price = (bid + ask) / 2
            if self.last_time is not None and timestamp <= self.last_time:
                raise ValueError("nonmonotonic snapshot timestamp")
            if timestamp > now or (now - timestamp).total_seconds() > self.cfg.max_age_seconds:
                raise ValueError("stale or future market data")
            self.last_time = timestamp
            self.last_mark = price
            nav = self.cash + self.btc * bid
            self.peak = max(self.peak, nav)
            if nav <= self.cfg.initial_usd * (1 - self.cfg.max_drawdown):
                self.halted = True
                reasons.append("DRAWDOWN_LIMIT")
            if self.halted:
                reasons.append("HALTED")
            elif (ask - bid) / price > self.cfg.max_spread:
                reasons.append("SPREAD_LIMIT")
            elif self.btc == 0:
                budget = min(self.cfg.initial_usd * self.cfg.max_order,
                             self.cfg.initial_usd * self.cfg.max_allocation - self.btc * ask,
                             self.cash)
                execution_price = ask * (1 + self.cfg.slippage_rate)
                quantity = min(budget / (execution_price * (1 + self.cfg.fee_rate)), ask_size)
                if quantity > 0:
                    gross = quantity * execution_price
                    fee = gross * self.cfg.fee_rate
                    self.cash -= gross + fee
                    self.btc += quantity
                    self.costs += fee + quantity * ask * self.cfg.slippage_rate
                    self.trades += 1
                    action = "PAPER_BUY"
                    reasons.append("SIMULATED_TAKER_FILL_NOT_VENUE_PROOF")
                else:
                    reasons.append("NO_CAPACITY")
            else:
                reasons.append("POSITION_HELD")
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            self.halted = True
            self.violations += 1
            reasons.append("DATA_ERROR:" + type(exc).__name__)
        nav = self.cash + self.btc * (self.last_mark if self.last_mark is not None else Decimal("0"))
        decision = {
            "timestamp": payload.get("timestamp"),
            "action": action,
            "reasons": reasons,
            "bid": fmt(bid) if price is not None else None,
            "ask": fmt(ask) if price is not None else None,
            "cash_usd": fmt(self.cash),
            "btc": fmt(self.btc),
            "nav_usd": fmt(nav),
            "fees_plus_slippage_usd": fmt(self.costs),
            "halted": self.halted,
            "previous_hash": self.previous_hash,
        }
        digest = hashlib.sha256(json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        decision["hash"] = digest
        self.previous_hash = digest
        return decision

    def report(self) -> dict[str, Any]:
        nav = self.cash + self.btc * (self.last_mark or Decimal("0"))
        return {
            "mode": "SHADOW_ONLY",
            "authenticated_exchange_test": False,
            "initial_usd": fmt(self.cfg.initial_usd),
            "final_nav_usd": fmt(nav),
            "simulated_pnl_usd": fmt(nav - self.cfg.initial_usd),
            "simulated_costs_usd": fmt(self.costs),
            "paper_fills": self.trades,
            "data_risk_violations": self.violations,
            "halted": self.halted,
            "last_decision_hash": self.previous_hash,
            "recovery_test_status": "NOT_RUN_BY_RUNNER",
        }


def public_snapshot() -> dict[str, Any]:
    request = Request(PUBLIC_URL, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=8) as response:
        if response.status != 200:
            raise RuntimeError("public market GET returned non-200")
        raw = response.read(1024 * 1024)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("unexpected public response")
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "data": data.get("data", data)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="offline JSONL snapshots")
    parser.add_argument("--public-once", action="store_true", help="one hardcoded unauthenticated GET")
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if (args.input is None) == (not args.public_once):
        parser.error("choose exactly one of --input or --public-once")
    runner = ShadowRunner()
    if args.input is not None:
        snapshots = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
        # Offline replay timestamps are judged at their own capture time;
        # freshness in replay cannot prove freshness of a live feed.
        stream = ((item, parse_timestamp(item["timestamp"])) for item in snapshots)
    else:
        item = public_snapshot()
        stream = [(item, datetime.now(timezone.utc))]
    args.decisions.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.decisions.open("w", encoding="utf-8") as out:
        for item, now in stream:
            out.write(json.dumps(runner.evaluate(item, now), sort_keys=True) + "\n")
    args.report.write_text(json.dumps(runner.report(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
