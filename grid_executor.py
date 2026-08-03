"""Grid execution engine with SQLite state, reconciliation, and atomic replacements."""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from precision_engine import (
    PairRules,
    decimal_from_api,
    decimal_string,
    round_down,
    validate_order,
    validate_post_only,
)
from revolut_x_client import RevolutXClient, RevolutXError

LOGGER = logging.getLogger(__name__)

ZERO = Decimal("0")
ONE = Decimal("1")
ONE_HUNDRED = Decimal("100")
BPS_BASE = Decimal("10000")

LIVE_ACTIVE_STATES = {"new", "partially_filled"}
LOCAL_RECONCILE_STATES = {"new", "partially_filled", "pending_submit", "pending_replace"}
LOCAL_ACTIVE_STATES = LOCAL_RECONCILE_STATES | {"simulated"}


def _now_ms() -> int:
    """Return wall-clock epoch milliseconds without using float arithmetic."""
    return time.time_ns() // 1_000_000


def _require_decimal(value: object, *, field_name: str) -> Decimal:
    """Require a finite Decimal for all in-memory financial values."""
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal; got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _normalize_symbol(symbol: str) -> str:
    """Normalize API/display pair forms for identity comparisons."""
    return symbol.strip().upper().replace("-", "/")


@dataclass(frozen=True, slots=True)
class DesiredOrder:
    """One logical grid order target."""

    logical_id: str
    side: str
    price: Decimal

    def __post_init__(self) -> None:
        if not self.logical_id:
            raise ValueError("logical_id must not be empty")
        normalized_side = self.side.lower()
        if normalized_side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        price = _require_decimal(self.price, field_name="price")
        if price <= ZERO:
            raise ValueError("price must be positive")


