"""Conservative, auditable vault release gate; intentionally NOT pilot approved."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]

def verify() -> dict:
    vault_src=(ROOT/"windows_key_vault.py").read_text("utf-8")
    cli_src=(ROOT/"scripts"/"windows_vault_cli.py").read_text("utf-8")
    tree=ast.parse(vault_src)
    names={n.name for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
    required={
        "initialize","public_key_pem","bind_api_key","remove_local",
        "make_get_only_reader","_atomic_write","_locked","_secure",
    }
    if not required.issubset(names):
        raise AssertionError("VAULT_METHOD_MISSING")
    banned=["CryptProtectData" + "(plain" + ", \"\", None, None, None, 0)",
            "CRYPTPROTECT_LOCAL_MACHINE","from revolut_x_client import",
            "private.pem","requests.post(", "requests.put(", "requests.delete("]
    if any(k in vault_src for k in banned):
        raise AssertionError("VAULT_MAY_EXPOSE_SECRETS_OR_WRITE")
    if "getpass.getpass" not in cli_src or "--api-key" in cli_src:
        raise AssertionError("CLI_MUST_NOT_EXPOSE_KEY_IN_ARGV")
    if "LOCALAPPDATA" not in vault_src or "win32crypt" not in vault_src:
        raise AssertionError("CURRENT_USER_DPAPI_REQUIRED")
    return {
        "schema":"windows-dpapi-user-vault-v1",
        "status":"PASS",
        "native_windows_dpapi_and_acl_tests": "RUN_BY_CI_ON_WINDOWS",
        "linux_fail_closed_test": "RUN_BY_CI_ON_UBUNTU",
        "real_account_credentials_used":False,
        "real_account_api_calls":"NOT_RUN",
        "api_key_permission_scope":"NOT_VERIFIED",
        "public_key_registration":"NOT_PERFORMED",
        "read_only_pilot":"NOT_STARTED",
        "live_trading":"DISABLED",
        "main_merged":False,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--commit-sha",required=True)
    p.add_argument("--output",required=True,type=Path)
    args=p.parse_args()
    try:
        if not re.fullmatch(r"[0-9a-f]{40}",args.commit_sha):
            raise ValueError("COMMIT_SHA_INVALID")
        verdict=verify()
        verdict["git_commit_sha"]=args.commit_sha
        code=0
    except (AssertionError, ValueError, SyntaxError) as exc:
        verdict={
            "schema":"windows-dpapi-user-vault-v1",
            "status":"FAIL","git_commit_sha":args.commit_sha,
            "reason":type(exc).__name__+":"+str(exc)[:120],
            "real_account_api_calls":"NOT_RUN","live_trading":"DISABLED",
        }
        code=1
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(verdict,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(verdict,sort_keys=True))
    return code

if __name__=="__main__":
    sys.exit(main())
