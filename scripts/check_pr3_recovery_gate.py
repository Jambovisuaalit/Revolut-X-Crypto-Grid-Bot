"""Fail-closed acceptance check for two OS-level recovery test reports.

This script runs after mock-only integration scripts. A green unittest exit
code alone does not qualify: verify exact counters and cleanup, then bind
the report to the current CI commit. No API credentials or exchange access.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def check(report_dir: Path, platform: str, commit_sha: str) -> dict:
    if platform not in ("linux", "win32"):
        raise ValueError("UNSUPPORTED_TEST_PLATFORM")
    if len(commit_sha) != 40 or any(c not in "0123456789abcdef" for c in commit_sha):
        raise ValueError("INVALID_COMMIT_SHA")
    recovery = json.loads((report_dir / f"process-recovery-{platform}.json").read_text(encoding="utf-8"))
    drawdown = json.loads((report_dir / f"drawdown-{platform}.json").read_text(encoding="utf-8"))
    if recovery.get("status") != "PASS" or recovery.get("cleanup_error") is not None:
        raise AssertionError("RECOVERY_REPORT_OR_CLEANUP_FAILED")
    if recovery.get("platform") != platform:
        raise AssertionError("RECOVERY_PLATFORM_MISMATCH")
    scenarios = recovery.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 2:
        raise AssertionError("RECOVERY_SCENARIO_COUNT_MISMATCH")
    expected = {"accepted_ack_lost", "unknown_fail_closed"}
    if {x.get("scenario") for x in scenarios} != expected:
        raise AssertionError("RECOVERY_SCENARIOS_MISSING")
    for item in scenarios:
        for key, val in (
            ("status", "PASS"),
            ("process_exit_code", 73),
            ("post_count", 1),
            ("put_count", 0),
            ("duplicate_orders", 0),
        ):
            if type(item.get(key)) is not type(val) or item.get(key) != val:
                raise AssertionError(f"RECOVERY_{item.get('scenario')}_{key}_MISMATCH")
    for key,val in (
        ("status","PASS"),
        ("baseline_equity","10000"),
        ("pre_threshold_equity","9701"),
        ("breach_equity","9690"),
        ("drawdown_percent","3.10"),
        ("kill_switch_persisted",True),
        ("restart_blocked",True),
        ("post_count",0),
        ("cancel_all_count",1),
    ):
        if type(drawdown.get(key)) is not type(val) or drawdown.get(key) != val:
            raise AssertionError(f"DRAWDOWN_{key}_MISMATCH")
    return {
        "schema": "pr3-recovery-integration-gate-v1",
        "status": "PASS",
        "git_commit_sha": commit_sha,
        "platform": platform,
        "scenarios": sorted(expected),
        "process_exit_code": 73,
        "post_count_per_scenario": 1,
        "put_count_per_scenario": 0,
        "duplicate_orders": 0,
        "cleanup_error": None,
        "kill_switch_persisted": True,
        "cancellation_attempts_mock": 1,
        "exchange_http_mutations": 0,
        "actual_authenticated_venue_test": "NOT_RUN",
        "live_trading": "DISABLED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--commit-sha", type=str, required=True)
    args = parser.parse_args()
    dest = args.report_dir / f"integration-verdict-{sys.platform}.json"
    try:
        verdict = check(args.report_dir, sys.platform, args.commit_sha)
        exit_code = 0
    except (OSError, ValueError, AssertionError, KeyError, TypeError, json.JSONDecodeError) as exc:
        verdict = {
            "schema": "pr3-recovery-integration-gate-v1",
            "status": "FAIL",
            "git_commit_sha": args.commit_sha,
            "platform": sys.platform,
            "reason": f"{type(exc).__name__}: {str(exc)[:180]}",
            "actual_authenticated_venue_test": "NOT_RUN",
            "live_trading": "DISABLED",
        }
        exit_code = 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(verdict, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(verdict, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
