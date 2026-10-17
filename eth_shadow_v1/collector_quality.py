"""Persistent collector data quality: missing slots, lag, checksum and health gates.

Reports observe collector storage only; they never start a market-data
fetch, simulation, background task, or live trading.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from .collector_store import CollectorStore, sha, PERIOD_MS

D = Decimal
DAY_MS = 86_400_000
MAX_HEARTBEAT_AGE_MS = 15_000
MAX_VALID_AGE_MS = 15_000
MAX_GAP_MS = 15_000
MIN_FREE_BYTES = 256 * 1024 * 1024


def percentile(values: list[Decimal], fraction: Decimal) -> str | None:
    if not values:
        return None
    values = sorted(values)
    index = max(0, math.ceil(len(values) * fraction) - 1)
    return str(values[index])


def audit_chain(db: sqlite3.Connection) -> dict[str, Any]:
    """Recompute every row's provenance and the full append-only chain."""
    last = "0"*64
    count = 0
    for row in db.execute(
        """SELECT checked_ms,source_ms,accepted,reason,latency_ms,bid,ask,
                  spread_pct,bid_depth_eur,ask_depth_eur,previous_hash,entry_hash,
                  payload_json FROM books ORDER BY seq"""):
        (checked, source, accepted, reason, latency, bid, ask, spread,
         bid_depth, ask_depth, previous, claimed, payload_json) = row
        if previous != last:
            raise ValueError("COLLECTOR_AUDIT_CHAIN_MISMATCH")
        if accepted:
            if not payload_json:
                raise ValueError("COLLECTOR_MISSING_RAW_BOOK")
            doc = json.loads(payload_json)
            raw, sample = doc["payload"], doc["sample"]
            if sample["payload_sha256"] != sha(raw):
                raise ValueError("COLLECTOR_BOOK_RAW_TAMPER")
            summary = sample["summary"]
            if (str(summary["best_bid_eur"]) != bid or str(summary["best_ask_eur"]) != ask
                    or int(summary["age_ms"]) != latency
                    or str(summary["spread_pct"]) != spread
                    or str(summary["bid_depth_eur"]) != bid_depth
                    or str(summary["ask_depth_eur"]) != ask_depth):
                raise ValueError("COLLECTOR_SUMMARY_TAMPER")
        else:
            if payload_json:
                raise ValueError("COLLECTOR_REJECTED_PAYLOAD_MUST_BE_NULL")
            sample = {"checked_ms": checked, "source_ms": source,
                      "accepted": False, "reason": reason, "payload_sha256": None,
                      "summary": None}
        if sample["checked_ms"] != checked or sample["source_ms"] != source or (
            bool(sample["accepted"]) != bool(accepted)) or sample["reason"] != reason:
            raise ValueError("COLLECTOR_AUDIT_STATE_MISMATCH")
        expected = sha({"previous_hash": last, "sample": sample})
        if claimed != expected:
            raise ValueError("COLLECTOR_AUDIT_HASH_MISMATCH")
        last, count = expected, count+1
    return {"status": "PASS", "rows_checked": count, "final_hash": last}


