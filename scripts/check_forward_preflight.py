"""Evaluate an existing public collector database without network access.

Read-only reporting only. Never starts the collector, shadow engine, or trading.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from eth_shadow_v1.forward_preflight import inspect_forward_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline 24h ETH/EUR forward data gate")
    parser.add_argument("--collector-db", required=True, type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--now-ms", type=int, default=None,
                        help="Fixed audit clock for deterministic offline tests only")
    args = parser.parse_args()
    now_ms = args.now_ms if args.now_ms is not None else int(
        datetime.now(timezone.utc).timestamp() * 1000
    )
    data = inspect_forward_preflight(args.collector_db, now_ms)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data, sort_keys=True))
    return 0 if data["data_24h"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
