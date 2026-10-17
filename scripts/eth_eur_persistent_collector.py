"""CLI for persistent public ETH/EUR snapshots and independently read-only health.

The daemon must be deployed on a user-controlled persistent machine. Running
tests or CI does NOT activate a background service.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys

from eth_shadow_v1.collector import ContinuousReadOnlyCollector
from eth_shadow_v1.collector_quality import evaluate_quality
from eth_shadow_v1.config import Config

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "eth_eur_grid_shadow_v1.toml"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persistent Revolut X ETH/EUR PUBLIC GETs ONLY")
    parser.add_argument("command", choices=["run", "health"])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--full-audit", action="store_true", help="verify all historical hashes, not just last 5000")
    parser.add_argument("--max-polls", type=int, default=0,
                        help="Nonzero for bounded smoke only; 0 means run until stopped")
    args = parser.parse_args(argv)
    if args.command == "health":
        from time import time_ns
        report = evaluate_quality(args.db, time_ns() // 1_000_000, full_audit=args.full_audit)
        if args.report:
            atomic_json(args.report, report)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "DATA_READY_FOR_REVIEW" else 2
    if args.max_polls < 0:
        parser.error("--max-polls cannot be negative")
    if os.getenv("LIVE_TRADING", "false").lower() == "true":
        parser.error("REFUSE_COLLECTOR_WHEN_LIVE_TRADING_ENV_ENABLED")
    cfg = Config.load(CONFIG)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with ContinuousReadOnlyCollector(cfg, args.db) as collector:
        count = collector.run(max_polls=args.max_polls)
    from time import time_ns
    report = evaluate_quality(args.db, time_ns() // 1_000_000)
    report["bounded_polls_completed"] = count
    if args.report:
        atomic_json(args.report, report)
    print(json.dumps(report, sort_keys=True))
    # Warmup is normal: a healthy short smoke must not claim 24h acceptance.
    return 0 if count == args.max_polls and args.max_polls > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