class GridExecutor:
    """Stateful post-only grid execution engine with fail-closed recovery."""

    def __init__(
        self,
        client: RevolutXClient,
        *,
        db_path: str = "bot_state.db",
        dry_run: bool = True,
    ) -> None:
        self.client = client
        self.dry_run = dry_run
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def close(self) -> None:
        """Close SQLite state storage."""
        self._conn.close()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS grid_orders (
                    logical_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    client_order_id TEXT NOT NULL UNIQUE,
                    venue_order_id TEXT,
                    previous_client_order_id TEXT,
                    previous_venue_order_id TEXT,
                    price TEXT NOT NULL,
                    base_size TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            self._ensure_column("grid_orders", "previous_client_order_id", "TEXT")
            self._ensure_column("grid_orders", "previous_venue_order_id", "TEXT")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_grid_orders_symbol_state "
                "ON grid_orders(symbol, state)"
            )

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            str(row["name"])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def get_meta(self, key: str) -> str | None:
        """Read a persisted bot metadata value."""
        row = self._conn.execute(
            "SELECT value FROM bot_meta WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Persist a bot metadata value atomically."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO bot_meta(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    def _assert_execution_allowed(self) -> None:
        if self.get_meta("kill_switch_triggered") == "true":
            raise RevolutXError("kill-switch is persisted; execution is blocked")

    def get_or_set_initial_equity(self, current_equity: Decimal) -> Decimal:
        """Return persisted initial equity, creating it on first safe startup."""
        current_equity = _require_decimal(current_equity, field_name="current_equity")
        if current_equity <= ZERO:
            raise ValueError("initial equity must be positive")

        stored = self.get_meta("initial_equity")
        if stored is not None:
            initial = decimal_from_api(stored, field_name="initial_equity")
            if initial <= ZERO:
                raise RevolutXError("persisted initial equity is invalid")
            return initial

        self.set_meta("initial_equity", decimal_string(current_equity))
        return current_equity

    @staticmethod
    def _remote_venue_id(order: Mapping[str, Any]) -> str:
        venue_id = order.get("id") or order.get("venue_order_id")
        if not isinstance(venue_id, str) or not venue_id:
            raise RevolutXError("active order is missing venue id")
        return venue_id

    @staticmethod
    def _remote_state(order: Mapping[str, Any]) -> str:
        raw_state = order.get("status") or order.get("state")
        if not isinstance(raw_state, str):
            raise RevolutXError("active order is missing state")
        state = raw_state.lower()
        if state not in LIVE_ACTIVE_STATES:
            raise RevolutXError(f"unknown active order state: {state}")
        return state

    @staticmethod
    def _validate_remote_order(row: sqlite3.Row, order: Mapping[str, Any]) -> None:
        expected_symbol = _normalize_symbol(str(row["symbol"]))
        remote_symbol = order.get("symbol")
        if isinstance(remote_symbol, str) and _normalize_symbol(remote_symbol) != expected_symbol:
            raise RevolutXError(
                f"reconciliation symbol mismatch for logical_id={row['logical_id']}"
            )

        remote_side = order.get("side")
        if isinstance(remote_side, str) and remote_side.lower() != str(row["side"]).lower():
            raise RevolutXError(
                f"reconciliation side mismatch for logical_id={row['logical_id']}"
            )

        if "price" in order:
            remote_price = decimal_from_api(
                order["price"],
                field_name=f"remote price {row['logical_id']}",
            )
            local_price = decimal_from_api(
                str(row["price"]),
                field_name=f"local price {row['logical_id']}",
            )
            if remote_price != local_price:
                raise RevolutXError(
                    f"reconciliation price mismatch for logical_id={row['logical_id']}"
                )

        if "quantity" in order:
            remote_size = decimal_from_api(
                order["quantity"],
                field_name=f"remote quantity {row['logical_id']}",
            )
            local_size = decimal_from_api(
                str(row["base_size"]),
                field_name=f"local size {row['logical_id']}",
            )
            if remote_size != local_size:
                raise RevolutXError(
                    f"reconciliation quantity mismatch for logical_id={row['logical_id']}"
                )

    def reconcile(self, symbol: str) -> None:
        """Reconcile all local live/pending orders with active exchange orders."""
        remote_orders = self.client.get_active_orders([symbol])
        by_client: dict[str, Mapping[str, Any]] = {}
        for order in remote_orders:
            client_id = order.get("client_order_id")
            if not isinstance(client_id, str) or not client_id:
                raise RevolutXError("active order is missing client_order_id")
            if client_id in by_client:
                raise RevolutXError(f"duplicate active client_order_id: {client_id}")
            by_client[client_id] = order

        placeholders = ",".join("?" for _ in LOCAL_RECONCILE_STATES)
        rows = self._conn.execute(
            f"SELECT * FROM grid_orders WHERE symbol = ? AND state IN ({placeholders})",
            (symbol, *sorted(LOCAL_RECONCILE_STATES)),
        ).fetchall()

        now_ms = _now_ms()
        with self._conn:
            for row in rows:
                client_id = str(row["client_order_id"])
                remote = by_client.get(client_id)

                if remote is None:
                    if str(row["state"]) in {"pending_submit", "pending_replace"}:
                        previous_id = row["previous_client_order_id"]
                        if previous_id and str(previous_id) in by_client:
                            raise RevolutXError(
                                f"replacement outcome uncertain for logical_id={row['logical_id']}; "
                                "previous order remains active"
                            )
                        raise RevolutXError(
                            f"uncertain order state for logical_id={row['logical_id']}; "
                            "manual reconciliation required"
                        )

                    self._conn.execute(
                        """
                        UPDATE grid_orders
                        SET state='inactive', updated_at_ms=?
                        WHERE logical_id=?
                        """,
                        (now_ms, row["logical_id"]),
                    )
                    continue

                self._validate_remote_order(row, remote)
                venue_id = self._remote_venue_id(remote)
                state = self._remote_state(remote)
                self._conn.execute(
                    """
                    UPDATE grid_orders
                    SET venue_order_id=?,
                        previous_client_order_id=NULL,
                        previous_venue_order_id=NULL,
                        state=?,
                        updated_at_ms=?
                    WHERE logical_id=?
                    """,
                    (venue_id, state, now_ms, row["logical_id"]),
                )

    @staticmethod
    def build_grid(
        center: Decimal,
        levels_per_side: int,
        spacing_bps: Decimal,
        rules: PairRules,
    ) -> list[DesiredOrder]:
        """Build symmetric, exchange-rounded buy/sell levels around a center price."""
        center = _require_decimal(center, field_name="center")
        spacing_bps = _require_decimal(spacing_bps, field_name="spacing_bps")
        if center <= ZERO:
            raise ValueError("center must be positive")
        if levels_per_side < 1:
            raise ValueError("levels_per_side must be >= 1")
        if spacing_bps <= ZERO:
            raise ValueError("spacing_bps must be positive")

        orders: list[DesiredOrder] = []
        for level in range(1, levels_per_side + 1):
            distance = spacing_bps * Decimal(level) / BPS_BASE
            if distance >= ONE:
                raise ValueError("grid level distance must remain below 100%")
            buy_price = round_down(center * (ONE - distance), rules.quote_step)
            sell_price = round_down(center * (ONE + distance), rules.quote_step)
            prefix = _normalize_symbol(rules.symbol)
            orders.append(DesiredOrder(f"{prefix}:buy:{level}", "buy", buy_price))
            orders.append(DesiredOrder(f"{prefix}:sell:{level}", "sell", sell_price))
        return orders

    @staticmethod
    def _balances_by_currency(
        balances: list[Mapping[str, Any]],
    ) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for balance in balances:
            currency_raw = balance.get("currency")
            if not isinstance(currency_raw, str) or not currency_raw:
                raise RevolutXError("balance entry missing currency")
            currency = currency_raw.upper()
            if currency in result:
                raise RevolutXError(f"duplicate balance currency: {currency}")
            if "available" not in balance:
                raise RevolutXError(f"balance entry missing available for {currency}")
            available = decimal_from_api(
                balance["available"],
                field_name=f"{currency}.available",
            )
            if available < ZERO:
                raise RevolutXError(f"available balance is negative for {currency}")
            result[currency] = available
        return result

    @staticmethod
    def _strategy_fingerprint(
        symbol: str,
        levels_per_side: int,
        spacing_bps: Decimal,
    ) -> str:
        return (
            f"{_normalize_symbol(symbol)}|"
            f"{levels_per_side}|"
            f"{decimal_string(spacing_bps)}"
        )

    def _active_rows(self, symbol: str) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in LOCAL_ACTIVE_STATES)
        return self._conn.execute(
            f"SELECT * FROM grid_orders WHERE symbol = ? AND state IN ({placeholders})",
            (symbol, *sorted(LOCAL_ACTIVE_STATES)),
        ).fetchall()

    def maintain_grid(
        self,
        *,
        symbol: str,
        rules: PairRules,
        center: Decimal,
        best_bid: Decimal,
        best_ask: Decimal,
        levels_per_side: int,
        spacing_bps: Decimal,
        allocation_pct: Decimal,
        recenter_threshold_bps: Decimal,
    ) -> None:
        """Create or atomically recenter the persisted logical grid."""
        self._assert_execution_allowed()

        center = _require_decimal(center, field_name="center")
        best_bid = _require_decimal(best_bid, field_name="best_bid")
        best_ask = _require_decimal(best_ask, field_name="best_ask")
        spacing_bps = _require_decimal(spacing_bps, field_name="spacing_bps")
        allocation_pct = _require_decimal(allocation_pct, field_name="allocation_pct")
        recenter_threshold_bps = _require_decimal(
            recenter_threshold_bps,
            field_name="recenter_threshold_bps",
        )
        if recenter_threshold_bps <= ZERO:
            raise ValueError("recenter_threshold_bps must be positive")

        desired = self.build_grid(center, levels_per_side, spacing_bps, rules)
        expected_ids = {target.logical_id for target in desired}
        fingerprint = self._strategy_fingerprint(symbol, levels_per_side, spacing_bps)
        stored_fingerprint = self.get_meta("strategy_fingerprint")
        active_rows = self._active_rows(symbol)
        active_ids = {str(row["logical_id"]) for row in active_rows}

        if stored_fingerprint is not None and stored_fingerprint != fingerprint and active_rows:
            raise RevolutXError(
                "grid strategy parameters changed while active orders exist; "
                "cancel/reconcile before changing levels or spacing"
            )
        if active_ids - expected_ids:
            raise RevolutXError("unexpected active logical grid orders found in SQLite")

        previous_center_raw = self.get_meta("grid_center")
        previous_center = (
            decimal_from_api(previous_center_raw, field_name="grid_center")
            if previous_center_raw
            else None
        )
        if previous_center is not None:
            if previous_center <= ZERO:
                raise RevolutXError("persisted grid center is invalid")
            drift_bps = abs(center - previous_center) / previous_center * BPS_BASE
            if (
                stored_fingerprint == fingerprint
                and drift_bps < recenter_threshold_bps
                and active_ids == expected_ids
            ):
                return

        for target in desired:
            validate_post_only(target.side, target.price, best_bid, best_ask)

            balances = self._balances_by_currency(self.client.get_balances())
            available_base = balances.get(rules.base.upper(), ZERO)
            available_quote = balances.get(rules.quote.upper(), ZERO)
            fraction = allocation_pct / ONE_HUNDRED
            if target.side == "buy":
                raw_size = (available_quote * fraction) / target.price
            else:
                raw_size = available_base * fraction

            rounded_price, rounded_size = validate_order(
                rules=rules,
                side=target.side,
                price=target.price,
                base_size=raw_size,
                available_base=available_base,
                available_quote=available_quote,
                allocation_pct=allocation_pct,
            )

            existing = self._conn.execute(
                "SELECT * FROM grid_orders WHERE logical_id=?",
                (target.logical_id,),
            ).fetchone()
            if existing is not None and str(existing["state"]) in LOCAL_ACTIVE_STATES:
                existing_price = decimal_from_api(
                    str(existing["price"]),
                    field_name="persisted order price",
                )
                existing_size = decimal_from_api(
                    str(existing["base_size"]),
                    field_name="persisted order base_size",
                )
                if existing_price == rounded_price and existing_size == rounded_size:
                    continue
                self._replace(existing, rounded_price, rounded_size)
            else:
                self._place(target, symbol, rounded_price, rounded_size)

        self.set_meta("grid_center", decimal_string(center))
        self.set_meta("strategy_fingerprint", fingerprint)

    @staticmethod
    def _validate_mutation_response(
        response: Mapping[str, Any],
        *,
        expected_client_order_id: str,
    ) -> tuple[str, str]:
        client_id = response.get("client_order_id")
        if not isinstance(client_id, str) or client_id != expected_client_order_id:
            raise RevolutXError("order mutation response client_order_id mismatch")
        venue_id = response.get("venue_order_id")
        if not isinstance(venue_id, str) or not venue_id:
            raise RevolutXError("order mutation response missing venue_order_id")
        raw_state = response.get("state")
        if not isinstance(raw_state, str):
            raise RevolutXError("order mutation response missing state")
        state = raw_state.lower()
        if state not in LIVE_ACTIVE_STATES:
            raise RevolutXError(f"unexpected order mutation state: {state}")
        return venue_id, state

    def _place(
        self,
        target: DesiredOrder,
        symbol: str,
        price: Decimal,
        base_size: Decimal,
    ) -> None:
        self._assert_execution_allowed()
        price = _require_decimal(price, field_name="price")
        base_size = _require_decimal(base_size, field_name="base_size")

        client_order_id = str(uuid.uuid4())
        now_ms = _now_ms()
        state = "simulated" if self.dry_run else "pending_submit"
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO grid_orders(
                    logical_id,
                    symbol,
                    side,
                    client_order_id,
                    venue_order_id,
                    previous_client_order_id,
                    previous_venue_order_id,
                    price,
                    base_size,
                    state,
                    updated_at_ms
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(logical_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    side=excluded.side,
                    client_order_id=excluded.client_order_id,
                    venue_order_id=NULL,
                    previous_client_order_id=NULL,
                    previous_venue_order_id=NULL,
                    price=excluded.price,
                    base_size=excluded.base_size,
                    state=excluded.state,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    target.logical_id,
                    symbol,
                    target.side,
                    client_order_id,
                    None,
                    None,
                    None,
                    decimal_string(price),
                    decimal_string(base_size),
                    state,
                    now_ms,
                ),
            )

        if self.dry_run:
            LOGGER.info(
                "DRY RUN place %s %s @ %s size=%s",
                target.side,
                symbol,
                price,
                base_size,
            )
            return

        response = self.client.place_limit_order(
            client_order_id=client_order_id,
            symbol=symbol,
            side=target.side,
            base_size=base_size,
            price=price,
        )
        venue_id, remote_state = self._validate_mutation_response(
            response,
            expected_client_order_id=client_order_id,
        )
        with self._conn:
            self._conn.execute(
                """
                UPDATE grid_orders
                SET venue_order_id=?, state=?, updated_at_ms=?
                WHERE logical_id=?
                """,
                (venue_id, remote_state, _now_ms(), target.logical_id),
            )

    def _replace(
        self,
        existing: sqlite3.Row,
        price: Decimal,
        base_size: Decimal,
    ) -> None:
        self._assert_execution_allowed()
        price = _require_decimal(price, field_name="price")
        base_size = _require_decimal(base_size, field_name="base_size")

        if self.dry_run:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE grid_orders
                    SET price=?, base_size=?, state='simulated', updated_at_ms=?
                    WHERE logical_id=?
                    """,
                    (
                        decimal_string(price),
                        decimal_string(base_size),
                        _now_ms(),
                        existing["logical_id"],
                    ),
                )
            LOGGER.info(
                "DRY RUN replace %s @ %s size=%s",
                existing["logical_id"],
                price,
                base_size,
            )
            return

        venue_id = existing["venue_order_id"]
        if not isinstance(venue_id, str) or not venue_id:
            raise RevolutXError(
                f"cannot replace {existing['logical_id']} without venue_order_id"
            )

        old_client_order_id = str(existing["client_order_id"])
        new_client_order_id = str(uuid.uuid4())
        with self._conn:
            self._conn.execute(
                """
                UPDATE grid_orders
                SET previous_client_order_id=?,
                    previous_venue_order_id=?,
                    client_order_id=?,
                    price=?,
                    base_size=?,
                    state='pending_replace',
                    updated_at_ms=?
                WHERE logical_id=?
                """,
                (
                    old_client_order_id,
                    venue_id,
                    new_client_order_id,
                    decimal_string(price),
                    decimal_string(base_size),
                    _now_ms(),
                    existing["logical_id"],
                ),
            )

        response = self.client.replace_limit_order(
            venue_order_id=venue_id,
            client_order_id=new_client_order_id,
            base_size=base_size,
            price=price,
        )
        new_venue_id, remote_state = self._validate_mutation_response(
            response,
            expected_client_order_id=new_client_order_id,
        )
        with self._conn:
            self._conn.execute(
                """
                UPDATE grid_orders
                SET venue_order_id=?,
                    previous_client_order_id=NULL,
                    previous_venue_order_id=NULL,
                    state=?,
                    updated_at_ms=?
                WHERE logical_id=?
                """,
                (
                    new_venue_id,
                    remote_state,
                    _now_ms(),
                    existing["logical_id"],
                ),
            )

    def trigger_kill_switch(self) -> None:
        """Persist the kill switch before cancelling all live account orders."""
        triggered_at = str(_now_ms())
        self.set_meta("kill_switch_triggered", "true")
        self.set_meta("kill_switch_triggered_at_ms", triggered_at)
        with self._conn:
            self._conn.execute(
                """
                UPDATE grid_orders
                SET state='cancelled_by_kill_switch', updated_at_ms=?
                WHERE state IN (
                    'new',
                    'partially_filled',
                    'pending_submit',
                    'pending_replace',
                    'simulated'
                )
                """,
                (int(triggered_at),),
            )

        LOGGER.critical("Kill-switch triggered; all bot execution halted")
        if not self.dry_run:
            self.client.cancel_all_orders()
