"""Fail CI when the offline shadow report hides an error or claims market validation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from decimal import Decimal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    expected = {
        "mode": "SHADOW_ONLY", "pair": "ETH-EUR",
        "pair_metadata_source": "synthetic_fixture",
        "data_provenance": "SYNTHETIC_FIXTURE_ONLY",
        "audit_integrity": "PASS", "exchange_orders": 0,
        "config_hash": "4f4d337ed15e5b88e1e979015acfab60c2a34fa87f177a2be97b2064d7de8c33",
        "last_hash": "27cca6b2600a76486b8ba0bf6fe5a6ac7c359c3d56944525bef39c7c1dd41ad8",
        "real_mutation_requests": 0, "closed_cycles": 1,
        "synthetic_fills": 2, "observations": 5,
        "forward_shadow_30d": "NOT_RUN", "acceptance": "NO_GO_SYNTHETIC_ONLY",
        "historical_30d_replay": "BLOCKED_NO_AUTHENTICATED_ORDERBOOK_HISTORY",
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise SystemExit(f"FAIL: {key} is {report.get(key)!r}; expected {value!r}")
    if Decimal(report["simulated_total_pnl_eur"]) <= 0 or report["hard_stop_triggered"]:
        raise SystemExit("FAIL: invalid deterministic fixture output")
    print("PASS: deterministic synthetic replay, audit, zero exchange writes; economic approval BLOCKED")


if __name__ == "__main__":
    main()
