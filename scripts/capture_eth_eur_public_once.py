"""One finite read-only capture. Saves reports even when Revolut X public GETs fail."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eth_shadow_v1.config import Config
from eth_shadow_v1.public_readonly import PublicOnlyClient, capture_once

CONFIG = Path(__file__).resolve().parent.parent / "configs" / "eth_eur_grid_shadow_v1.toml"


def main() -> int:
    parser = argparse.ArgumentParser(description="ETH/EUR read-only public capture; no trading")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    cfg = Config.load(CONFIG)
    client = PublicOnlyClient()
    try:
        capture, report = capture_once(cfg=cfg, samples=args.samples, client=client)
    finally:
        client.close()
    for dest, record in ((args.capture, capture), (args.report, report)):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_OFFLINE_INSPECTION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
