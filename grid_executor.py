"""Persistent, idempotent grid-order execution and reconciliation."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from precision_engine import PairRules, PrecisionError, SymbolNormalizer, decimal_from_api, decimal_text
from revolut_x_client import ApiError, RevolutXClient


class ReconciliationError(RuntimeError):
    """Raised when local state and exchange state cannot safely be reconciled."""


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """Normalized limit order request."""

    client_order_id: str
    symbol: str
    side: str
    price: Decimal
    quantity: Decimal


class GridExecutor:
    """Coordinates pair validation, reservations, lineage, and API mutations."""

    ACTIVE_STATUSES = frozenset({"pending_new", "new", "partially_filled"})
    KNOWN_STATUSES = ACTIVE_STATUSES | frozenset({"filled", "cancelled", "rejected", "replaced"})

    def __init__(
        self,
        client: RevolutXClient,
        pair_rules: Mapping[str, PairRules],
        *,
        database_path: str = "bot_state.db",
        live_trading: bool = False,
    ) -> None:
        """Open persistent state; live mutations are disabled unless opted in."""
        self.client = client
        self.pair_rules = {SymbolNormalizer.to_display_symbol(key): value for key, value in pair_rules.items()}
        self.live_trading = live_trading
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(Path(database_path), isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()
        self.reconciled = False

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS order_lineage (
                client_order_id TEXT PRIMARY KEY,
                venue_order_id TEXT UNIQUE,
                parent_venue_order_id TEXT NULL,
                root_client_order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
                price TEXT NOT NULL,
                quantity TEXT NOT NULL,
                filled_quantity TEXT NOT NULL DEFAULT '0',
                leaves_quantity TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS fills (
                fill_id TEXT PRIMARY KEY,
                venue_order_id TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                fee TEXT NOT NULL,
                fee_currency TEXT NOT NULL,
                is_maker INTEGER NOT NULL CHECK(is_maker IN (0, 1)),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS bot_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    @staticmethod
    def _payload(order: OrderRequest) -> dict[str, Any]:
        return {
            "client_order_id": order.client_order_id,
            "symbol": SymbolNormalizer.to_api_symbol(order.symbol),
            "side": order.side,
            "order_configuration": {
                "limit": {
                    "base_size": decimal_text(order.quantity),
                    "price": decimal_text(order.price),
                    "time_in_force": "gtc",
                    "execution_instructions": ["post_only"],
                }
            },
        }

    def _validated_order(
        self, client_order_id: str, symbol: str, side: str, price: Decimal, quantity: Decimal
    ) -> OrderRequest:
        normalized_symbol = SymbolNormalizer.to_display_symbol(symbol)
        normalized_side = side.lower()
        if normalized_side not in {"buy", "sell"}:
            raise PrecisionError("side must be buy or sell")
        rules = self.pair_rules.get(normalized_symbol)
        if rules is None:
            raise PrecisionError(f"no pair rules loaded for {normalized_symbol}")
        rounded_price, rounded_quantity = rules.normalize(price, quantity)
        return OrderRequest(client_order_id, normalized_symbol, normalized_side, rounded_price, rounded_quantity)

    def _reserved(self, currency: str) -> Decimal:
        total = Decimal("0")
        rows = self.connection.execute(
            "SELECT symbol, side, price, leaves_quantity FROM order_lineage WHERE status IN ('pending_new','new','partially_filled')"
        )
        for row in rows:
            base, quote = str(row["symbol"]).split("/", 1)
            leaves = decimal_from_api(row["leaves_quantity"], "leaves_quantity")
            if row["side"] == "buy" and quote == currency:
                total += leaves * decimal_from_api(row["price"], "price")
            elif row["side"] == "sell" and base == currency:
                total += leaves
        return total

    def _assert_funding(self, order: OrderRequest, free_balances: Mapping[str, Decimal]) -> None:
        base, quote = order.symbol.split("/", 1)
        currency = quote if order.side == "buy" else base
        required = order.price * order.quantity if order.side == "buy" else order.quantity
        if currency not in free_balances:
            raise PrecisionError(f"missing free balance for {currency}")
        free = free_balances[currency]
        if free < 0:
            raise PrecisionError("free balance cannot be negative")
        if required > free * Decimal("0.05"):
            raise PrecisionError("single order exceeds 5% of available free balance")
        if required + self._reserved(currency) > free:
            raise PrecisionError("aggregate local reservation exceeds free balance")

    @staticmethod
    def _venue_id(response: Any) -> str:
        if not isinstance(response, Mapping):
            raise ApiError("order response must be an object")
        venue_id = response.get("order_id", response.get("venue_order_id"))
        if not isinstance(venue_id, str) or not venue_id:
            raise ApiError("order response is missing venue order id")
        return venue_id

    def create_order(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        price: Decimal,
        quantity: Decimal,
        free_balances: Mapping[str, Decimal],
    ) -> Mapping[str, Any]:
        """Persist then place one idempotent post-only limit order."""
        if not self.reconciled:
            raise ReconciliationError("startup reconciliation has not completed")
        order = self._validated_order(client_order_id, symbol, side, price, quantity)
        payload = self._payload(order)
        with self._lock:
            if not self.live_trading:
                self._assert_funding(order, free_balances)
                return {"dry_run": True, "payload": payload}
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self.connection.execute(
                    "SELECT venue_order_id, status FROM order_lineage WHERE client_order_id=?", (client_order_id,)
                ).fetchone()
                if existing is not None:
                    self.connection.execute("COMMIT")
                    return {"client_order_id": client_order_id, "venue_order_id": existing[0], "status": existing[1]}
                self._assert_funding(order, free_balances)
                self.connection.execute(
                    """INSERT INTO order_lineage
                       (client_order_id, root_client_order_id, symbol, side, price, quantity, leaves_quantity, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_new')""",
                    (client_order_id, client_order_id, order.symbol, order.side, decimal_text(order.price),
                     decimal_text(order.quantity), decimal_text(order.quantity)),
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
            response = self.client.place_order(payload)
            venue_id = self._venue_id(response)
            self.connection.execute(
                "UPDATE order_lineage SET venue_order_id=?, status='new', updated_at=CURRENT_TIMESTAMP WHERE client_order_id=?",
                (venue_id, client_order_id),
            )
            return response

    def replace_order(
        self,
        venue_order_id: str,
        *,
        new_price: Decimal,
        free_balances: Mapping[str, Decimal],
        client_order_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Replace using remaining quantity and append a lineage generation."""
        if not self.reconciled:
            raise ReconciliationError("startup reconciliation has not completed")
        with self._lock:
            parent = self.connection.execute(
                "SELECT * FROM order_lineage WHERE venue_order_id=?", (venue_order_id,)
            ).fetchone()
            if parent is None or parent["status"] not in {"new", "partially_filled"}:
                raise ReconciliationError("only a known active order can be replaced")
            child_id = client_order_id or str(uuid.uuid4())
            leaves = decimal_from_api(parent["leaves_quantity"], "leaves_quantity")
            order = self._validated_order(child_id, parent["symbol"], parent["side"], new_price, leaves)
            payload = self._payload(order)
            if not self.live_trading:
                return {"dry_run": True, "payload": payload}
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                if self.connection.execute(
                    "SELECT 1 FROM order_lineage WHERE client_order_id=?", (child_id,)
                ).fetchone():
                    self.connection.execute("COMMIT")
                    return {"client_order_id": child_id, "status": "already_recorded"}
                # Exclude the parent reservation because PUT replaces rather than adds to it.
                self.connection.execute("UPDATE order_lineage SET status='replaced' WHERE venue_order_id=?", (venue_order_id,))
                self._assert_funding(order, free_balances)
                self.connection.execute(
                    """INSERT INTO order_lineage
                       (client_order_id,parent_venue_order_id,root_client_order_id,symbol,side,price,quantity,
                        filled_quantity,leaves_quantity,status,version)
                       VALUES (?,?,?,?,?,?,?,'0',?,'pending_new',?)""",
                    (child_id, venue_order_id, parent["root_client_order_id"], order.symbol, order.side,
                     decimal_text(order.price), decimal_text(order.quantity), decimal_text(order.quantity), parent["version"] + 1),
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
            try:
                response = self.client.replace_order(venue_order_id, payload)
                new_venue_id = self._venue_id(response)
            except Exception:
                # The parent remains active locally unless an acknowledgement proves replacement.
                self.connection.execute("UPDATE order_lineage SET status=? WHERE venue_order_id=?", (parent["status"], venue_order_id))
                raise
            self.connection.execute(
                "UPDATE order_lineage SET venue_order_id=?, status='new', updated_at=CURRENT_TIMESTAMP WHERE client_order_id=?",
                (new_venue_id, child_id),
            )
            return response

    def reconcile_open_orders(self) -> None:
        """Require an exact match between venue open orders and non-pending local orders."""
        if self.connection.execute("SELECT 1 FROM order_lineage WHERE status='pending_new' LIMIT 1").fetchone():
            raise ReconciliationError("an uncertain pending mutation requires manual reconciliation")
        payload = self.client.get_open_orders()
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
            raise ReconciliationError("open orders response must be a list")
        venue: dict[str, Mapping[str, Any]] = {}
        for item in payload:
            if not isinstance(item, Mapping):
                raise ReconciliationError("malformed open order")
            venue_id = item.get("order_id", item.get("venue_order_id"))
            if not isinstance(venue_id, str) or not venue_id:
                raise ReconciliationError("open order has no id")
            venue[venue_id] = item
        local = {
            row["venue_order_id"]: row
            for row in self.connection.execute(
                "SELECT * FROM order_lineage WHERE status IN ('new','partially_filled')"
            )
        }
        if set(venue) != set(local):
            raise ReconciliationError("venue/local open-order mismatch")
        for venue_id, item in venue.items():
            status = str(item.get("status", "")).lower()
            if status not in {"new", "open", "partially_filled"}:
                raise ReconciliationError(f"unknown open order status: {status}")
            filled = decimal_from_api(item.get("filled_quantity", local[venue_id]["filled_quantity"]), "filled_quantity")
            quantity = decimal_from_api(local[venue_id]["quantity"], "quantity")
            leaves = quantity - filled
            if leaves < 0:
                raise ReconciliationError("filled quantity exceeds original quantity")
            local_status = "partially_filled" if filled > 0 else "new"
            self.connection.execute(
                "UPDATE order_lineage SET filled_quantity=?, leaves_quantity=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE venue_order_id=?",
                (decimal_text(filled), decimal_text(leaves), local_status, venue_id),
            )
            if filled > 0:
                self.reconcile_fills(venue_id)
        self.reconciled = True

    def reconcile_fills(self, venue_order_id: str) -> None:
        """Upsert maker/taker fills and fees for an affected venue order."""
        payload = self.client.get_fills(venue_order_id)
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
            raise ReconciliationError("fills response must be a list")
        for item in payload:
            if not isinstance(item, Mapping):
                raise ReconciliationError("malformed fill")
            try:
                fill_id = str(item["fill_id"])
                quantity = decimal_text(decimal_from_api(item["quantity"], "fill quantity"))
                price = decimal_text(decimal_from_api(item["price"], "fill price"))
                fee = decimal_text(decimal_from_api(item["fee"], "fill fee"))
                fee_currency = str(item["fee_currency"]).upper()
                is_maker = item["im"]
            except KeyError as exc:
                raise ReconciliationError("fill is missing required fields") from exc
            if not fill_id or not fee_currency or not isinstance(is_maker, bool):
                raise ReconciliationError("fill fields have invalid types")
            self.connection.execute(
                "INSERT OR IGNORE INTO fills(fill_id,venue_order_id,quantity,price,fee,fee_currency,is_maker) VALUES(?,?,?,?,?,?,?)",
                (fill_id, venue_order_id, quantity, price, fee, fee_currency, int(is_maker)),
            )

    def close(self) -> None:
        """Close the SQLite connection."""
        self.connection.close()
