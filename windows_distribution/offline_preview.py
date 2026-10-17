"""Windows OFFLINE development preview. Never connects to any exchange.

This is a deliberately bounded demonstration of the existing
GridBotSupervisor lifecycle. It does not start a collector, load vaults,
request an API key, or expose order/strategy configuration.

A successful preview never means the software is production-approved.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from bot_main import offline_main


def run_fixture() -> dict[str, object]:
    """Run the real supervisor's offline fixture in isolated temporary SQLite."""
    if os.environ.get("LIVE_TRADING", "false").strip().lower() != "false":
        raise RuntimeError("LIVE_ENV_FORBIDDEN")
    with TemporaryDirectory(prefix="gridconsole-preview-") as temp:
        report_path = Path(temp) / "report.json"
        args = [
            "--offline-fixture", "--db", str(Path(temp) / "fixture.sqlite"),
            "--ticks", "3", "--interval-ms", "0",
            "--report", str(report_path),
        ]
        output = StringIO()
        with redirect_stdout(output):
            result_code = offline_main(args)
        if result_code != 0:
            raise RuntimeError("OFFLINE_FIXTURE_FAILED")
        report = json.loads(report_path.read_text(encoding="utf-8"))
    required = {
        "schema": "offline-supervisor-v1",
        "result": "PASS",
        "state": "STOPPED",
        "ticks_completed": 3,
        "source": "SYNTHETIC_OFFLINE_ONLY",
        "exchange_orders_sent": 0,
        "authenticated_account_test": "NOT_RUN",
        "live_trading": "DISABLED",
    }
    for key, value in required.items():
        if type(report.get(key)) is not type(value) or report.get(key) != value:
            raise RuntimeError("OFFLINE_REPORT_MISMATCH")
    # Fixed, safe release-report projection: no balances, credentials, or IDs.
    return {
        "schema": "grid-console-windows-preview-v1",
        "status": "DEV_DEMO_PASS",
        "fixture_ticks": 3,
        "account_connected": False,
        "synthetic_data_only": True,
        "network_enabled": False,
        "orders_sent": 0,
        "continuous_collector": "NOT_STARTED",
        "signed_customer_release": False,
        "live_trading": "DISABLED",
        "real_account_test": "NOT_RUN",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grid Console OFFLINE development preview: NO account / NO trading."
    )
    parser.add_argument("--self-test", action="store_true",
                        help="Run 3 bounded synthetic checks, output safe JSON, exit")
    args = parser.parse_args(argv)
    try:
        result = run_fixture()
        print(json.dumps(result, sort_keys=True))
        if not args.self_test and sys.stdin is not None and sys.stdin.isatty():
            print("Development preview only. Close the window to exit.")
            input("Press Enter to close...")
        return 0
    except Exception:
        # Secrets/paths must never be included in terminal or packaged diagnostics.
        print(json.dumps({
            "schema": "grid-console-windows-preview-v1",
            "status": "DEV_DEMO_FAIL",
            "real_account_test": "NOT_RUN",
            "orders_sent": 0,
            "live_trading": "DISABLED",
        }, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
