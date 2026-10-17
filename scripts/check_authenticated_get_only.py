"""Machine-readable structural safety attestation. No account API calls."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "account_readonly.py"


def audit() -> dict:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if any(name in names for name in (
        "place_order", "replace_order", "cancel_order", "cancel_all_orders",
        "request", "post", "put", "patch", "delete",
    )):
        raise AssertionError("write-capable method exists")
    uses = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("post", "put", "patch", "delete", "request"):
                raise AssertionError("generic or mutating HTTP transport is used")
            if node.func.attr == "get" and isinstance(node.func.value, ast.Attribute):
                if node.func.value.attr == "_transport":
                    uses.append("self._transport.get")
        if isinstance(node, ast.ImportFrom) and node.module in (
            "revolut_x_client", "grid_executor", "bot_main", "supervisor_runtime"
        ):
            raise AssertionError("GET-only adapter imports privileged trading component")
    if uses != ["self._transport.get"]:
        raise AssertionError("unexpected transport call sites")
    return {
        "schema": "auth-account-get-only-v1",
        "status": "PASS",
        "allowed_http_methods": ["GET"],
        "expected_transport_callsites": 1,
        "endpoint_allowlist": [
            "/1.0/balances", "/1.0/orders/active",
            "/1.0/orders/historical", "/1.0/orders/{uuid}",
            "/1.0/orders/fills/{uuid}",
        ],
        "execution_mutations": 0,
        "read_only_permissions_verified_at_exchange": False,
        "signed_get_mock_contract": "TESTED_IN_CI",
        "authenticated_real_account_calls": "NOT_RUN",
        "supervisor_integrated_with_account": False,
        "market_data_collector_started": False,
        "forward_shadow": "NOT_RUN",
        "live_trading_approval": "NO_GO",
    }


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--commit-sha",required=True)
    p.add_argument("--output",required=True,type=Path)
    args=p.parse_args()
    try:
        if not re.fullmatch("[0-9a-f]{40}", args.commit_sha):
            raise ValueError("invalid commit SHA")
        report=audit()
        report["git_commit_sha"]=args.commit_sha
        status=0
    except (ValueError, AssertionError, SyntaxError) as exc:
        report={
            "schema":"auth-account-get-only-v1",
            "status":"FAIL", "git_commit_sha":args.commit_sha,
            "reason":type(exc).__name__+":"+str(exc)[:120],
            "authenticated_real_account_calls":"NOT_RUN",
            "live_trading_approval":"NO_GO",
        }
        status=1
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True))
    return status


if __name__=="__main__":
    sys.exit(main())
