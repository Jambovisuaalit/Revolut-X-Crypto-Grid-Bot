"""Offline bounded GET pilot report checker.

The report must remain explicitly NOT APPROVED FOR REAL ACCOUNT USE.
Never accepts or emits credentials, balances or order identifiers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

SCHEMA="bounded-account-readonly-pilot-gate-v1"


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--commit-sha",required=True)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    verdict={
        "schema":SCHEMA,
        "status":"FAIL",
        "git_commit_sha":args.commit_sha,
        "real_account_gets":"NOT_RUN",
        "real_account_test_approval":"NOT_GRANTED",
        "client_api_permissions":"NOT_VERIFIED",
        "signed_installer":"NOT_BUILT",
        "live_trading_approval":"NO_GO",
        "max_pilot_get_attempts":25,
        "max_pages_per_list":10,
        "pilot_auto_429_retries":0,
        "data_exported":False,
        "venue_mutations_sent":0,
    }
    result=1
    try:
        if not re.fullmatch("[0-9a-f]{40}",args.commit_sha):
            raise ValueError("INVALID_COMMIT_SHA")
        from account_pilot_gate import PilotBounds, GuardedGetTransport
        from account_readonly import ReadLimits
        # Immutable constructors reject any attempt to expand pilot budget.
        assert PilotBounds() == PilotBounds(max_attempts=25,max_pages_per_list=10)
        assert ReadLimits().max_rate_retries == 0
        assert not hasattr(GuardedGetTransport,"post")
        assert not hasattr(GuardedGetTransport,"request")
        verdict["status"]="PASS"
        result=0
    except Exception:
        verdict["status"]="FAIL"
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(verdict,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(verdict,sort_keys=True))
    return result

if __name__=="__main__":
    sys.exit(main())
