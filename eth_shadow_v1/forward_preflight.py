"""Offline acceptance preflight for 24h ETH/EUR public-book collection.

Does not fetch market data, run simulations or change SQLite.
Never promotes a dataset to a 30-day forward strategy result.
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
import sqlite3

from eth_shadow_v1.collector_quality import DAY_MS, evaluate_quality
from eth_shadow_v1.collector_store import PERIOD_MS

SCHEMA = "eth-eur-forward-preflight-v1"
MIN_COVERAGE = Decimal("0.99")
FORWARD_DAYS = 30


def _blocked(reason: str) -> dict:
    return {
        "schema": SCHEMA, "status": "BLOCKED", "reasons": [reason],
        "data_24h": "NO_GO", "forward_30d": "NOT_STARTED",
        "observed_unique_slots": 0, "accepted_unique_slots": 0,
        "expected_slots": DAY_MS // PERIOD_MS,
        "coverage_ratio": "0", "accepted_coverage_ratio": "0",
        "collector_quality": "NOT_VERIFIED", "collector_audit": "NOT_VERIFIED",
        "real_mutations": 0, "live_trading": "DISABLED",
        "economic_approval": "NO_GO",
    }


def inspect_forward_preflight(database: Path, now_ms: int) -> dict:
    """Fail closed for missing, malformed, too-short, stale or tampered collector history."""
    if type(now_ms) is not int or now_ms <= DAY_MS:
        return _blocked("INVALID_AUDIT_TIME")
    path = Path(database)
    if not path.is_file() or path.is_symlink():
        return _blocked("COLLECTOR_DB_UNAVAILABLE")
    try:
        quality = evaluate_quality(path, now_ms, verify_audit=True, full_audit=True)
        uri = "file:" + quote(str(path.resolve()).replace("\\", "/"), safe="/:") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as db:
            db.execute("PRAGMA query_only=ON")
            rows = db.execute(
                "SELECT checked_ms,accepted FROM books WHERE checked_ms>=? AND checked_ms<=? ORDER BY checked_ms",
                (now_ms - DAY_MS, now_ms),
            ).fetchall()
            if any(type(timestamp) is not int or timestamp <= 0 for timestamp, _ in rows):
                return _blocked("INVALID_CAPTURE_TIMESTAMP")
            # Count scheduled time slots, not raw rows: duplicates must not hide downtime.
            start = now_ms - DAY_MS
            slots = set()
            accepted = set()
            for stamp, valid in rows:
                slot = min((stamp - start) // PERIOD_MS, DAY_MS // PERIOD_MS - 1)
                if slot < 0:
                    continue
                slots.add(slot)
                if valid == 1:
                    accepted.add(slot)
            expected = DAY_MS // PERIOD_MS
            cover = Decimal(len(slots)) / Decimal(expected)
            good_cover = Decimal(len(accepted)) / Decimal(expected)
            reasons = list(quality.get("alerts") or [])
            info = dict(db.execute("SELECT key,value FROM info"))
            try:
                started_ms = int(info["started_ms"])
            except (KeyError, ValueError, TypeError):
                reasons.append("COLLECTOR_START_UNVERIFIED")
                started_ms = now_ms
            if started_ms > start:
                reasons.append("FULL_24H_NOT_OBSERVED")
            if cover < MIN_COVERAGE:
                reasons.append("24H_UNIQUE_SLOT_COVERAGE_BELOW_99PCT")
            if good_cover < MIN_COVERAGE:
                reasons.append("24H_ACCEPTED_UNIQUE_COVERAGE_BELOW_99PCT")
            if quality.get("status") != "DATA_READY_FOR_REVIEW":
                reasons.append("COLLECTOR_QUALITY_NOT_APPROVED")
            if quality.get("audit", {}).get("status") != "PASS" or quality.get("audit", {}).get("scope") != "FULL":
                reasons.append("FULL_AUDIT_NOT_APPROVED")
            if quality.get("pair") != "ETH/EUR" or quality.get("mode") != "READ_ONLY_DATA_QUALITY":
                reasons.append("INVALID_COLLECTOR_PROVENANCE")
            reasons = sorted(set(reasons))
            return {
                **_blocked("DATA_QUALITY_PENDING"),
                "status": "READY_FOR_MANUAL_REVIEW" if not reasons else "BLOCKED",
                "reasons": reasons,
                "data_24h": "PASS" if not reasons else "NO_GO",
                "observed_unique_slots": len(slots),
                "accepted_unique_slots": len(accepted),
                "expected_slots": expected,
                "coverage_ratio": format(cover, "f"),
                "accepted_coverage_ratio": format(good_cover, "f"),
                "collector_quality": quality.get("status", "NOT_VERIFIED"),
                "collector_audit": quality.get("audit", {}).get("status", "NOT_VERIFIED"),
                "forward_30d": "NOT_STARTED",
                "economic_approval": "NO_GO",
            }
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, ArithmeticError):
        return _blocked("COLLECTOR_DB_INVALID_OR_UNVERIFIED")
