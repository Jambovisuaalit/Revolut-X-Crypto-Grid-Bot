"""Deliberate local-only Windows vault actions; NEVER makes API calls.

API key entry uses interactive getpass, not argv/env/config/clipboard uploads.
Only the public key may be displayed.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Allow manual 'python scripts/windows_vault_cli.py ...' from repo root.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from windows_key_vault import WindowsUserVault, VaultError


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(
        description="Windows CURRENT USER DPAPI vault (no network, no trading)"
    )
    sub=parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init", help="Generate private key in Windows DPAPI vault, display PUBLIC key")
    sub.add_parser("public", help="Display PUBLIC PEM for manual Revolut X registration")
    sub.add_parser("status", help="Show safe status only")
    sub.add_parser("link", help="Prompt locally for account API key without echo")
    remove=sub.add_parser("remove", help="Remove only LOCAL encrypted file; DOES NOT revoke API key")
    remove.add_argument("--confirm-local-only-removal", action="store_true")
    args=parser.parse_args(argv)
    try:
        vault=WindowsUserVault()
        if args.action=="init":
            print(vault.initialize(), end="")
            print("Public key only. Keep the API/private key secret.")
        elif args.action=="public":
            print(vault.public_key_pem(),end="")
        elif args.action=="status":
            import json
            print(json.dumps(vault.status(),sort_keys=True))
        elif args.action=="link":
            print("Input stays on this computer. No Revolut X request is sent.")
            api_key=getpass.getpass("Enter the Revolut X API key (input hidden): ")
            vault.bind_api_key(api_key)
            print("API key encrypted locally. Account connectivity NOT VERIFIED.")
        elif args.action=="remove":
            if not args.confirm_local_only_removal:
                raise VaultError("REQUIRES_EXPLICIT_LOCAL_REMOVAL_FLAG")
            print("WARNING: Removing local ciphertext DOES NOT revoke Revolut X API access.")
            vault.remove_local(confirmed=True)
            print("Local encrypted vault removed; revoke the key separately in Revolut X.")
        return 0
    except VaultError as exc:
        print("BLOCKED: "+str(exc),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
