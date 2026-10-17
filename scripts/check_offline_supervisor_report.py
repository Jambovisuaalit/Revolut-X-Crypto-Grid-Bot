"""Fail an unsafe or superficially green offline supervisor CI report."""
from __future__ import annotations
import json
from pathlib import Path
import sys

def check(path: Path) -> None:
    v = json.loads(path.read_text(encoding="utf-8"))
    required={
        "schema":"offline-supervisor-v1",
        "result":"PASS",
        "state":"STOPPED",
        "ticks_completed":3,
        "last_equity_eur":"1000",
        "source":"SYNTHETIC_OFFLINE_ONLY",
        "exchange_orders_sent":0,
        "authenticated_account_test":"NOT_RUN",
        "live_trading":"DISABLED",
    }
    for key,expected in required.items():
        if type(v.get(key)) is not type(expected) or v.get(key)!=expected:
            raise SystemExit(f"FAIL: {key} mismatch")
    print("PASS: offline fixture only; lifecycle stopped; no exchange actions")

if __name__=="__main__":
    check(Path(sys.argv[1]))
