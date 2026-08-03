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

from precision_engine import PairRules, decimal_string, round_down, validate_order, validate_post_only
from revolut_x_client import RevolutXClient, RevolutXError

LOGGER = logging.getLogger(__name__)
ACTIVE_STATES = {"new", "partially_filled", "pending_submit", "pending_replace", "simulated"}


@dataclass(frozen=True, slots=True)
class DesiredOrder:
    """One logical grid order target."""

    logical_id: str
    side: str
    price: Decimal


class GridExecutor:
    """Stateful post-only grid execution engine."""

    def __init__(self, client: RevolutXClient, *, db_path: str = "bot_state.db", dry_run: bool = True) -> None:
        self.client = client
        self.dry_run = dry_run
        self.db_path = Path(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
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
                    price TEXT NOT NULL,
                    base_size TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )

    def get_meta(self, key: str) -> str | None:
        """Read a persisted bot metadata value."""
        row = self._conn.execute("SELECT value FROM bot_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Persist a bot metadata value atomically."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO bot_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_or_set_initial_equity(self, current_equity: Decimal) -> Decimal:
        """Return persisted initial equity, creating it on first safe startup."""
        stored = self.get_meta("initial_equity")
        if stored is not None:
            return Decimal(stored)
        if current_equity <= Decimal("0"):
            raise ValueError("initial equity must be positive")
        self.set_meta("initial_equity", decimal_string(current_equity))
        return current_equity

    def reconcile(self, symbol: str) -> None:
        """Reconcile local bot orders with currently active exchange orders."""
        if self.dry_run:
            return
        remote = self.client.get_active_orders([symbol])
        by_client: dict[str, Mapping[str, Any]] = {}
        for order in remote:
            client_id = order.get("client_order_id")
            if isinstance(client_id, str):
                by_client[client_id] = order

        rows = self._conn.execute(
            "SELECT * FROM grid_orders WHERE symbol = ? AND state IN ('new','partially_filled','pending_submit','pending_replace')",
            (symbol,),
        ).fetchall()
        now_ms = int(time.time() * 1000)
        with self._conn:
            for row in rows:
                client_id = str(row["client_order_id"])
                remote_order = by_client.get(client_id)
                if remote_order is None:
                    if row["state"] in {"pending_submit", "pending_replace"}:
                        raise RevolutXError(
                            f"uncertain order state for logical_id={row['logical_id']}; manual reconciliation required"
                        )
                    self._conn.execute(
                        "UPDATE grid_orders SET state='inactive', updated_at_ms=? WHERE logical_id=?",
                        (now_ms, row["logical_id"]),
                    )
                    continue
                venue_id = remote_order.get("venue_order_id")
                state = str(remote_order.get("state") or remote_order.get("status") or "new").lower()
                self._conn.execute(
                    "UPDATE grid_orders SET venue_order_id=?, state=?, updated_at_ms=? WHERE logical_id=?",
                    (str(venue_id) if venue_id else row["venue_order_id"], state, now_ms, row["logical_id"]),
                )

    @staticmethod
    def build_grid(center: Decimal, levels_per_side: int, spacing_bps: Decimal, rules: PairRules) -> list[DesiredOrder]:
        """Build symmetric buy/sell levels around a center price."""
        if center <= Decimal("0"):
            raise ValueError("center must be positive")
        if levels_per_side < 1:
            raise ValueError("levels_per_side must be >= 1")
        if spacing_bps <= Decimal("0"):
            raise ValueError("spacing_bps must be positive")
        orders: list[DesiredOrder] = []
        bps = Decimal("10000")
        for level in range(1, levels_per_side + 1):
            distance = spacing_bps * Decimal(level) / bps
            buy_price = round_down(center * (Decimal("1") - distance), rules.quote_step)
            sell_price = round_down(center * (Decimal("1") + distance), rules.quote_step)
            orders.append(DesiredOrder(f"buy:{level}", "buy", buy_price))
            orders.append(DesiredOrder(f"sell:{level}", "sell", sell_price))
        return orders

    @staticmethod
    def _balances_by_currency(balances: list[Mapping[str, Any]]) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for balance in balances:
            currency = str(balance.get("currency", ""))
            if not currency:
                continue
            result[currency] = Decimal(str(balance.get("available", "0")))
        return result

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
        previous_center_raw = self.get_meta("grid_center")
        previous_center = Decimal(previous_center_raw) if previous_center_raw else None
        if previous_center is not None:
            drift_bps = abs(center - previous_center) / previous_center * Decimal("10000")
            active_count = self._conn.execute(
                "SELECT COUNT(*) AS c FROM grid_orders WHERE symbol=? AND state IN ('new','partially_filled','simulated')",
                (symbol,),
            ).fetchone()["c"]
            expected = levels_per_side * 2
            if drift_bps < recenter_threshold_bps and int(active_count) >= expected:
                return

        desired = self.build_grid(center, levels_per_side, spacing_bps, rules)
        for target in desired:
            validate_post_only(target.side, target.price, best_bid, best_ask)
            balances = self._balances_by_currency(self.client.get_balances())
            available_base = balances.get(rules.base, Decimal("0"))
            available_quote = balances.get(rules.quote, Decimal("0"))
            fraction = allocation_pct / Decimal("100")
            if target.side == "buy":
                raw_size = (available_quote * fraction) / target.price
            else:
                raw_size = available_base * fraction

            existing = self._conn.execute("SELECT * FROM grid_orders WHERE logical_id=?", (target.logical_id,)).fetchone()
            effective_base = available_base
            effective_quote = available_quote
            if existing is not None and str(existing["state"]) in ACTIVE_STATES:
                old_size = Decimal(str(existing["base_size"]))
                old_price = Decimal(str(existing["price"]))
                if target.side == "buy":
                    effective_quote += old_size * old_price
                else:
                    effective_base += old_size

            rounded_price, rounded_size = validate_order(
                rules=rules,
                side=target.side,
                price=target.price,
                base_size=raw_size,
                available_base=effective_base,
                available_quote=effective_quote,
                allocation_pct=allocation_pct,
            )

            if existing is not None and str(existing["state"]) in ACTIVE_STATES:
                if Decimal(str(existing["price"])) == rounded_price and Decimal(str(existing["base_size"])) == rounded_size:
                    continue
                self._replace(existing, rounded_price, rounded_size)
            else:
                self._place(target, symbol, rounded_price, rounded_size)

        self.set_meta("grid_center", decimal_string(center))

    def _place(self, target: DesiredOrder, symbol: str, price: Decimal, base_size: Decimal) -> None:
        client_order_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        state = "simulated" if self.dry_run else "pending_submit"
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO grid_orders(logical_id,symbol,side,client_order_id,venue_order_id,price,base_size,state,updated_at_ms)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(logical_id) DO UPDATE SET
                    symbol=excluded.symbol, side=excluded.side, client_order_id=excluded.client_order_id,
                    venue_order_id=NULL, price=excluded.price, base_size=excluded.base_size,
                    state=excluded.state, updated_at_ms=excluded.updated_at_ms
                """,
                (
                    target.logical_id,
                    symbol,
                    target.side,
                    client_order_id,
                    None,
                    decimal_string(price),
                    decimal_string(base_size),
                    state,
                    now_ms,
                ),
            )
        if self.dry_run:
            LOGGER.info("DRY RUN place %s %s @ %s size=%s", target.side, symbol, price, base_size)
            return
        response = self.client.place_limit_order(
            client_order_id=client_order_id,
            symbol=symbol,
            side=target.side,
            base_size=decimal_string(base_size),
            price=decimal_string(price),
        )
        venue_id = response.get("venue_order_id")
        if not venue_id:
            raise RevolutXError("place response missing venue_order_id")
        remote_state = str(response.get("state") or "new").lower()
        with self._conn:
            self._conn.execute(
                "UPDATE grid_orders SET venue_order_id=?, state=?, updated_at_ms=? WHERE logical_id=?",
                (str(venue_id), remote_state, int(time.time() * 1000), target.logical_id),
            )

    def _replace(self, existing: sqlite3.Row, price: Decimal, base_size: Decimal) -> None:
        venue_id = existing["venue_order_id"]
        if self.dry_run:
            with self._conn:
                self._conn.execute(
                    "UPDATE grid_orders SET price=?, base_size=?, state='simulated', updated_at_ms=? WHERE logical_id=?",
                    (decimal_string(price), decimal_string(base_size), int(time.time() * 1000), existing["logical_id"]),
                )
            LOGGER.info("DRY RUN replace %s @ %s size=%s", existing["logical_id"], price, base_size)
            return
        if not venue_id:
            raise RevolutXError(f"cannot replace {existing['logical_id']} without venue_order_id")
        new_client_order_id = str(uuid.uuid4())
        with self._conn:
            self._conn.execute(
                "UPDATE grid_orders SET client_order_id=?, price=?, base_size=?, state='pending_replace', updated_at_ms=? WHERE logical_id=?",
                (
                    new_client_order_id,
                    decimal_string(price),
                    decimal_string(base_size),
                    int(time.time() * 1000),
                    existing["logical_id"],
                ),
            )
        response = self.client.replace_limit_order(
            venue_order_id=str(venue_id),
            client_order_id=new_client_order_id,
            base_size=decimal_string(base_size),
            price=decimal_string(price),
        )
        new_venue_id = response.get("venue_order_id")
        if not new_venue_id:
            raise RevolutXError("replace response missing venue_order_id")
        remote_state = str(response.get("state") or "new").lower()
        with self._conn:
            self._conn.execute(
                "UPDATE grid_orders SET venue_order_id=?, state=?, updated_at_ms=? WHERE logical_id=?",
                (str(new_venue_id), remote_state, int(time.time() * 1000), existing["logical_id"]),
            )

    def trigger_kill_switch(self) -> None:
        """Persist the kill switch, cancel all orders, and permanently block execution."""
        self.set_meta("kill_switch_triggered", "true")
        with self._conn:
            self._conn.execute(
                "UPDATE grid_orders SET state='cancelled_by_kill_switch', updated_at_ms=? WHERE state IN ('new','partially_filled','pending_submit','pending_replace','simulated')",
                (int(time.time() * 1000),),
            )
        LOGGER.critical("Kill-switch triggered; all bot execution halted")
        if not self.dry_run:
            self.client.cancel_all_orders()