def evaluate_quality(db_path: Path, now_ms: int, *, verify_audit: bool = True) -> dict:
    """Read-only report; not a forward-shadow acceptance decision."""
    path = Path(db_path)
    if not path.exists():
        return {"status": "BLOCKED", "alerts": ["DB_MISSING"],
                "shadow_forward_status": "NOT_STARTED", "live_mutations": 0}
    db = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        info = dict(db.execute("SELECT key,value FROM info").fetchall())
        stats = db.execute("""
            SELECT COUNT(*), COALESCE(SUM(accepted),0), MIN(checked_ms), MAX(checked_ms),
                   MAX(CASE WHEN accepted=1 THEN checked_ms ELSE NULL END)
            FROM books
        """).fetchone()
        total, good, first, last, valid = stats
        started = int(info.get("started_ms") or (first or now_ms))
        elapsed = max(0, now_ms-started)
        # Expected slots since collector started include downtime: no silent reset
        expected = elapsed//PERIOD_MS + 1
        coverage = min(D(total)/D(max(expected,1)), D(1))
        valid_ratio = D(good)/D(max(total,1))
        lag = [D(row[0]) for row in db.execute(
            "SELECT latency_ms FROM books WHERE accepted=1 AND latency_ms IS NOT NULL")]
        spreads = [D(row[0]) for row in db.execute(
            "SELECT spread_pct FROM books WHERE accepted=1 AND spread_pct IS NOT NULL")]
        ordered = [r[0] for r in db.execute("SELECT checked_ms FROM books ORDER BY seq")]
        gaps = sum(1 for earlier, later in zip(ordered,ordered[1:])
                   if later-earlier > MAX_GAP_MS)
        max_gap = max([later-earlier for earlier,later in zip(ordered,ordered[1:])] or [0])
        reason_counts = dict(db.execute(
            "SELECT reason,COUNT(*) FROM books WHERE accepted=0 GROUP BY reason").fetchall())
        candle_count, latest_candle = db.execute(
            "SELECT COUNT(*), MAX(close_ms) FROM candles").fetchone()
        pair = db.execute(
            "SELECT checked_ms,pair_hash,active FROM pair_checks ORDER BY checked_ms DESC LIMIT 1"
        ).fetchone()
        free_bytes = shutil.disk_usage(path.parent).free
        audit = audit_chain(db) if verify_audit else {"status": "NOT_VERIFIED"}
        alerts = []
        if not total:
            alerts.append("NO_BOOK_SAMPLES")
        if elapsed < DAY_MS:
            alerts.append("WARMUP_UNDER_24H")
        if now_ms-int(info.get("heartbeat_ms",0)) > MAX_HEARTBEAT_AGE_MS:
            alerts.append("COLLECTOR_HEARTBEAT_STALE")
        if not valid or now_ms-valid > MAX_VALID_AGE_MS:
            alerts.append("LATEST_VALID_BOOK_STALE")
        if coverage < D("0.99"):
            alerts.append("SAMPLE_COVERAGE_BELOW_99PCT")
        if valid_ratio < D("0.99"):
            alerts.append("ACCEPTED_RATIO_BELOW_99PCT")
        if gaps:
            alerts.append("CAPTURE_GAPS_OVER_15S")
        if lag and D(percentile(lag,D(".95"))) > D(1500):
            alerts.append("SOURCE_AGE_P95_OVER_1500MS")
        if not pair or pair[2] != 1:
            alerts.append("PAIR_NOT_VERIFIED_ACTIVE")
        if info.get("pair_drift") == "true":
            alerts.append("PAIR_RULES_CHANGED_MANUAL_REVIEW")
        if not latest_candle or now_ms-latest_candle > 2*900000:
            alerts.append("CLOSED_CANDLES_STALE")
        if candle_count < 200:
            alerts.append("INSUFFICIENT_CANDLE_HISTORY")
        if free_bytes < MIN_FREE_BYTES:
            alerts.append("DISK_FREE_UNDER_256MB")
        if audit["status"] != "PASS":
            alerts.append("AUDIT_UNVERIFIED")
        # A 24h status means data capture QC passed, not profitability/forward GO.
        healthy = not alerts
        report = {
            "schema": "eth-eur-collector-quality-v1",
            "pair": "ETH/EUR", "region": "EEA", "mode": "READ_ONLY_DATA_QUALITY",
            "status": "DATA_READY_FOR_REVIEW" if healthy else "BLOCKED",
            "runtime_ms": elapsed, "start_ms": started, "report_at_ms": now_ms,
            "sample_interval_ms": PERIOD_MS, "sample_count": total,
            "accepted_count": good, "rejected_count": total-good,
            "expected_slots": expected,
            "coverage_ratio": str(coverage),
            "accepted_ratio": str(valid_ratio),
            "source_age_p95_ms": percentile(lag,D(".95")),
            "spread_p95_pct": percentile(spreads,D(".95")),
            "max_inter_observation_gap_ms": max_gap, "gaps_over_15s": gaps,
            "last_book_ms": last, "last_valid_book_ms": valid,
            "last_heartbeat_ms": int(info["heartbeat_ms"]) if info.get("heartbeat_ms") else None,
            "closed_candles": candle_count, "latest_candle_ms": latest_candle,
            "pair_active": bool(pair and pair[2] == 1),
            "pair_rules_changed": info.get("pair_drift") == "true",
            "rejection_reasons": reason_counts,
            "disk_free_bytes": free_bytes,
            "audit": audit, "alerts": sorted(alerts),
            "exchange_mutations": 0, "shadow_forward_status": "NOT_STARTED",
            "economic_approval": "NO_GO",
        }
        return report
    finally:
        db.close()
