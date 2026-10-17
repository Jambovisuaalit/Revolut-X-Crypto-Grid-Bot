"""Append-only persistent ETH/EUR public observations with a single-owner SQLite lease.

This storage layer never imports an exchange trading client. No HTTP. WAL and
BEGIN IMMEDIATE make event+hash+heartbeat writes atomic and restart-safe.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .journal import canonical

PERIOD_MS = 5_000
LEASE_MS = 60_000
MIN_FREE_BYTES = 256 * 1024 * 1024


def sha(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


class CollectorStore:
    """Durable single-writer collector DB, fail closed if another owner is active."""

    def __init__(self, path: Path, owner: str):
        if not owner or len(owner) > 128:
            raise ValueError("collector owner identifier required")
        self.path, self.owner = Path(path), owner
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS info(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS books(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_ms INTEGER NOT NULL,
                source_ms INTEGER,
                accepted INTEGER NOT NULL CHECK(accepted IN(0,1)),
                reason TEXT NOT NULL,
                latency_ms INTEGER,
                bid TEXT, ask TEXT, spread_pct TEXT, bid_depth_eur TEXT, ask_depth_eur TEXT,
                pair_hash TEXT,
                previous_hash TEXT NOT NULL, entry_hash TEXT NOT NULL,
                payload_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_books_checked ON books(checked_ms);
            CREATE TABLE IF NOT EXISTS candles(
                close_ms INTEGER PRIMARY KEY, payload_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL, acquired_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pair_checks(
                checked_ms INTEGER PRIMARY KEY, pair_hash TEXT NOT NULL,
                rules_json TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN(0,1))
            );
            CREATE TABLE IF NOT EXISTS faults(
                id INTEGER PRIMARY KEY AUTOINCREMENT, checked_ms INTEGER NOT NULL,
                kind TEXT NOT NULL, reason TEXT NOT NULL
            );
        """)

    def _begin(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")

    def _info(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM info WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def _set(self, key: str, val: str) -> None:
        self.db.execute(
            "INSERT INTO info(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key,val),
        )

    def _commit_or_rollback(self, callback):
        self._begin()
        try:
            result = callback()
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def acquire(self, now_ms: int) -> None:
        """Acquire or renew lease; prior owner can recover after 60 s of silence."""
        def txn():
            owner = self._info("owner")
            until = int(self._info("lease_until") or "0")
            if owner and owner != self.owner and until >= now_ms:
                raise RuntimeError("COLLECTOR_ALREADY_OWNED")
            self._set("owner", self.owner)
            self._set("lease_until", str(now_ms + LEASE_MS))
            if self._info("started_ms") is None:
                self._set("started_ms", str(now_ms))
            self._set("heartbeat_ms", str(now_ms))
        self._commit_or_rollback(txn)

    def _assert_owner(self, now_ms: int) -> None:
        if self._info("owner") != self.owner or int(self._info("lease_until") or "0") < now_ms:
            raise RuntimeError("COLLECTOR_LEASE_LOST")

    def _renew(self, now_ms: int) -> None:
        self._set("lease_until", str(now_ms + LEASE_MS))
        self._set("heartbeat_ms", str(now_ms))

    def record_pair(self, now_ms: int, rules: dict, *, active: bool) -> str:
        digest = sha(rules)
        def txn():
            self._assert_owner(now_ms)
            initial = self._info("initial_pair_hash")
            if initial is None:
                self._set("initial_pair_hash", digest)
            elif initial != digest:
                self._set("pair_drift", "true")
            self.db.execute("INSERT INTO pair_checks VALUES (?,?,?,?)",
                            (now_ms, digest, canonical(rules), int(active)))
            self._renew(now_ms)
        self._commit_or_rollback(txn)
        return digest

    def record_candles(self, now_ms: int, candles: list[dict]) -> None:
        """Closed candles immutable; conflicting update halts rather than silently rewriting."""
        def txn():
            self._assert_owner(now_ms)
            for candle in candles:
                stamp = candle["close_ts_ms"]
                digest = sha(candle)
                prior = self.db.execute("SELECT payload_hash FROM candles WHERE close_ms=?", (stamp,)).fetchone()
                if prior and prior[0] != digest:
                    raise ValueError("CANDLE_HISTORY_REVISION")
                self.db.execute(
                    "INSERT OR IGNORE INTO candles(close_ms,payload_hash,payload_json,acquired_ms) VALUES (?,?,?,?)",
                    (stamp, digest, canonical(candle), now_ms),
                )
            self._set("last_candle_refresh_ms", str(now_ms))
            self._renew(now_ms)
        self._commit_or_rollback(txn)

    def record_book(self, now_ms: int, source_ms: int | None,
                    payload: dict | None, summary: dict | None,
                    reason: str) -> None:
        """Persist valid AND rejected polls to measure availability without survivorship bias."""
        if shutil.disk_usage(self.path.parent).free < MIN_FREE_BYTES:
            raise RuntimeError("LOW_DISK_SPACE")
        accepted = not reason and summary is not None
        def txn():
            self._assert_owner(now_ms)
            recent = self.db.execute(
                "SELECT checked_ms,source_ms,entry_hash FROM books ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if recent and now_ms <= recent[0]:
                raise ValueError("NONMONOTONIC_CAPTURE_CLOCK")
            if accepted and recent and recent[1] is not None and source_ms <= recent[1]:
                raise ValueError("NONMONOTONIC_SOURCE_TIME")
            prev = recent[2] if recent else "0"*64
            sample = {
                "checked_ms": now_ms, "source_ms": source_ms,
                "accepted": bool(accepted), "reason": reason or "OK",
                "payload_sha256": sha(payload) if payload else None,
                "summary": summary,
            }
            entry_hash = sha({"previous_hash": prev, "sample": sample})
            self.db.execute("""INSERT INTO books(
                checked_ms,source_ms,accepted,reason,latency_ms,bid,ask,spread_pct,
                bid_depth_eur,ask_depth_eur,pair_hash,previous_hash,entry_hash,payload_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    now_ms, source_ms, int(accepted), reason or "OK",
                    summary.get("age_ms") if summary else None,
                    summary.get("best_bid_eur") if summary else None,
                    summary.get("best_ask_eur") if summary else None,
                    summary.get("spread_pct") if summary else None,
                    summary.get("bid_depth_eur") if summary else None,
                    summary.get("ask_depth_eur") if summary else None,
                    self._info("initial_pair_hash"), prev, entry_hash,
                    canonical(sample) if accepted else None,
                ))
            self._set("last_poll_ms", str(now_ms))
            if accepted:
                self._set("last_valid_ms", str(now_ms))
            self._renew(now_ms)
        self._commit_or_rollback(txn)

    def record_fault(self, now_ms: int, kind: str, reason: str) -> None:
        """All collection failures are durable and never count as valid books."""
        def txn():
            self._assert_owner(now_ms)
            self.db.execute("INSERT INTO faults(checked_ms,kind,reason) VALUES (?,?,?)",
                            (now_ms,kind,reason[:180]))
            self._renew(now_ms)
        self._commit_or_rollback(txn)

    def release(self, now_ms: int) -> None:
        def txn():
            if self._info("owner") == self.owner:
                self._set("lease_until", "0")
                self._set("owner", "")
                self._set("heartbeat_ms", str(now_ms))
        self._commit_or_rollback(txn)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "CollectorStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
