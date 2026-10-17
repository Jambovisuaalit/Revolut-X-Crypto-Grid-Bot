"""Durable once-only GET attempt counter for OFFLINE mocked pilot tests.

The budget reservation is committed BEFORE the (mock) transport call so
process death cannot erase an attempted request. Any new owner attempting to
reuse the database is rejected: crash recovery is a manual review, never
an automatic fresh allowance.

This version intentionally refuses any mode other than OFFLINE_MOCK_ONLY.
It does not contain a real-account activation path.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import threading
import uuid


class PilotLedgerError(RuntimeError):
    """Redacted accounting failure; fail closed before any network I/O."""


class DurablePilotLedger:
    def __init__(self, path: Path, *, max_attempts: int = 25,
                 mode: str = "OFFLINE_MOCK_ONLY") -> None:
        if mode != "OFFLINE_MOCK_ONLY":
            raise PilotLedgerError("REAL_ACCOUNT_LEDGER_NOT_APPROVED")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 25:
            raise PilotLedgerError("INVALID_PILOT_LEDGER_BUDGET")
        self.path = Path(path)
        if self.path.name.lower() in ("bot_state.db", "collector.sqlite"):
            raise PilotLedgerError("LIVE_DATABASE_NOT_ALLOWED")
        self._owner = str(uuid.uuid4())
        self._lock = threading.Lock()
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL atomically claims the on-disk name across processes; unlike
        # an exists() preflight, no foreign DB can be opened then modified.
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            raise PilotLedgerError("PILOT_LEDGER_ALREADY_EXISTS_MANUAL_REVIEW") from None
        try:
            os.close(fd)
        except BaseException:
            raise PilotLedgerError("PILOT_LEDGER_FILE_CREATE_FAILED") from None
        try:
            self._db = sqlite3.connect(
                str(self.path), isolation_level=None, check_same_thread=False,
                timeout=5,
            )
        except sqlite3.Error:
            raise PilotLedgerError("PILOT_LEDGER_OPEN_FAILED") from None
        try:
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute(
                "CREATE TABLE pilot_budget (id INTEGER PRIMARY KEY CHECK(id=1), "
                "owner TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL, "
                "attempts INTEGER NOT NULL, allowance INTEGER NOT NULL)"
            )
            self._db.execute(
                "INSERT INTO pilot_budget(id,owner,mode,state,attempts,allowance) "
                "VALUES (1,?,'OFFLINE_MOCK_ONLY','RUNNING',0,?)",
                (self._owner, max_attempts),
            )
        except BaseException:
            self._db.close()
            self._closed=True
            raise

    def reserve_before_get(self) -> int:
        """Atomic reservation, no refunds after timeout or abrupt process exit."""
        with self._lock:
            if self._closed:
                raise PilotLedgerError("PILOT_LEDGER_CLOSED")
            try:
                self._db.execute("BEGIN IMMEDIATE")
                row=self._db.execute(
                    "SELECT owner,mode,state,attempts,allowance "
                    "FROM pilot_budget WHERE id=1"
                ).fetchone()
                if (row is None or row[0]!=self._owner or
                    row[1]!="OFFLINE_MOCK_ONLY" or row[2]!="RUNNING"):
                    raise PilotLedgerError("PILOT_LEDGER_NOT_ACTIVE")
                attempts,allowance=row[3],row[4]
                if (type(attempts) is not int or type(allowance) is not int
                        or attempts < 0 or allowance > 25 or attempts >= allowance):
                    raise PilotLedgerError("PILOT_LEDGER_BUDGET_EXHAUSTED")
                self._db.execute(
                    "UPDATE pilot_budget SET attempts=attempts+1 WHERE id=1"
                )
                self._db.execute("COMMIT")
                return attempts+1
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def seal(self) -> None:
        """End session permanently; cannot rearm it by restarting Python."""
        with self._lock:
            if self._closed:
                return
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    "UPDATE pilot_budget SET state='SEALED' WHERE id=1 AND owner=?",
                    (self._owner,),
                )
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def attempts(self) -> int:
        with self._lock:
            if self._closed:
                raise PilotLedgerError("PILOT_LEDGER_CLOSED")
            row=self._db.execute("SELECT attempts FROM pilot_budget WHERE id=1").fetchone()
            if row is None:
                raise PilotLedgerError("PILOT_LEDGER_MISSING")
            return row[0]

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed=True

    def __enter__(self) -> "DurablePilotLedger":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
