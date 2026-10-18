"""Bounded ETH/EUR public GET pilot. Never orders; never auto-rearms.

Railway's default start command MUST be "status", not "run".
Run and arm explicitly require separate operator approval; no auto resume on crash.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from scripts.eth_eur_persistent_collector import atomic_json

DURATION_MS = 86_400_000
POLL_MS = 5000
SCHEMA = "eth-eur-24h-railway-pilot-v1"
ARM_FLAG = "EXPLICIT_PUBLIC_GET_APPROVAL"
LIVE_FLAG = "LIVE_TRADING"
STATE_FILE = "pilot-state.json"


def utc_ms() -> int:
    return time.time_ns() // 1_000_000


def read_state(root: Path) -> dict | None:
    try:
        return json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def safe_flags() -> None:
    if os.getenv(LIVE_FLAG, "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_BE_EXPLICIT_FALSE")
    if os.getenv(ARM_FLAG) != "I_APPROVE_PUBLIC_GET_24H":
        raise RuntimeError("PUBLIC_GET_APPROVAL_REQUIRED")


def arm(root: Path, now: int) -> dict:
    safe_flags()
    if not root.is_dir() or not (root / "STOPPED_BY_DEFAULT").exists():
        raise RuntimeError("PILOT_DIRECTORY_NOT_PREPARED")
    if (root / STATE_FILE).exists() or (root / "collector.sqlite").exists():
        raise RuntimeError("PILOT_ALREADY_ARMED_OR_HAS_DATA")
    state = {
        "schema": SCHEMA, "mode": "PUBLIC_GET_ONLY", "symbol": "ETH-EUR",
        "approval": "EXPLICIT_OPERATOR_APPROVAL", "status": "ARMED",
        "armed_at_ms": now, "started_at_ms": None, "deadline_at_ms": None,
        "real_exchange_mutations": 0, "forward_30d": "NOT_STARTED",
        "economic_approval": "NO_GO",
    }
    # Exclusive creation: no run may inherit an old or partially completed campaign.
    with (root / STATE_FILE).open("x", encoding="utf-8") as fd:
        json.dump(state, fd, sort_keys=True)
        fd.flush()
        os.fsync(fd.fileno())
    return state


def begin_once(root: Path, now: int) -> dict:
    safe_flags()
    state = read_state(root)
    if not isinstance(state, dict) or state.get("schema") != SCHEMA or state.get("status") != "ARMED":
        raise RuntimeError("PILOT_NOT_ARMED_OR_ALREADY_STARTED")
    if now < state["armed_at_ms"] or now - state["armed_at_ms"] > 15 * 60 * 1000:
        raise RuntimeError("PILOT_APPROVAL_EXPIRED")
    if (root / "collector.sqlite").exists():
        raise RuntimeError("UNEXPECTED_COLLECTOR_DB")
    state["status"] = "RUNNING_NO_AUTO_RESUME"
    state["started_at_ms"] = now
    state["deadline_at_ms"] = now + DURATION_MS
    atomic_json(root / STATE_FILE, state)  # commit BEFORE first network request
    return state


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fd:
        for piece in iter(lambda: fd.read(1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()


def sqlite_backup(source: Path, target: Path) -> dict:
    """Consistent snapshot from SQLite backup API; source stays read-only."""
    if not source.is_file() or target.exists() or source.resolve() == target.resolve():
        raise RuntimeError("INVALID_BACKUP_SOURCE_OR_TARGET")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Do not embed query string into URI path; quote filenames safely.
    from urllib.parse import quote
    uri = "file:" + quote(str(source.resolve()).replace("\\", "/"), safe="/:") + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=15)) as src:
            with closing(sqlite3.connect(target, timeout=15)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(f"file:{quote(str(target.resolve()).replace(chr(92), '/'), safe='/:')}?mode=ro", uri=True)) as chk:
                if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("BACKUP_SQLITE_INTEGRITY_FAILED")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"path": target.name, "size_bytes": target.stat().st_size, "sha256": sha256_file(target)}


def finish(root: Path, state: dict, *, reason: str, now: int) -> dict:
    """Finalization never grants trading permission, even on passing collector quality."""
    from eth_shadow_v1.forward_preflight import inspect_forward_preflight
    db = root / "collector.sqlite"
    report = inspect_forward_preflight(db, now)
    backup = None
    backup_error = None
    if db.is_file():
        try:
            backup = sqlite_backup(db, root / "reports" / "collector-backup.sqlite")
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            backup_error = type(exc).__name__
    status = "FINISHED_REVIEW_REQUIRED" if reason == "DEADLINE" and report["data_24h"] == "PASS" and backup and not backup_error else "BLOCKED"
    evidence = {
        "schema": SCHEMA, "status": status, "reason": reason,
        "started_at_ms": state["started_at_ms"], "deadline_at_ms": state["deadline_at_ms"],
        "stopped_at_ms": now, "preflight": report, "backup": backup,
        "backup_error": backup_error, "live_trading": "DISABLED",
        "exchange_mutations": 0, "forward_30d": "NOT_STARTED",
        "economic_approval": "NO_GO",
    }
    atomic_json(root / "reports" / "final-report.json", evidence)
    state["status"] = status
    state["stop_reason"] = reason
    state["stopped_at_ms"] = now
    atomic_json(root / STATE_FILE, state)
    return evidence


def run(root: Path) -> int:
    state = begin_once(root, utc_ms())
    reason = "INTERRUPTED"
    try:
        # Import only after approval and persistent RUNNING record.
        from eth_shadow_v1.collector import ContinuousReadOnlyCollector
        from eth_shadow_v1.config import Config
        config = Config.load(Path(__file__).resolve().parents[1] / "configs" / "eth_eur_grid_shadow_v1.toml")
        with ContinuousReadOnlyCollector(config, root / "collector.sqlite") as collector:
            collector.store.acquire(utc_ms())
            try:
                next_tick = time.monotonic()
                while True:
                    now = utc_ms()
                    if now >= state["deadline_at_ms"]:
                        reason = "DEADLINE"
                        break
                    # A 5-second cadence is measured from poll START, not
                    # request completion; never add HTTP latency to each slot.
                    collector.poll()
                    next_tick += POLL_MS / 1000
                    now_mono = time.monotonic()
                    if next_tick < now_mono:
                        # Skip missed ticks; do not create catch-up snapshots.
                        next_tick = now_mono
                    wait_ms = min(
                        max(0, int((next_tick - now_mono) * 1000)),
                        max(0, state["deadline_at_ms"] - utc_ms()),
                    )
                    if wait_ms:
                        time.sleep(wait_ms / 1000)
            finally:
                collector.store.release(utc_ms())
    except (Exception, KeyboardInterrupt) as exc:
        reason = "ERROR_" + type(exc).__name__
    evidence = finish(root, state, reason=reason, now=utc_ms())
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "FINISHED_REVIEW_REQUIRED" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="24h public GET collector, fail-closed")
    parser.add_argument("command", choices=["status", "arm", "run"])
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    args = parser.parse_args()
    root = args.data_dir
    if args.command == "status":
        print(json.dumps({"state": read_state(root), "collector_started": False,
                          "exchange_mutations": 0, "live": "DISABLED"}, sort_keys=True))
        return 0
    if args.command == "arm":
        print(json.dumps(arm(root, utc_ms()), sort_keys=True))
        return 0
    return run(root)


if __name__ == "__main__":
    raise SystemExit(main())
