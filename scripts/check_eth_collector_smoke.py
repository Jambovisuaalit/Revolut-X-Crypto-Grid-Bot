"""Fail if the three-sample, public-only CI check claims forward readiness."""
from __future__ import annotations

import json
from pathlib import Path
import sys

def main(path: Path) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    needed = {
        "pair": "ETH/EUR", "region": "EEA",
        "sample_count": 3, "accepted_count": 3, "rejected_count": 0,
        "status": "BLOCKED", "shadow_forward_status": "NOT_STARTED",
        "economic_approval": "NO_GO", "exchange_mutations": 0,
        "bounded_polls_completed": 3,
    }
    for key, expected in needed.items():
        if report.get(key) != expected:
            raise SystemExit(f"FAIL {key}: {report.get(key)!r}, expected {expected!r}")
    if report["audit"]["status"] != "PASS" or report["audit"]["rows_checked"] != 3:
        raise SystemExit("FAIL incomplete audit")
    if "WARMUP_UNDER_24H" not in report["alerts"]:
        raise SystemExit("FAIL premature health readiness")
    print("PASS public read-only 3-poll smoke; 24h readiness BLOCKED")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
