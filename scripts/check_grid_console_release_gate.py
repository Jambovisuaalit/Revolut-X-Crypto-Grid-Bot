"""Machine-readable commercial release gate, deliberately NO_GO for this preview.

Green unit tests mean the portal is safe to demo, NOT that user-account
connectivity, live execution, legal clearance or expected returns are validated.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from product_shell.server import VERSION


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    blocked = {
        "account_readonly_auth":"MOCK_TESTED_REAL_ACCOUNT_NOT_VERIFIED",
        "private_key_windows_vault":"WINDOWS_DPAPI_TESTED_NO_CUSTOMER_KEYS",
        "signed_windows_installer":"UNSIGNED_DEV_INSTALLER_ONLY_NO_TRUSTED_SIGNATURE",
        "venue_reconciliation_live":"NOT_APPROVED",
        "live_order_mutations":"BLOCKED",
        "24h_real_collector_host_evidence":"NOT_VERIFIED",
        "30d_forward_shadow":"NOT_COMPLETED",
        "pnl_stress_and_benchmark":"NOT_APPROVED",
        "legal_MiCA_and_revolut_terms":"REVIEW_REQUIRED",
        "support_and_incident_runbook":"NOT_APPROVED",
    }
    report={
        "product":"Grid Console", "version":VERSION,
        "preview_approval":"DEV_DEMO_ONLY",
        "sellable_product":"NO_GO",
        "live_trading":"NOT_ENABLED",
        "release_blockers":blocked,
        "orders_sent":0,
        "buyer_funds_used_eur":"0",
        "app_integration":"SOURCE_TREE_MERGED_NOT_CUSTOMER_READY",
        "customer_installable_ui":"NOT_BUILT_SIGNED",
        "real_account_get_calls":"NOT_RUN",
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("PASS: preview code gate; customer-sale and live trading remain NO_GO")
    print(json.dumps(report,sort_keys=True))


if __name__=="__main__":
    main()
