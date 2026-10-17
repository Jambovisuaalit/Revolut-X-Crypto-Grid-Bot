"""Run deterministic offline ETH/EUR shadow replay. NEVER connects to a network."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

from eth_shadow_v1.config import Config
from eth_shadow_v1.grid import PairRules
from eth_shadow_v1.journal import Journal, canonical

CONFIG = Path(__file__).resolve().parent.parent / "configs" / "eth_eur_grid_shadow_v1.toml"


def sample_pair() -> dict:
    """Demonstration constraints, expressly NOT verified Revolut X current pair rules."""
    return {
        "status": "active", "region": "EEA", "symbol": "ETH-EUR",
        "base_step": "0.00000001", "quote_step": "0.01",
        "min_notional": "1.00", "max_notional": "10000.00",
        "source": "synthetic_fixture",
    }


def synthetic_candles(instant_ms: int) -> list[dict]:
    """200 artificial flat-market bars: for software tests, not profitability inference."""
    last_closed = (instant_ms // 900000) * 900000
    return [
        {"close_ts_ms": last_closed - (199-i)*900000,
         "high": "3003", "low": "2997", "close": "3000",
         "volume": "100", "closed": True}
        for i in range(200)
    ]


def sample_event(index: int, timestamp_ms: int, bid: str, ask: str,
                 candles: list[dict]) -> dict:
    """One reproducible fictitious ETH/EUR book observation."""
    return {
        "event_id": f"fixture-{index:03d}", "observed_ms": timestamp_ms + 100,
        "metadata": {"timestamp": timestamp_ms, "region": "EEA"},
        "symbol": "ETH-EUR", "bids": [[bid, "1.0"]],
        "asks": [[ask, "1.0"]], "candles": candles,
    }


def fixture() -> dict:
    """One closed cycle over 25 simulated seconds, never a 30-day trial."""
    start_ms = int(datetime(2026, 10, 17, 0, 5, tzinfo=timezone.utc).timestamp()*1000)
    candles = synthetic_candles(start_ms)
    quotes = [
        ("2999.75", "3000.25"),  # anchor 3000
        ("2975.50", "2976.00"),  # trigger one
        ("2975.50", "2976.00"),  # synthetic buy
        ("3002.00", "3002.50"),  # sale trigger one
        ("3002.00", "3002.50"),  # synthetic sale
    ]
    return {"pair_rules": sample_pair(),
            "events": [sample_event(i, start_ms + i*5000, b, a, candles)
                       for i, (b, a) in enumerate(quotes)]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline-only ETH/EUR shadow; no public/auth HTTP")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--fixture-demo", action="store_true")
    selection.add_argument("--input", type=Path, help="offline JSON file with synthetic pair_rules and events")
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run-id", default="eth-eur-shadow-v1-offline")
    args = parser.parse_args()
    payload = fixture() if args.fixture_demo else json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("offline input requires events list")
    cfg = Config.load(CONFIG)
    pair_rules = PairRules.from_dict(payload["pair_rules"])
    with Journal(args.journal, args.run_id, cfg, pair_rules) as journal:
        for event in payload["events"]:
            journal.process(event)
        report = journal.report()
        decisions = journal.decisions()
    for path in (args.report, args.decisions):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.decisions.write_text("".join(canonical(d)+"\n" for d in decisions), encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
