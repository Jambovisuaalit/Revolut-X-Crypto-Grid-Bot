"""Isolated authenticated, strictly GET-only Revolut X account reader.

This module is *not* a subclass of RevolutXClient and never imports the
execution client. Its only transport primitive is session.get(); there is no
request(method, ...), write endpoint, bearer export, key file loader, or
auto-runner. API credentials are provided in memory by an approved, future
local credential vault, not read from environment or GitHub Actions.

Official contract: https://developer.revolut.com/docs/api/revolut-x-crypto-exchange
All customer data tests in this branch are mocked: NO authenticated API calls.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode
from uuid import UUID

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


BASE_URL = "https://revx.revolut.com/api"
BALANCES = "/1.0/balances"
ACTIVE = "/1.0/orders/active"
HISTORY = "/1.0/orders/historical"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PAGES = 100
MAX_ROWS = 10_000
MAX_RATE_RETRIES = 2
MAX_RETRY_AFTER_MS = 10_000
LIMIT = 100
IDENTITY = re.compile(r"^[A-Za-z0-9]{64}$")
CURRENCY = re.compile(r"^[A-Z0-9]{2,12}$")
SYMBOL = re.compile(r"^[A-Z0-9]{2,12}[/\-][A-Z0-9]{2,12}$")
KNOWN_ORDER_STATES = frozenset({
    "pending_new", "new", "partially_filled", "filled", "cancelled",
    "rejected", "pending_cancel", "replaced", "expired",
})


class AccountReadError(RuntimeError):
    """Fail-closed exception with sanitized, non-credential details."""


class AccountTransport(Protocol):
    """Only GET is structurally available to the account reader."""

    def get(self, url: str, *, headers: Mapping[str, str], timeout: int,
            allow_redirects: bool, params: None = None) -> Any:
        ...


@dataclass(frozen=True)
class Balance:
    currency: str
    available: Decimal
    reserved: Decimal
    total: Decimal
    staked: Decimal


@dataclass(frozen=True)
class AccountSnapshot:
    """Sequential GET observation; *not* an atomic or live-trading approval."""

    captured_ms: int
    balances: tuple[Balance, ...]
    active_orders: tuple[Mapping[str, Any], ...]
    historical_orders: tuple[Mapping[str, Any], ...]
    active_pages: int
    historical_pages: int
    data_provenance: str = "SIGNED_GET_NOT_ACCOUNT_VERIFIED"
    reconciled_for_trading: bool = False
    live_trading_approval: str = "NO_GO"


def _decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise AccountReadError(f"{field}: expected decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise AccountReadError(f"{field}: invalid decimal") from None
    if not parsed.is_finite() or parsed < 0:
        raise AccountReadError(f"{field}: invalid nonnegative amount")
    return parsed


def _canonical_uuid(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise AccountReadError("invalid venue order identifier")
    try:
        if str(UUID(value)) != value.lower():
            raise ValueError("non-canonical uuid")
    except ValueError:
        raise AccountReadError("invalid venue order identifier") from None
    return value


def _balances(payload: Any) -> tuple[Balance, ...]:
    if not isinstance(payload, list) or len(payload) > 500:
        raise AccountReadError("balances must be a bounded array")
    output = []
    seen = set()
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("currency"), str):
            raise AccountReadError("invalid balance row")
        currency = item["currency"]
        if not CURRENCY.fullmatch(currency) or currency in seen:
            raise AccountReadError("invalid or repeated currency")
        seen.add(currency)
        available = _decimal(item.get("available"), "balance.available")
        reserved = _decimal(item.get("reserved"), "balance.reserved")
        total = _decimal(item.get("total"), "balance.total")
        staked = _decimal(item.get("staked", "0"), "balance.staked")
        if total != available + reserved + staked:
            raise AccountReadError("balance components do not reconcile")
        output.append(Balance(currency, available, reserved, total, staked))
    return tuple(output)


def _order(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise AccountReadError("malformed order row")
    venue_id = _canonical_uuid(row.get("id"))
    client_id = row.get("client_order_id")
    if client_id is not None:
        _canonical_uuid(client_id)
    symbol = row.get("symbol")
    side = row.get("side")
    status = row.get("status")
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise AccountReadError("invalid order symbol")
    if side not in ("buy", "sell") or status not in KNOWN_ORDER_STATES:
        raise AccountReadError("unknown order side or status")
    # Other order types may not contain a limit price; do not fabricate one.
    for field in ("quantity", "filled_quantity", "leaves_quantity", "price"):
        if field in row and row[field] is not None:
            _decimal(row[field], f"order.{field}")
    if all(key in row for key in ("quantity","filled_quantity","leaves_quantity")):
        if (_decimal(row["quantity"], "order.quantity")
                != _decimal(row["filled_quantity"], "order.filled")
                 + _decimal(row["leaves_quantity"], "order.leaves")):
            raise AccountReadError("order remaining quantity mismatch")
    # Return a redacted operational subset, never unconstrained account JSON.
    return {
        "id": venue_id, "client_order_id": client_id,
        "symbol": symbol, "side": side, "status": status,
        "type": row.get("type"),
        "price": row.get("price"), "quantity": row.get("quantity"),
        "filled_quantity": row.get("filled_quantity"),
        "leaves_quantity": row.get("leaves_quantity"),
    }


class AuthenticatedAccountReader:
    """Signed GET-only account access; no mutation API, no key loading."""

    def __init__(
        self,
        api_key: str,
        private_key: Ed25519PrivateKey,
        *,
        transport: AccountTransport | None = None,
        clock_ms: Callable[[], int] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(api_key, str) or not IDENTITY.fullmatch(api_key):
            raise ValueError("API key must be a 64-character alphanumeric identifier")
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("an Ed25519 private key object is required")
        self._api_key = api_key
        self._private_key = private_key
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._sleeper = sleeper
        if transport is None:
            session = requests.Session()
            session.trust_env = False
            session.cookies.clear()
            self._transport: AccountTransport = session
            self._owned_session: requests.Session | None = session
        else:
            self._transport = transport
            self._owned_session = None

    def __repr__(self) -> str:
        return "<AuthenticatedAccountReader GET_ONLY credentials=REDACTED>"

    def close(self) -> None:
        if self._owned_session is not None:
            self._owned_session.close()
            self._owned_session = None

    def __enter__(self) -> "AuthenticatedAccountReader":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _headers(self, path: str, query: str) -> dict[str, str]:
        clock = self._clock_ms()
        if type(clock) is not int or clock < 1_000_000_000_000:
            raise AccountReadError("invalid signing clock")
        timestamp = str(clock)
        message = f"{timestamp}GET/api{path}{query}".encode("utf-8")
        signature = base64.b64encode(self._private_key.sign(message)).decode("ascii")
        return {
            "X-Revx-API-Key": self._api_key,
            "X-Revx-Timestamp": timestamp,
            "X-Revx-Signature": signature,
            "Accept": "application/json",
            "User-Agent": "GridConsoleReadOnly/0.1",
        }

    def _get(self, path: str, *, cursor: str | None = None) -> Any:
        if path not in (BALANCES, ACTIVE, HISTORY) and not (
            path.startswith("/1.0/orders/") and (
                _is_order_detail(path) or _is_fills_detail(path)
            )
        ):
            raise AccountReadError("endpoint not on GET-only allowlist")
        params: dict[str, str] = {}
        if path in (ACTIVE, HISTORY):
            params["limit"] = str(LIMIT)
            if cursor is not None:
                if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024 or any(
                    ord(char) < 33 or ord(char) > 126 for char in cursor
                ):
                    raise AccountReadError("invalid pagination cursor")
                params["cursor"] = cursor
        elif cursor is not None:
            raise AccountReadError("cursor forbidden for this endpoint")
        query = urlencode(sorted(params.items()), safe="")
        full_url = BASE_URL + path + ("?" + query if query else "")
        for attempt in range(MAX_RATE_RETRIES+1):
            headers = self._headers(path, query)
            try:
                response = self._transport.get(
                    full_url, headers=headers, timeout=10,
                    allow_redirects=False, params=None,
                )
            except requests.RequestException:
                raise AccountReadError("GET transport failed") from None
            if response.status_code == 429:
                if attempt == MAX_RATE_RETRIES:
                    raise AccountReadError("rate limit retry budget exhausted")
                try:
                    delay = int(response.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    raise AccountReadError("invalid Retry-After milliseconds") from None
                if delay < 0 or delay > MAX_RETRY_AFTER_MS:
                    raise AccountReadError("Retry-After outside permitted range")
                self._sleeper(delay / 1000)
                continue
            if response.status_code in (301, 302, 303, 307, 308):
                raise AccountReadError("redirect rejected")
            if response.status_code in (401, 403):
                raise AccountReadError("account authentication/permission denied")
            if response.status_code != 200:
                raise AccountReadError("authenticated account GET failed")
            try:
                raw = response.content
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise AccountReadError("response exceeded size limit")
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError, TypeError):
                raise AccountReadError("malformed JSON from account endpoint") from None
            return result
        raise AccountReadError("unexpected retry path")

    def balances(self) -> tuple[Balance, ...]:
        return _balances(self._get(BALANCES))

    def _paged_orders(self, endpoint: str) -> tuple[tuple[Mapping[str, Any], ...], int]:
        if endpoint not in (ACTIVE, HISTORY):
            raise AccountReadError("unsupported paginated endpoint")
        items: list[Mapping[str, Any]] = []
        seen_cursors: set[str] = set()
        seen_ids: set[str] = set()
        cursor: str | None = None
        for page in range(1, MAX_PAGES + 1):
            payload = self._get(endpoint, cursor=cursor)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise AccountReadError("orders response has invalid data array")
            if not isinstance(payload.get("metadata"), dict):
                raise AccountReadError("orders response missing metadata")
            rows = payload["data"]
            if len(rows) > LIMIT:
                raise AccountReadError("oversized orders page")
            for row in rows:
                parsed = _order(row)
                if parsed["id"] in seen_ids:
                    raise AccountReadError("duplicate order across pages")
                seen_ids.add(parsed["id"])
                items.append(parsed)
                if len(items) > MAX_ROWS:
                    raise AccountReadError("orders exceed bounded read cap")
            cursor_next = payload["metadata"].get("next_cursor")
            if cursor_next in (None, ""):
                return tuple(items), page
            if not isinstance(cursor_next, str) or not 1 <= len(cursor_next) <= 1024:
                raise AccountReadError("invalid next cursor")
            if cursor_next in seen_cursors:
                raise AccountReadError("repeating pagination cursor")
            seen_cursors.add(cursor_next)
            cursor = cursor_next
        raise AccountReadError("pagination not provably complete")

    def active_orders(self) -> tuple[Mapping[str, Any], ...]:
        return self._paged_orders(ACTIVE)[0]

    def historical_orders(self) -> tuple[Mapping[str, Any], ...]:
        """Default venue history window ONLY; missing row is not proof of rejection."""
        return self._paged_orders(HISTORY)[0]

    def order_details(self, venue_order_id: str) -> Mapping[str, Any]:
        canonical = _canonical_uuid(venue_order_id)
        result = self._get(f"/1.0/orders/{canonical}")
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise AccountReadError("order details missing data object")
        order = _order(result["data"])
        if order["id"] != canonical:
            raise AccountReadError("order details identity mismatch")
        return order

    def order_fills(self, venue_order_id: str) -> tuple[Mapping[str, Any], ...]:
        canonical = _canonical_uuid(venue_order_id)
        result = self._get(f"/1.0/orders/fills/{canonical}")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise AccountReadError("fills response missing data array")
        fills = []
        seen = set()
        for row in result["data"]:
            if not isinstance(row, dict) or not isinstance(row.get("tid"), str) or not row["tid"]:
                raise AccountReadError("malformed order fill")
            if row["tid"] in seen:
                raise AccountReadError("duplicate fill")
            seen.add(row["tid"])
            price = _decimal(row.get("p"), "fill.price")
            qty = _decimal(row.get("q"), "fill.quantity")
            if price == 0 or qty == 0:
                raise AccountReadError("invalid fill price or quantity")
            fills.append({"tid": row["tid"], "p": str(price), "q": str(qty)})
            if len(fills) > MAX_ROWS:
                raise AccountReadError("fills exceed bounded read cap")
        return tuple(fills)

    def snapshot(self) -> AccountSnapshot:
        """Non-atomic sequential read; return nothing if any essential GET fails."""
        balances = self.balances()
        active, active_pages = self._paged_orders(ACTIVE)
        historical, historical_pages = self._paged_orders(HISTORY)
        return AccountSnapshot(
            captured_ms=self._clock_ms(),
            balances=balances, active_orders=active,
            historical_orders=historical, active_pages=active_pages,
            historical_pages=historical_pages,
        )


def _is_order_detail(path: str) -> bool:
    prefix = "/1.0/orders/"
    return path.startswith(prefix) and _uuid_valid(path[len(prefix):])


def _is_fills_detail(path: str) -> bool:
    prefix = "/1.0/orders/fills/"
    return path.startswith(prefix) and _uuid_valid(path[len(prefix):])


def _uuid_valid(text: str) -> bool:
    try:
        _canonical_uuid(text)
        return True
    except AccountReadError:
        return False
