"""Persistent post-only order state machine; ambiguous writes require reconciliation."""

from __future__ import annotations

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
    """Unverified venue/local state; execution must remain blocked."""


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: str
    price: Decimal
    quantity: Decimal


class GridExecutor:
    """Write intent before a mutation, never replay it, adopt only verified venue state."""

    ACTIVE_STATUSES = frozenset({"pending_new", "pending_replace", "venue_pending", "new", "partially_filled", "replace_uncertain"})
    TERMINAL_STATUSES = frozenset({"filled", "cancelled", "rejected", "replaced"})
    KNOWN_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES

    def __init__(
        self,
        client: RevolutXClient,
        pair_rules: Mapping[str, PairRules],
        *,
        database_path: str = "bot_state.db",
        live_trading: bool = False,
    ) -> None:
        self.client = client
        self.pair_rules = {SymbolNormalizer.to_display_symbol(k): v for k, v in pair_rules.items()}
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
            PRAGMA synchronous=FULL;
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
            CREATE TABLE IF NOT EXISTS bot_metadata (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fills_wire (
                fill_id TEXT PRIMARY KEY,
                venue_order_id TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                fee TEXT NULL, fee_currency TEXT NULL, is_maker INTEGER NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                    "execution_instructions": ["post_only"],
                }
            },
        }

    @staticmethod
    def _replace_payload(order: OrderRequest) -> dict[str, Any]:
        """PUT /orders/{id} uses a FLAT body, unlike POST order_configuration."""
        return {
            "client_order_id": order.client_order_id,
            "price": decimal_text(order.price),
            "base_size": decimal_text(order.quantity),
            "execution_instructions": ["post_only"],
        }

    def _validated_order(
        self, client_order_id: str, symbol: str, side: str, price: Decimal, quantity: Decimal
    ) -> OrderRequest:
        try:
            if str(uuid.UUID(client_order_id)) != client_order_id.lower():
                raise ValueError("noncanonical UUID")
        except (ValueError, AttributeError) as exc:
            raise PrecisionError("client_order_id must be a canonical UUID") from exc
        normalized_symbol = SymbolNormalizer.to_display_symbol(symbol)
        side = side.lower()
        if side not in {"buy", "sell"}:
            raise PrecisionError("side must be buy or sell")
        rules = self.pair_rules.get(normalized_symbol)
        if rules is None:
            raise PrecisionError(f"pair rules missing: {normalized_symbol}")
        if not isinstance(price, Decimal) or not isinstance(quantity, Decimal):
            raise PrecisionError("price and quantity must be Decimal")
        rounded_price, rounded_quantity = rules.normalize(price, quantity)
        return OrderRequest(client_order_id, normalized_symbol, side, rounded_price, rounded_quantity)

    def _reserved(self, currency: str) -> Decimal:
        total = Decimal("0")
        for row in self.connection.execute(
            "SELECT symbol,side,price,leaves_quantity FROM order_lineage "
            "WHERE status IN ('pending_new','pending_replace','new','partially_filled','replace_uncertain')"
        ):
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
        free = free_balances.get(currency)
        if not isinstance(free, Decimal) or not free.is_finite() or free < 0:
            raise PrecisionError(f"invalid/missing free balance: {currency}")
        if required > free * Decimal("0.05"):
            raise PrecisionError("single order exceeds 5% of free balance")
        if required + self._reserved(currency) > free:
            raise PrecisionError("aggregate reservation exceeds free balance")

    def _assert_not_halted(self) -> None:
        row = self.connection.execute(
            "SELECT value FROM bot_metadata WHERE key='kill_switch_triggered'"
        ).fetchone()
        if row is not None and row["value"] == "true":
            raise ReconciliationError("persistent kill switch is triggered; trading halted")

    def create_order(
        self, *, client_order_id: str, symbol: str, side: str,
        price: Decimal, quantity: Decimal, free_balances: Mapping[str, Decimal]
    ) -> Mapping[str, Any]:
        with self._lock:
            self._assert_not_halted()
            if not self.reconciled:
                raise ReconciliationError("startup/recovery reconciliation is required")
            order = self._validated_order(client_order_id, symbol, side, price, quantity)
            existing = self.connection.execute(
                "SELECT * FROM order_lineage WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["symbol"] != order.symbol or existing["side"] != order.side
                    or decimal_from_api(existing["price"], "price") != order.price
                    or decimal_from_api(existing["quantity"], "quantity") != order.quantity
                ):
                    raise ReconciliationError("client order id reused for a different instruction")
                if existing["status"] in {"pending_new", "pending_replace", "replace_uncertain"}:
                    raise ReconciliationError("order outcome remains uncertain; never replay")
                return {"client_order_id": client_order_id, "venue_order_id": existing["venue_order_id"],
                        "status": existing["status"], "idempotent": True}
            self._assert_funding(order, free_balances)
            payload = self._payload(order)
            if not self.live_trading:
                return {"dry_run": True, "payload": payload}
            self.connection.execute(
                "INSERT INTO order_lineage "
                "(client_order_id,root_client_order_id,symbol,side,price,quantity,leaves_quantity,status) "
                "VALUES(?,?,?,?,?,?,?,'pending_new')",
                (client_order_id, client_order_id, order.symbol, order.side,
                 decimal_text(order.price), decimal_text(order.quantity), decimal_text(order.quantity)),
            )
            try:
                result = self.client.place_order(payload)
                self.connection.execute(
                    "UPDATE order_lineage SET venue_order_id=?,status=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE client_order_id=?",
                    (result["venue_order_id"], "venue_pending" if result["state"] == "pending_new" else result["state"], client_order_id),
                )
                return result
            except Exception:
                self.reconciled = False
                raise

    def replace_order(
        self, venue_order_id: str, *, new_price: Decimal,
        free_balances: Mapping[str, Decimal], client_order_id: str | None = None
    ) -> Mapping[str, Any]:
        with self._lock:
            self._assert_not_halted()
            if not self.reconciled:
                raise ReconciliationError("reconciliation is required")
            parent = self.connection.execute(
                "SELECT * FROM order_lineage WHERE venue_order_id=?", (venue_order_id,)
            ).fetchone()
            if parent is None or parent["status"] not in {"new", "partially_filled", "venue_pending"}:
                raise ReconciliationError("only known active orders may be replaced")
            child_id = client_order_id or str(uuid.uuid4())
            order = self._validated_order(
                child_id, parent["symbol"], parent["side"], new_price,
                decimal_from_api(parent["leaves_quantity"], "leaves_quantity")
            )
            payload = self._replace_payload(order)
            existing = self.connection.execute(
                "SELECT 1 FROM order_lineage WHERE client_order_id=?", (child_id,)
            ).fetchone()
            if existing:
                raise ReconciliationError("replacement client id already used; never replay")
            if not self.live_trading:
                return {"dry_run": True, "payload": payload}
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                # Exclude replaced order from aggregate reserves only inside the
                # DB transaction. Rollback restores the parent if checks fail.
                self.connection.execute(
                    "UPDATE order_lineage SET status='replaced' WHERE venue_order_id=?",
                    (venue_order_id,),
                )
                self._assert_funding(order, free_balances)
                self.connection.execute(
                    "UPDATE order_lineage SET status='replace_uncertain' WHERE venue_order_id=?",
                    (venue_order_id,),
                )
                self.connection.execute(
                    "INSERT INTO order_lineage "
                    "(client_order_id,parent_venue_order_id,root_client_order_id,symbol,side,price,"
                    "quantity,leaves_quantity,status,version) VALUES(?,?,?,?,?,?,?,?,'pending_replace',?)",
                    (child_id, venue_order_id, parent["root_client_order_id"], order.symbol,
                     order.side, decimal_text(order.price), decimal_text(order.quantity),
                     decimal_text(order.quantity), parent["version"] + 1),
                )
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
            try:
                result = self.client.replace_order(venue_order_id, payload)
                if result["venue_order_id"] == venue_order_id:
                    raise ReconciliationError("replacement response reused parent venue id")
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute(
                    "UPDATE order_lineage SET status='replaced' WHERE venue_order_id=?", (venue_order_id,)
                )
                self.connection.execute(
                    "UPDATE order_lineage SET venue_order_id=?,status=? WHERE client_order_id=?",
                    (result["venue_order_id"], "venue_pending" if result["state"] == "pending_new" else result["state"], child_id),
                )
                self.connection.execute("COMMIT")
                return result
            except Exception:
                # Do not revert to "new": parent and child may BOTH be present at the venue.
                if self.connection.in_transaction:
                    self.connection.execute("ROLLBACK")
                self.reconciled = False
                raise

    @staticmethod
    def _order_identity(row: sqlite3.Row, item: Mapping[str, Any]) -> None:
        """Require strict identity, full original quantity and maker-only semantics."""
        try:
            if not (
                item.get("client_order_id") == row["client_order_id"]
                and SymbolNormalizer.to_display_symbol(str(item["symbol"])) == row["symbol"]
                and str(item["side"]).lower() == row["side"]
                and str(item["type"]).lower() == "limit"
                and "post_only" in item["execution_instructions"]
                and decimal_from_api(item["price"], "venue price") == decimal_from_api(row["price"], "local price")
                and decimal_from_api(item["quantity"], "venue quantity") ==
                    decimal_from_api(row["quantity"], "local quantity")
            ):
                raise ReconciliationError("venue order does not match persisted order intent")
        except (KeyError, TypeError, PrecisionError) as exc:
            raise ReconciliationError("venue order has missing/invalid identity fields") from exc

    @staticmethod
    def _order_status(item: Mapping[str, Any]) -> tuple[str, Decimal, Decimal]:
        status = item.get("status")
        if status not in GridExecutor.KNOWN_STATUSES:
            raise ReconciliationError(f"unknown venue status: {status}")
        quantity = decimal_from_api(item.get("quantity"), "quantity")
        filled = decimal_from_api(item.get("filled_quantity"), "filled_quantity")
        leaves = decimal_from_api(item.get("leaves_quantity"), "leaves_quantity")
        if min(quantity, filled, leaves) < 0 or filled + leaves != quantity:
            raise ReconciliationError("invalid venue filled/leaves accounting")
        return status, filled, leaves

    def reconcile_open_orders(self) -> None:
        """Adopt confirmed pending writes, or keep trading blocked if outcome cannot be proved.

        Recent history is only a lookup source. Missing entries NEVER authorize another POST/PUT.
        """
        with self._lock:
            self.reconciled = False
            active = self.client.get_open_orders()
            rows = list(self.connection.execute("SELECT * FROM order_lineage"))
            local_by_id = {row["venue_order_id"]: row for row in rows if row["venue_order_id"]}
            pending = [row for row in rows if row["status"] in {"pending_new", "pending_replace", "replace_uncertain"}]
            active_by_id = {item["id"]: item for item in active}
            if len(active_by_id) != len(active):
                raise ReconciliationError("duplicate active venue identifiers")
            need_history = bool(pending or any(
                row["status"] in {"new", "partially_filled", "venue_pending"} and row["venue_order_id"] not in active_by_id
                for row in rows
            ))
            historical = self.client.get_historical_orders() if need_history else []
            history_by_id = {item["id"]: item for item in historical}
            by_client: dict[str, list[Mapping[str, Any]]] = {}
            for item in active + historical:
                key = item.get("client_order_id")
                if isinstance(key, str) and key:
                    by_client.setdefault(key, []).append(item)
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                for row in pending:
                    if row["status"] == "replace_uncertain":
                        continue  # resolved only when its pending child is confirmed
                    candidates = by_client.get(row["client_order_id"], [])
                    if len(candidates) != 1:
                        raise ReconciliationError("pending mutation not uniquely verified; manual review required")
                    item = candidates[0]
                    self._order_identity(row, item)
                    status, filled, leaves = self._order_status(item)
                    if row["status"] == "pending_replace":
                        parent_id = row["parent_venue_order_id"]
                        if (not parent_id or parent_id == item["id"]
                                or item.get("previous_order_id") != parent_id):
                            raise ReconciliationError("unverified replacement parent linkage")
                        if parent_id in active_by_id:
                            raise ReconciliationError("parent and replacement both active; manual review required")
                        self.connection.execute(
                            "UPDATE order_lineage SET status='replaced' WHERE venue_order_id=?", (parent_id,)
                        )
                    self.connection.execute(
                        "UPDATE order_lineage SET venue_order_id=?,status=?,filled_quantity=?,"
                        "leaves_quantity=?,updated_at=CURRENT_TIMESTAMP WHERE client_order_id=?",
                        (item["id"], "venue_pending" if status == "pending_new" else status, decimal_text(filled), decimal_text(leaves), row["client_order_id"]),
                    )
                    if filled > 0:
                        self.reconcile_fills(item["id"])
                # Re-query after potential pending adoption.
                rows = list(self.connection.execute("SELECT * FROM order_lineage"))
                local_by_id = {row["venue_order_id"]: row for row in rows if row["venue_order_id"]}
                if any(row["status"] in {"pending_new", "pending_replace", "replace_uncertain"} for row in rows):
                    raise ReconciliationError("unresolved mutation remains")
                if any(vid not in local_by_id for vid in active_by_id):
                    raise ReconciliationError("untracked venue order exists")
                for row in rows:
                    if row["status"] not in {"new", "partially_filled", "venue_pending"}:
                        continue
                    vid = row["venue_order_id"]
                    item = active_by_id.get(vid) or history_by_id.get(vid)
                    if item is None:
                        raise ReconciliationError("previously active order missing from venue history")
                    self._order_identity(row, item)
                    status, filled, leaves = self._order_status(item)
                    self.connection.execute(
                        "UPDATE order_lineage SET status=?,filled_quantity=?,leaves_quantity=?,"
                        "updated_at=CURRENT_TIMESTAMP WHERE venue_order_id=?",
                        ("venue_pending" if status == "pending_new" else status, decimal_text(filled), decimal_text(leaves), vid),
                    )
                    if filled > 0:
                        self.reconcile_fills(vid)
                for vid, item in active_by_id.items():
                    row = self.connection.execute(
                        "SELECT status FROM order_lineage WHERE venue_order_id=?", (vid,)
                    ).fetchone()
                    if not row or row["status"] not in {"new", "partially_filled", "venue_pending"}:
                        raise ReconciliationError("unverified active order/status mismatch")
                self.connection.execute("COMMIT")
                self.reconciled = True
            except Exception:
                if self.connection.in_transaction:
                    self.connection.execute("ROLLBACK")
                raise

    def reconcile_fills(self, venue_order_id: str) -> None:
        """Record the official Trade wire values without inventing fee/maker information."""
        payload = self.client.get_fills(venue_order_id)
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
            raise ReconciliationError("fills must be an array")
        for item in payload:
            if not isinstance(item, Mapping):
                raise ReconciliationError("malformed fill")
            fill_id = item.get("tid", item.get("fill_id"))
            quantity = item.get("q", item.get("quantity"))
            price = item.get("p", item.get("price"))
            if not isinstance(fill_id, str) or not fill_id:
                raise ReconciliationError("fill id missing")
            qty = decimal_from_api(quantity, "fill quantity")
            pr = decimal_from_api(price, "fill price")
            if qty <= 0 or pr <= 0:
                raise ReconciliationError("fill values must be positive")
            fee = item.get("fee")
            fee_value = decimal_text(decimal_from_api(fee, "fee")) if fee is not None else None
            maker = item.get("im")
            if maker is not None and not isinstance(maker, bool):
                raise ReconciliationError("invalid maker field")
            self.connection.execute(
                "INSERT OR IGNORE INTO fills_wire "
                "(fill_id,venue_order_id,quantity,price,fee,fee_currency,is_maker) VALUES(?,?,?,?,?,?,?)",
                (fill_id, venue_order_id, decimal_text(qty), decimal_text(pr),
                 fee_value, item.get("fee_currency"), None if maker is None else int(maker)),
            )

    def close(self) -> None:
        self.connection.close()
