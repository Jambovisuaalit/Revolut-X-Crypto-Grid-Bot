"""Strict offline, read-only projection of the ETH/EUR shadow engine journal.

Never imports an exchange client, runs a strategy, repairs a database, or
represents synthetic prices/fills as actual Revolut X account information.
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote
import json
import sqlite3

from eth_shadow_v1.config import Config
from eth_shadow_v1.journal import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "eth_eur_grid_shadow_v1.toml"
SCHEMA = "grid-console-shadow-readonly-v1"
MAX_ROWS = 50000
MAX_EVENTS = 40
MAX_HISTORY = 300
D = Decimal


def _empty(status: str, reason: str) -> dict:
    return {
        "schema": SCHEMA, "status": status, "reason": reason,
        "mode": "SHADOW_ONLY", "pair": "ETH-EUR", "account_connection": "NOT_CONNECTED",
        "live_orders": "DISABLED", "reconciled_for_trading": False,
        "source": None, "run_id": None, "config_hash": None,
        "journal_integrity": "NOT_VERIFIED", "observations": 0,
        "initial_equity_eur": None, "equity_eur": None, "cash_eur": None,
        "eth_qty": None, "realized_pnl_eur": None, "unrealized_pnl_eur": None,
        "total_pnl_eur": None, "fees_eur": None, "max_drawdown_eur": None,
        "risk_state": "UNKNOWN", "hard_stop_triggered": None,
        "cost_basis_eur": None, "last_source_ms": None,
        "positions": [], "fills": [], "equity_history": [],
    }


def _decimal(value: object, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("INVALID_SHADOW_DECIMAL")
    try:
        parsed = D(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("INVALID_SHADOW_DECIMAL") from exc
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise ValueError("INVALID_SHADOW_DECIMAL")
    return parsed


def _text(value: object, maximum: int = 100) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("INVALID_SHADOW_TEXT")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("INVALID_SHADOW_TEXT")
    return value


def _as_str(value: Decimal) -> str:
    return format(value, "f")


def _project(db: sqlite3.Connection, cfg: Config) -> dict:
    db.execute("PRAGMA query_only=ON")
    metadata = dict(db.execute("SELECT key,value FROM metadata"))
    if (metadata.get("mode") != "shadow" or metadata.get("config_hash") != cfg.sha256
            or not metadata.get("run_id") or not metadata.get("pair_source")):
        raise ValueError("SHADOW_METADATA_MISMATCH")
    run_id = _text(metadata["run_id"])
    source = _text(metadata["pair_source"])
    if source not in ("synthetic_fixture", "public_capture_replay"):
        # Unrecognized source must never be presented as trusted data.
        raise ValueError("SHADOW_SOURCE_NOT_VERIFIED")
    rows = db.execute(
        "SELECT event_id,previous_hash,entry_hash,decision_json,state_json "
        "FROM decisions ORDER BY seq LIMIT ?", (MAX_ROWS + 1,)
    ).fetchall()
    if len(rows) > MAX_ROWS:
        raise ValueError("SHADOW_JOURNAL_TOO_LARGE")
    if not rows:
        return {**_empty("NO_DATA", "EMPTY_JOURNAL"),
                "source": "SYNTHETIC_FIXTURE_ONLY" if source == "synthetic_fixture" else "PUBLIC_REPLAY_UNVERIFIED",
                "run_id": run_id, "config_hash": cfg.sha256,
                "journal_integrity": "PASS"}
    previous = "0" * 64
    history = []
    recent_fills = []
    fill_ids = set()
    expected_fills = {}
    fill_count = 0
    fees_from_fills = D("0")
    last_state = None
    last_decision = None
    for event_id, prior_hash, entry_hash, decision_json, state_json in rows:
        decision = json.loads(decision_json)
        state = json.loads(state_json)
        signed = dict(decision)
        signed.pop("entry_hash", None)
        if (decision["event_id"] != event_id or prior_hash != previous or
                decision["previous_hash"] != previous or decision["entry_hash"] != entry_hash or
                digest(signed) != entry_hash or digest(state) != decision["state_hash"] or
                decision.get("mode") != "shadow" or state.get("mode") != "shadow" or
                state.get("config_hash") != cfg.sha256 or decision.get("config_hash") != cfg.sha256):
            raise ValueError("SHADOW_AUDIT_CHAIN_INVALID")
        previous = entry_hash
        cash = _decimal(state["cash"], nonnegative=True)
        eth = _decimal(state["eth"], nonnegative=True)
        equity = _decimal(state["last_equity"], nonnegative=True)
        if _decimal(decision["cash_eur"]) != cash or _decimal(decision["equity_eur"]) != equity or _decimal(decision["eth_balance"]) != eth:
            raise ValueError("SHADOW_STATE_DECISION_MISMATCH")
        if abs(equity - cash - eth * (_decimal(state["last_bid"]) if state.get("last_bid") is not None else D("0"))) > D("100"):
            raise ValueError("SHADOW_EQUITY_INCONSISTENT")
        stamp = decision.get("source_ts_utc_ms")
        if stamp is not None and (type(stamp) is not int or stamp < 0):
            raise ValueError("SHADOW_TIMESTAMP_INVALID")
        history.append({"source_ms": stamp, "equity_eur": _as_str(equity)})
        fills = decision.get("fills")
        if not isinstance(fills, list) or len(fills) > 16:
            raise ValueError("SHADOW_FILLS_INVALID")
        for fill in fills:
            if fill.get("synthetic") is not True or fill.get("event_id") != event_id:
                raise ValueError("SHADOW_NON_SYNTHETIC_FILL")
            fill_id = _text(fill["fill_id"], 180)
            if fill_id in fill_ids:
                raise ValueError("SHADOW_DUPLICATE_FILL")
            fill_ids.add(fill_id)
            expected_fills[fill_id] = digest(fill)
            side = fill["side"]
            if side not in ("BUY", "SELL", "SELL_HARD_STOP"):
                raise ValueError("SHADOW_FILL_SIDE_INVALID")
            fee = _decimal(fill["fee_eur"], nonnegative=True)
            qty = _decimal(fill["quantity"], nonnegative=True)
            price = _decimal(fill["fill_price"], nonnegative=True)
            change = _decimal(fill["cash_change_eur"])
            if qty <= 0 or price <= 0 or (side == "BUY" and change >= 0) or (side != "BUY" and change <= 0):
                raise ValueError("SHADOW_FILL_AMOUNT_INVALID")
            fees_from_fills += fee
            fill_count += 1
            recent_fills.append({
                "time_ms": stamp, "side": side, "quantity": _as_str(qty),
                "price_eur": _as_str(price), "fee_eur": _as_str(fee),
                "lot_id": _text(fill["lot_id"], 32),
                "cycle_id": _text(fill["cycle_id"], 50),
                "synthetic": True,
            })
        last_state, last_decision = state, decision
    assert last_state is not None and last_decision is not None
    indexed = db.execute("SELECT fill_id,event_id,details_json FROM synthetic_fills").fetchall()
    if len(indexed) != fill_count:
        raise ValueError("SHADOW_FILL_INDEX_MISMATCH")
    for fill_id, event_id, details_json in indexed:
        if fill_id not in fill_ids:
            raise ValueError("SHADOW_FILL_INDEX_MISMATCH")
        details = json.loads(details_json)
        if (details.get("fill_id") != fill_id or details.get("event_id") != event_id or
                details.get("synthetic") is not True or digest(details) != expected_fills[fill_id]):
            raise ValueError("SHADOW_FILL_INDEX_MISMATCH")
    if _decimal(last_state["fees"], nonnegative=True) != fees_from_fills:
        raise ValueError("SHADOW_FEES_MISMATCH")
    cash = _decimal(last_state["cash"], nonnegative=True)
    eth = _decimal(last_state["eth"], nonnegative=True)
    equity = _decimal(last_state["last_equity"], nonnegative=True)
    realized = _decimal(last_state["realized_pnl"])
    positions = []
    cost_basis = D("0")
    owned = D("0")
    for index, lot in sorted(last_state["lots"].items(), key=lambda x:int(x[0])):
        if lot["status"] != "OPEN_LOT":
            continue
        qty = _decimal(lot["qty"], nonnegative=True)
        cost = _decimal(lot["cost"], nonnegative=True)
        owned += qty
        cost_basis += cost
        positions.append({"level": int(index), "quantity": _as_str(qty),
                          "cost_basis_eur": _as_str(cost), "synthetic": True})
    if owned != eth or cost_basis > cfg["max_cost_basis_eur"]:
        raise ValueError("SHADOW_POSITION_MISMATCH")
    total_pnl = equity - cfg["initial_cash_eur"]
    unrealized = equity - cash - cost_basis
    if abs(total_pnl - realized - unrealized) > D("0.02"):
        raise ValueError("SHADOW_PNL_RECONCILIATION_FAILED")
    risk = _text(last_state["status"], 40)
    if risk not in ("WAITING_FOR_DATA", "READY", "RUNNING", "PAUSED_ENTRIES", "DATA_HALT", "STOPPED"):
        raise ValueError("SHADOW_RISK_INVALID")
    return {
        **_empty("READY", "NONE"), "status": "READY",
        "source": "SYNTHETIC_FIXTURE_ONLY" if source == "synthetic_fixture" else "PUBLIC_REPLAY_UNVERIFIED",
        "run_id": run_id, "config_hash": cfg.sha256, "journal_integrity": "PASS",
        "observations": len(rows), "initial_equity_eur": _as_str(cfg["initial_cash_eur"]),
        "equity_eur": _as_str(equity), "cash_eur": _as_str(cash),
        "eth_qty": _as_str(eth), "realized_pnl_eur": _as_str(realized),
        "unrealized_pnl_eur": _as_str(unrealized), "total_pnl_eur": _as_str(total_pnl),
        "fees_eur": _as_str(fees_from_fills), "max_drawdown_eur": _as_str(_decimal(last_state["max_dd"],nonnegative=True)),
        "risk_state": risk, "hard_stop_triggered": risk == "STOPPED",
        "cost_basis_eur": _as_str(cost_basis), "last_source_ms": last_decision.get("source_ts_utc_ms"),
        "positions": positions, "fills": list(reversed(recent_fills[-MAX_EVENTS:])),
        "equity_history": history[-MAX_HISTORY:],
    }


def inspect_shadow_journal(path: Path | None) -> dict:
    """Return redacted, verified shadow values, or fail closed with no financial values."""
    if path is None:
        return _empty("NOT_CONFIGURED", "NO_SHADOW_JOURNAL_SELECTED")
    try:
        path = Path(path)
        if not path.is_file() or path.is_symlink():
            return _empty("BLOCKED", "SHADOW_JOURNAL_UNAVAILABLE")
        # Read only; do not silently create or migrate the database.
        uri = "file:" + quote(str(path.resolve()).replace("\\", "/"), safe="/:") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            cfg = Config.load(CONFIG_PATH)
            return _project(db, cfg)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, IndexError, ArithmeticError, AssertionError):
        return _empty("BLOCKED", "SHADOW_JOURNAL_INVALID_OR_UNVERIFIED")
