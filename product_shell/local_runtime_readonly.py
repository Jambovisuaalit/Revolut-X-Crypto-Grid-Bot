"""Local SQLite observability for Grid Console; never connects to an exchange.

Read-only by SQLite mode=ro and PRAGMA query_only. Returns only conservative,
redacted operational summaries; no order IDs, balances, API keys, or file paths.
The trading engine's schema and journal remain owned by GridExecutor.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

VALID_STATES = frozenset({"STOPPED", "RECONCILING", "OFFLINE_MONITORING",
                          "BLOCKED", "RISK_LOCKED"})
VALID_ORDER_STATES = frozenset({
    "pending_new", "pending_replace", "venue_pending", "new",
    "partially_filled", "replace_uncertain", "filled",
    "cancelled", "rejected", "replaced",
})
UNCERTAIN = frozenset({"pending_new", "pending_replace", "replace_uncertain", "venue_pending"})
STATE_LABEL = {
    "STOPPED": "Pysäytetty",
    "RECONCILING": "Täsmäytys kesken",
    "OFFLINE_MONITORING": "Offline-valvonta (tallennettu tila)",
    "BLOCKED": "Estetty",
    "RISK_LOCKED": "Riskilukitus",
    "NOT_CONFIGURED": "Ei tietokantaa",
    "NOT_STARTED": "Ei käynnistetty",
}
REASONS = {
    "NONE": "Ei ilmoitettua eston syytä",
    "STARTUP": "Käynnistyksen täsmäytys",
    "OPERATOR_STOP": "Valvoja pysäytettiin",
    "SYNTHETIC_FIXTURE_ONLY": "Synteettinen offline-testi",
    "HARD_DRAWDOWN_3PCT": "Kolmen prosentin tappioraja",
    "PERSISTENT_RISK_LOCK": "Pysyvä riskilukitus",
    "STALE_PORTFOLIO_SAMPLE": "Vanhentunut saldohavainto",
    "NON_OFFLINE_SOURCE_FORBIDDEN": "Virheellinen havaintolähde",
    "INVALID_SAMPLE_OR_RUNTIME_FAILURE": "Virheellinen havainto tai ohjelmavirhe",
    "BACKWARDS_SAMPLE_TIMESTAMP": "Havainnon aikaleima palasi taaksepäin",
    "STARTUP_RECONCILIATION_FAILED": "Käynnistystäsmäytys epäonnistui",
    "INVALID_SAMPLE_CLOCK": "Virheellinen havainnon aikaleima",
    "SUPERVISOR_LEASE_LOST": "Valvojan omistajalukitus menetettiin",
}
STATUS_LABEL = {
    "pending_new": "Lähetys epävarma",
    "pending_replace": "Korvaus epävarma",
    "replace_uncertain": "Korvauksen tila epävarma",
    "venue_pending": "Pörssivahvistus puuttuu",
    "new": "Avoin (paikallinen merkintä)",
    "partially_filled": "Osittain toteutunut (paikallinen merkintä)",
    "filled": "Täytetty (paikallinen merkintä)",
    "cancelled": "Peruttu (paikallinen merkintä)",
    "rejected": "Hylätty (paikallinen merkintä)",
    "replaced": "Korvattu (paikallinen merkintä)",
}
MAX_EVENTS = 40


def _empty(status: str = "NOT_CONFIGURED", reason: str = "NONE") -> dict:
    return {
        "schema": "grid-console-local-runtime-v1",
        "provenance": "LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED",
        "status": status,
        "label": STATE_LABEL.get(status, "Tietoa ei voitu vahvistaa"),
        "reason": reason,
        "reason_label": REASONS.get(reason, "Tila vaatii tarkistuksen"),
        "risk_lock": "UNKNOWN",
        "cancellation": "NOT_VERIFIED",
        "heartbeat_age_ms": None,
        "heartbeat": "UNKNOWN",
        "uncertain_order_count": None,
        "order_event_count": 0,
        "fill_event_count": 0,
        "events": [],
        "history_note": "Näytetään vain paikallisen toimeksiantokirjanpidon uusimmat merkinnät; valvojan tilasiirtymistä ei vielä ole erillistä historiataulua.",
        "live_orders": "DISABLED",
        "account_connection": "NOT_CONNECTED",
        "reconciled_for_trading": False,
    }


def _integer(value: str | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.isdecimal() or len(value) > 18:
        raise ValueError("INVALID_LOCAL_TIMESTAMP")
    return int(value)


def _timestamp(value: str) -> str:
    # GridExecutor creates timestamp in SQLite UTC 'YYYY-MM-DD HH:MM:SS'.
    if not isinstance(value, str) or len(value) != 19:
        raise ValueError("INVALID_JOURNAL_TIMESTAMP")
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def inspect_local_runtime(db_path: Path | None, now_ms: int | None = None) -> dict:
    """Read a consistent local snapshot. Reject stale, malformed and unsafe data."""
    if db_path is None:
        return _empty()
    try:
        path = Path(db_path)
        if not path.is_file() or path.is_symlink():
            return _empty("BLOCKED", "DATABASE_UNAVAILABLE")
        now = now_ms if now_ms is not None else time.time_ns() // 1_000_000
        if type(now) is not int or now <= 0:
            return _empty("BLOCKED", "INVALID_CLOCK")
        # as_uri percent-escapes Windows paths, whitespace, # and ?.
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA busy_timeout=500")
            db.execute("BEGIN")
            names = _tables(db)
            if not {"bot_metadata", "order_lineage", "fills_wire"}.issubset(names):
                return _empty("BLOCKED", "UNRECOGNIZED_DATABASE_SCHEMA")
            metadata = {}
            for key, val in db.execute(
                "SELECT key,value FROM bot_metadata WHERE key IN "
                "('supervisor_state','supervisor_reason','supervisor_heartbeat_ms',"
                "'supervisor_lease_until_ms','kill_switch_triggered',"
                "'cancellation_verification_status')"
            ).fetchall():
                metadata[key] = val
            persisted = metadata.get("supervisor_state")
            if persisted is None:
                status = "NOT_STARTED"
            elif persisted in VALID_STATES:
                status = persisted
            else:
                raise ValueError("UNKNOWN_SUPERVISOR_STATE")
            flag = metadata.get("kill_switch_triggered")
            if flag not in (None, "true", "false"):
                raise ValueError("UNKNOWN_RISK_LOCK_FLAG")
            lock = "TRIGGERED" if flag == "true" else "NOT_TRIGGERED" if flag == "false" else "UNKNOWN"
            if flag == "true":
                status = "RISK_LOCKED"
            heartbeat_ms = _integer(metadata.get("supervisor_heartbeat_ms"))
            lease_ms = _integer(metadata.get("supervisor_lease_until_ms"))
            heartbeat_age = now - heartbeat_ms if heartbeat_ms is not None else None
            hb = "UNKNOWN"
            if heartbeat_age is not None:
                if heartbeat_age < -3000:
                    raise ValueError("CLOCK_REGRESSION")
                hb = "FRESH" if 0 <= heartbeat_age <= 15_000 else "STALE"
            if status in ("OFFLINE_MONITORING", "RECONCILING") and (
                lease_ms is None or lease_ms < now or hb != "FRESH"
            ):
                status = "BLOCKED"
                reason = "LEASE_EXPIRED_OR_HEARTBEAT_STALE"
            else:
                reason = metadata.get("supervisor_reason", "NONE")
            if not isinstance(reason, str) or len(reason) > 100:
                raise ValueError("INVALID_REASON")
            # Treat an unrecognized reason as a generic warning, never echo it.
            if reason not in REASONS and reason != "LEASE_EXPIRED_OR_HEARTBEAT_STALE":
                reason = "UNRECOGNIZED_REASON"
            cancel = metadata.get("cancellation_verification_status")
            if cancel not in (None, "VERIFIED_NO_ACTIVE_ORDERS", "CANCELLATION_UNVERIFIED"):
                cancel = "NOT_VERIFIED"
            if cancel != "VERIFIED_NO_ACTIVE_ORDERS":
                cancel = "NOT_VERIFIED"
            # Validate the ENTIRE local journal before showing even a capped
            # event subset. Unsafe old rows must not be hidden by a 40-row LIMIT.
            known_order_states = tuple(sorted(VALID_ORDER_STATES))
            placeholders = ",".join("?" for _ in known_order_states)
            unexpected = db.execute(
                "SELECT COUNT(*) FROM order_lineage WHERE status NOT IN (" +
                placeholders + ") OR side NOT IN ('buy','sell')",
                known_order_states,
            ).fetchone()[0]
            if unexpected:
                raise ValueError("UNKNOWN_LOCAL_ORDER_STATES")
            # Query local journal only; no venue IDs, quantities or prices leave here.
            journal = db.execute(
                "SELECT status,side,created_at FROM order_lineage "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?", (MAX_EVENTS,)
            ).fetchall()
            if any(row[0] not in VALID_ORDER_STATES or row[1] not in ("buy","sell") for row in journal):
                raise ValueError("UNKNOWN_ORDER_JOURNAL_STATE")
            uncertain_count = db.execute(
                "SELECT COUNT(*) FROM order_lineage WHERE status IN "
                "('pending_new','pending_replace','replace_uncertain','venue_pending')"
            ).fetchone()[0]
            fills = db.execute(
                "SELECT created_at FROM fills_wire "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?", (MAX_EVENTS,)
            ).fetchall()
            events = []
            for state, side, created_at in journal:
                events.append({
                    "time_utc": _timestamp(created_at),
                    "category": "ORDER_LOCAL",
                    "label": ("Osto" if side == "buy" else "Myynti") + " — nykyinen paikallinen tila: " + STATUS_LABEL[state],
                    "timestamp_kind": "ORDER_CREATED_AT",
                    "uncertain": state in UNCERTAIN,
                })
            for (created_at,) in fills:
                events.append({
                    "time_utc": _timestamp(created_at),
                    "category": "FILL_LOCAL",
                    "label": "Toteutus kirjattu paikallisesti — ei varmennettu pörssistä",
                    "timestamp_kind": "FILL_CREATED_AT",
                    "uncertain": False,
                })
            events.sort(key=lambda x: x["time_utc"], reverse=True)
            events = events[:MAX_EVENTS]
            if uncertain_count > 0 and status not in ("RISK_LOCKED", "BLOCKED"):
                status = "BLOCKED"
                reason = "UNCERTAIN_LOCAL_ORDERS"
            result = _empty(status, reason)
            result.update({
                "risk_lock": lock,
                "cancellation": cancel,
                "heartbeat_age_ms": heartbeat_age,
                "heartbeat": hb,
                "uncertain_order_count": uncertain_count,
                "order_event_count": len(journal),
                "fill_event_count": len(fills),
                "events": events,
            })
            # A local journal NEVER establishes actual exchange reconciliation.
            return result
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        return _empty("BLOCKED", "LOCAL_DATABASE_READ_FAILED")
