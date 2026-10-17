"""Isolated, read-only market-data shadow runner. No trading client is imported.

Input: JSONL snapshots {"timestamp":"...Z","bids":[["100","1"]],"asks":[["101","1"]]}.
Offline by default; optional live mode fetches only a hardcoded public GET.
Outputs deterministic JSONL decisions and a JSON report. No exchange mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

from precision_engine import decimal_from_api as dec, decimal_text as fmt

PUBLIC_URL = "https://revx.revolut.com/api/2.0/public/order-book/BTC-USD"


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("source timestamp must be a string")
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
    # Drawdown is measured against the immutable starting equity, not a trailing peak.

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
        self.pending_buy: Decimal | None = None

    def evaluate(self, payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        timestamp = None
        now = now or datetime.now(timezone.utc)
        reasons = []
        action = "HOLD"
        price = None
        bid = ask = None
        try:
            if now.tzinfo is None:
                raise ValueError("now must be timezone-aware")
            timestamp = parse_timestamp(payload["timestamp"])
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
            elif self.btc == 0 and self.pending_buy is None:
                budget = min(self.cfg.initial_usd * self.cfg.max_order,
                             self.cfg.initial_usd * self.cfg.max_allocation - self.btc * ask,
                             self.cash)
                # A maker buy rests at the best bid. Touching the bid is not a fill.
                # Conservative replay: only a later ask strictly below the resting
                # limit can fill; queue position and partial fills remain unproven.
                self.pending_buy = bid
                action = "PAPER_POST_ONLY_BUY_PLACED"
                reasons.append("RESTING_MAKER_ORDER_UNFILLED")
            elif self.pending_buy is not None and ask < self.pending_buy:
                limit = self.pending_buy
                budget = min(self.cfg.initial_usd * self.cfg.max_order, self.cash)
                quantity = min(budget / (limit * (1 + self.cfg.fee_rate)), ask_size)
                if quantity > 0:
                    gross = quantity * limit
                    fee = gross * self.cfg.fee_rate
                    self.cash -= gross + fee
                    self.btc += quantity
                    self.costs += fee
                    self.trades += 1
                    action = "PAPER_MAKER_FILL_ASSUMED"
                    reasons.append("CONSERVATIVE_CROSSING_HEURISTIC_NOT_QUEUE_PROOF")
                self.pending_buy = None
                # Post-fill equity includes fee and the newly acquired position.
                post_fill_nav = self.cash + self.btc * bid
                if post_fill_nav <= self.cfg.initial_usd * (1 - self.cfg.max_drawdown):
                    self.halted = True
                    reasons.append("DRAWDOWN_LIMIT_POST_FILL")
            else:
                reasons.append("POSITION_HELD")
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            self.halted = True
            self.violations += 1
            reasons.append("DATA_ERROR:" + type(exc).__name__)
        nav = self.cash + self.btc * (self.last_mark if self.last_mark is not None else Decimal("0"))
        decision = {
            "timestamp": payload.get("timestamp") if isinstance(payload, dict) else None,
            "action": action,
            "reasons": reasons,
            "bid": fmt(bid) if price is not None else None,
            "ask": fmt(ask) if price is not None else None,
            "cash_usd": fmt(self.cash),
            "btc": fmt(self.btc),
            "nav_usd": fmt(nav),
            "simulated_fees_usd": fmt(self.costs),
            "halted": self.halted,
            "pending_buy": fmt(self.pending_buy) if self.pending_buy is not None else None,
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
            "simulated_fees_usd": fmt(self.costs),
            "slippage_model": "NOT_MODELED",
            "paper_fills": self.trades,
            "drawdown_definition": "initial_equity_fixed_3pct",
            "pending_maker_buy": fmt(self.pending_buy) if self.pending_buy is not None else None,
            "data_risk_violations": self.violations,
            "halted": self.halted,
            "last_decision_hash": self.previous_hash,
            "recovery_test_status": "NOT_RUN_BY_RUNNER",
        }

    def state(self) -> dict[str, Any]:
        return {
            "cash": fmt(self.cash), "btc": fmt(self.btc),
            "costs": fmt(self.costs), "trades": self.trades,
            "violations": self.violations, "halted": self.halted,
            "previous_hash": self.previous_hash,
            "last_time": self.last_time.isoformat() if self.last_time else None,
            "last_mark": fmt(self.last_mark) if self.last_mark is not None else None,
            "pending_buy": fmt(self.pending_buy) if self.pending_buy is not None else None,
        }

    def restore(self, state: dict[str, Any]) -> None:
        for key in ("cash", "btc", "costs"):
            setattr(self, key, dec(state[key], key))
        for key in ("trades", "violations"):
            value = state[key]
            if type(value) is not int or value < 0:
                raise ValueError("invalid persisted counter")
            setattr(self, key, value)
        if type(state["halted"]) is not bool:
            raise ValueError("invalid persisted halt")
        self.halted = state["halted"]
        digest = state["previous_hash"]
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("invalid audit hash")
        self.previous_hash = digest
        self.last_time = parse_timestamp(state["last_time"]) if state["last_time"] else None
        self.last_mark = dec(state["last_mark"], "last_mark") if state["last_mark"] is not None else None
        self.pending_buy = dec(state["pending_buy"], "pending_buy") if state["pending_buy"] is not None else None


def save_state(path: Path, runner: ShadowRunner) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(runner.state(), stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def public_snapshot() -> dict[str, Any]:
    request = Request(PUBLIC_URL, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=8) as response:
        if response.status != 200:
            raise RuntimeError("public market GET returned non-200")
        raw = response.read(1024 * 1024)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("unexpected public response")
    # Reception time is not exchange event time; without a trusted source timestamp
    # the observation cannot establish market-data freshness.
    source = data.get("data", data)
    if not isinstance(source, dict) or "timestamp" not in source:
        raise ValueError("public order book lacks verified source timestamp")
    return {"timestamp": source["timestamp"], "data": source}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="offline JSONL snapshots")
    parser.add_argument("--public-once", action="store_true", help="one hardcoded unauthenticated GET")
    parser.add_argument("--poll-seconds", type=float, default=0, help="public GET polling interval; 0 means once")
    parser.add_argument("--samples", type=int, default=1, help="maximum public GET observations")
    parser.add_argument("--state", type=Path, help="durable simulation state")
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if (args.input is None) == (not args.public_once):
        parser.error("choose exactly one of --input or --public-once")
    if args.samples < 1 or args.poll_seconds < 0 or (args.samples > 1 and args.poll_seconds < 5):
        parser.error("polling requires >=5 seconds between samples")
    runner = ShadowRunner()
    if args.state and args.state.exists():
        runner.restore(json.loads(args.state.read_text(encoding="utf-8")))
    if args.input is not None:
        snapshots = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
        # Offline replay timestamps are judged at their own capture time;
        # freshness in replay cannot prove freshness of a live feed.
        stream = ((item, parse_timestamp(item["timestamp"])) for item in snapshots)
    else:
        def public_stream():
            for index in range(args.samples):
                if index:
                    time.sleep(args.poll_seconds)
                item = public_snapshot()
                yield item, datetime.now(timezone.utc)
        stream = public_stream()
    args.decisions.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.decisions.open("a", encoding="utf-8") as out:
        for item, now in stream:
            out.write(json.dumps(runner.evaluate(item, now), sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())
            if args.state:
                save_state(args.state, runner)
    args.report.write_text(json.dumps(runner.report(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
