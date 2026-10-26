"""Explicitly approved, one-shot 180s public GET-only Railway storage/cadence probe.

No account APIs, credentials, orders, shadow strategy, live trading or auto-restart.
Unique /data/three-minute-probe directory prevents reruns after any interruption.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import time

from eth_shadow_v1.collector import ContinuousReadOnlyCollector
from eth_shadow_v1.config import Config
from eth_shadow_v1.collector_quality import audit_chain
from scripts.eth_eur_persistent_collector import atomic_json

DURATION_SECONDS = 180
MAX_BOOK_POLLS = 36
INTERVAL_SECONDS = 5
MIN_FREE = 256 * 1024 * 1024
PROBE_NAME = "three-minute-probe"
APPROVAL_PHRASE = "APPROVED_GET_ONLY_3MIN_2026_10_18"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _timeout(_signum, _frame) -> None:
    raise TimeoutError("THREE_MINUTE_HARD_TIMEOUT")


def begin(root: Path, approved: str, *, now: int) -> tuple[Path, dict]:
    if os.getenv("LIVE_TRADING", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_BE_DISABLED")
    if approved != APPROVAL_PHRASE:
        raise RuntimeError("EXPLICIT_3MIN_APPROVAL_REQUIRED")
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("PERSISTENT_VOLUME_MISSING")
    if shutil.disk_usage(root).free < MIN_FREE:
        raise RuntimeError("DISK_RESERVE_BELOW_256MIB")
    directory = root / PROBE_NAME
    # Directory creation is atomic. Existence blocks automatic retries on ANY failure.
    directory.mkdir(mode=0o700, exist_ok=False)
    state = {
        "schema": "revx-public-get-3min-probe-v1",
        "status": "STARTED_NO_RETRY", "start_ms": now,
        "absolute_deadline_ms": now + DURATION_SECONDS * 1000,
        "max_book_polls": MAX_BOOK_POLLS, "exchange_mutations": 0,
        "live_trading": "DISABLED", "forward_30d": "NOT_STARTED",
        "economic_approval": "NO_GO",
    }
    atomic_json(directory / "state.json", state)
    return directory, state


def summarize(directory: Path, state: dict, *, polls: int, end_ms: int,
              cause: str, free_before: int) -> dict:
    database = directory / "collector.sqlite"
    books = accepted = 0
    observed_bytes = 0
    max_book_bytes = 0
    audit = {"status": "NO_DATABASE"}
    db_bytes = 0
    if database.is_file():
        db_bytes = database.stat().st_size
        with closing(sqlite3.connect(database)) as con:
            books, accepted = con.execute(
                "SELECT COUNT(*),COALESCE(SUM(accepted),0) FROM books"
            ).fetchone()
            sizes = [len(row[0].encode("utf-8")) for row in con.execute(
                "SELECT payload_json FROM books WHERE accepted=1 AND payload_json IS NOT NULL")]
            observed_bytes = sum(sizes)
            max_book_bytes = max(sizes, default=0)
            audit = audit_chain(con, full=True)
    free_after = shutil.disk_usage(directory).free
    # Whole-directory footprint includes WAL/SHM, journals and metadata if present.
    files = {p.name: p.stat().st_size for p in directory.iterdir() if p.is_file()}
    sqlite_disk_bytes = sum(n for name, n in files.items() if name.startswith("collector.sqlite"))
    mean_payload = observed_bytes / accepted if accepted else None
    # Bound conservative data+full-local-backup requirement against available space.
    projected_db_bytes = (sqlite_disk_bytes / max(books, 1)) * (86400 / INTERVAL_SECONDS)
    backup_budget_bytes = 2 * projected_db_bytes
    report = {
        "schema": state["schema"], "status": "REVIEW_REQUIRED" if cause == "TIMEBOX_COMPLETED" and audit["status"] == "PASS" else "BLOCKED",
        "cause": cause, "start_ms": state["start_ms"], "end_ms": end_ms,
        "duration_ms": end_ms - state["start_ms"], "deadline_ms": state["absolute_deadline_ms"],
        "book_poll_attempts": polls, "sqlite_book_rows": books, "accepted_rows": accepted,
        "accepted_ratio": accepted / max(books, 1),
        "payload_mean_bytes": mean_payload, "payload_max_bytes": max_book_bytes,
        "accepted_payload_total_bytes": observed_bytes, "sqlite_file_bytes": db_bytes,
        "sqlite_all_files_bytes": sqlite_disk_bytes,
        "free_before_bytes": free_before, "free_after_bytes": free_after,
        "remaining_above_256mib": max(0, free_after - MIN_FREE),
        "estimated_24h_db_bytes_from_probe": round(projected_db_bytes),
        "estimated_24h_with_local_backup_bytes": round(backup_budget_bytes),
        "backup_capacity_estimate_pass": backup_budget_bytes <= max(0, free_after - MIN_FREE),
        "full_chain_audit": audit,
        "live_trading": "DISABLED", "exchange_mutations": 0,
        "forward_30d": "NOT_STARTED", "economic_approval": "NO_GO",
        "notes": "Sizing is an estimate; DB growth and 24h free-tier continuity not proven.",
    }
    atomic_json(directory / "final-report.json", report)
    state["status"] = report["status"]
    state["end_ms"] = end_ms
    atomic_json(directory / "state.json", state)
    return report


def execute(root: Path, approved: str, *, clock_ms=now_ms,
            mono=time.monotonic, sleep=time.sleep, collector_factory=ContinuousReadOnlyCollector,
            use_alarm: bool = True) -> dict:
    directory, state = begin(root, approved, now=clock_ms())
    free_before = shutil.disk_usage(root).free
    polls = 0
    cause = "INTERRUPTED"
    prior_handler = None
    try:
        if use_alarm:
            prior_handler = signal.signal(signal.SIGALRM, _timeout)
            signal.alarm(DURATION_SECONDS)
        cfg = Config.load(Path(__file__).resolve().parents[1] / "configs" / "eth_eur_grid_shadow_v1.toml")
        # The collector only calls allowlisted public GETs; no trading runtime imported.
        with collector_factory(cfg, directory / "collector.sqlite") as collector:
            collector.store.acquire(clock_ms())
            try:
                next_tick = mono()
                while polls < MAX_BOOK_POLLS and clock_ms() < state["absolute_deadline_ms"]:
                    collector.poll()
                    polls += 1
                    next_tick += INTERVAL_SECONDS
                    remaining_ms = state["absolute_deadline_ms"] - clock_ms()
                    if remaining_ms <= 0 or polls >= MAX_BOOK_POLLS:
                        break
                    if mono() > next_tick:
                        next_tick = mono() + INTERVAL_SECONDS
                    sleep(min(max(0, next_tick - mono()), remaining_ms / 1000))
                cause = "TIMEBOX_COMPLETED" if polls == MAX_BOOK_POLLS or clock_ms() >= state["absolute_deadline_ms"] else "INTERRUPTED"
            finally:
                collector.store.release(clock_ms())
    except (Exception, KeyboardInterrupt) as exc:
        cause = "STOPPED_" + type(exc).__name__
    finally:
        if use_alarm:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, prior_handler)
    try:
        report = summarize(directory, state, polls=polls, end_ms=clock_ms(),
                           cause=cause, free_before=free_before)
    except Exception as exc:
        report = {"schema": state["schema"], "status": "BLOCKED", "cause": cause,
                  "report_error": type(exc).__name__, "book_poll_attempts": polls,
                  "exchange_mutations": 0, "live_trading": "DISABLED",
                  "economic_approval": "NO_GO"}
        atomic_json(directory / "final-report.json", report)
        state["status"] = "BLOCKED"
        atomic_json(directory / "state.json", state)
    print("RAILWAY_3MIN_FINAL " + json.dumps(report, sort_keys=True), flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicitly approved 180s GET-only probe")
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    parser.add_argument("--approve", required=True)
    args = parser.parse_args()
    report = execute(args.data_dir, args.approve)
    return 0 if report["status"] == "REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
