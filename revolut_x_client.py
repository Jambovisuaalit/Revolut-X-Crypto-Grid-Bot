"""Revolut X REST client with exact Ed25519 signing and 429 backoff."""

from __future__ import annotations

import base64
import json
import logging
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence, TypeAlias
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

LOGGER = logging.getLogger(__name__)

JsonObject: TypeAlias = dict[str, Any]
QueryValue: TypeAlias = str | int | Sequence[str]
FinancialWireValue: TypeAlias = Decimal | str


class RevolutXError(RuntimeError):
    """Base exception for Revolut X client failures."""


class RevolutXHTTPError(RevolutXError):
    """Raised for non-successful Revolut X HTTP responses."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Revolut X HTTP {status_code}: {message}")
        self.status_code = status_code


class RevolutXClient:
    """Signed REST client for the Revolut X Crypto Exchange API.

    Financial order values may enter as ``Decimal`` or pre-serialized decimal
    strings. They are always validated through ``Decimal`` and are emitted to
    the wire as non-scientific JSON strings. ``float`` is never accepted for a
    financial order field.
    """

    def __init__(
        self,
        api_key: str,
        private_key_path: str,
        *,
        base_url: str = "https://revx.revolut.com/api",
        timeout_seconds: int = 10,
        max_429_retries: int = 8,
        session: requests.Session | None = None,
    ) -> None:
        if len(api_key) != 64 or not api_key.isalnum():
            raise ValueError("api_key must be a 64-character alphanumeric string")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_429_retries < 0:
            raise ValueError("max_429_retries must be non-negative")

        self._api_key = api_key
        self._private_key = self._load_private_key(private_key_path)
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_429_retries = max_429_retries
        self._session = session or requests.Session()

    @staticmethod
    def _load_private_key(private_key_path: str) -> Ed25519PrivateKey:
        """Load an unencrypted Ed25519 private key from a PEM file."""
        try:
            pem = Path(private_key_path).read_bytes()
            key = serialization.load_pem_private_key(pem, password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("failed to load Ed25519 private key") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("private key must be Ed25519")
        return key

    @staticmethod
    def _minified_body(body: Mapping[str, Any] | None) -> str:
        """Serialize a request body exactly as compact JSON for signing/wire use."""
        if body is None:
            return ""
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _query_string(params: Mapping[str, QueryValue] | None) -> str:
        """Build the exact query string used both in the URL and signature."""
        if not params:
            return ""
        normalized: list[tuple[str, str | int]] = []
        for key, value in params.items():
            if isinstance(value, (str, int)):
                normalized.append((key, value))
            else:
                normalized.append((key, ",".join(value)))
        return urlencode(normalized)

    @staticmethod
    def _signature_message(
        timestamp_ms: str,
        method: str,
        signing_path: str,
        query: str,
        body: str,
    ) -> bytes:
        """Return Timestamp+Method+Path+Query+Body with no separators."""
        if not signing_path.startswith("/api/"):
            raise ValueError("signing_path must start with '/api/'")
        return f"{timestamp_ms}{method.upper()}{signing_path}{query}{body}".encode("utf-8")

    @staticmethod
    def _financial_string(value: FinancialWireValue, *, field_name: str) -> str:
        """Validate a financial value through Decimal and serialize for the API."""
        if isinstance(value, float):
            raise TypeError(f"{field_name} must never be float")
        if not isinstance(value, (Decimal, str)):
            raise TypeError(f"{field_name} must be Decimal or decimal string")
        try:
            parsed = value if isinstance(value, Decimal) else Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{field_name} is not a valid decimal value") from exc
        if not parsed.is_finite():
            raise ValueError(f"{field_name} must be finite")
        if parsed <= Decimal("0"):
            raise ValueError(f"{field_name} must be positive")
        return format(parsed, "f")

    def _signed_headers(self, method: str, endpoint: str, query: str, body: str) -> dict[str, str]:
        """Build Revolut X authentication headers without exposing secret material."""
        if not endpoint.startswith("/") or endpoint.startswith("/api/"):
            raise ValueError("endpoint must start with '/' and exclude the '/api' prefix")

        timestamp_ms = str(time.time_ns() // 1_000_000)
        signing_path = f"/api{endpoint}"
        message = self._signature_message(timestamp_ms, method, signing_path, query, body)
        signature = base64.b64encode(self._private_key.sign(message)).decode("ascii")
        headers = {
            "Accept": "application/json",
            "X-Revx-API-Key": self._api_key,
            "X-Revx-Timestamp": timestamp_ms,
            "X-Revx-Signature": signature,
        }
        if body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, QueryValue] | None = None,
        body: Mapping[str, Any] | None = None,
        auth: bool = True,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> Any:
        """Execute a request, automatically honoring 429 Retry-After milliseconds."""
        if not endpoint.startswith("/"):
            raise ValueError("endpoint must start with '/'")

        query = self._query_string(params)
        body_text = self._minified_body(body)
        url = f"{self._base_url}{endpoint}"
        if query:
            url = f"{url}?{query}"

        retries = 0
        while True:
            headers = (
                self._signed_headers(method, endpoint, query, body_text)
                if auth
                else {"Accept": "application/json"}
            )
            if body_text and not auth:
                headers["Content-Type"] = "application/json"

            try:
                response = self._session.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    data=body_text if body_text else None,
                    timeout=self._timeout_seconds,
                )
            except requests.RequestException as exc:
                raise RevolutXError(f"network request failed: {exc.__class__.__name__}") from exc

            if response.status_code == 429:
                if retries >= self._max_429_retries:
                    raise RevolutXHTTPError(429, "rate-limit retry budget exhausted")
                retry_after = response.headers.get("Retry-After")
                if retry_after is None:
                    raise RevolutXHTTPError(429, "missing Retry-After header")
                try:
                    retry_ms = max(1, int(retry_after))
                except ValueError as exc:
                    raise RevolutXHTTPError(429, "invalid Retry-After header") from exc
                retries += 1
                LOGGER.warning(
                    "Revolut X rate limit hit; retrying after %d ms (attempt %d)",
                    retry_ms,
                    retries,
                )
                time.sleep(retry_ms / 1000)
                continue

            if response.status_code not in expected_statuses:
                text = (response.text or "").replace("\n", " ")[:500]
                raise RevolutXHTTPError(response.status_code, text or "unexpected response")

            if response.status_code == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise RevolutXError("malformed JSON response") from exc

    def get_balances(self) -> list[JsonObject]:
        """Return account balances."""
        payload = self._request("GET", "/1.0/balances")
        if not isinstance(payload, list):
            raise RevolutXError("balances response must be a list")
        return [dict(item) for item in payload if isinstance(item, dict)]

    def get_pair_configuration(self, region: str | None = None) -> dict[str, JsonObject]:
        """Return public pair configuration keyed by pair symbol."""
        params: dict[str, QueryValue] = {}
        if region:
            params["region"] = region
        payload = self._request(
            "GET",
            "/1.0/public/configuration/pairs",
            params=params,
            auth=False,
        )
        if not isinstance(payload, dict):
            raise RevolutXError("pair configuration response must be an object")
        return {str(key): dict(value) for key, value in payload.items() if isinstance(value, dict)}

    def get_account_pair_configuration(self) -> dict[str, JsonObject]:
        """Return pair configuration for the authenticated account region."""
        payload = self._request("GET", "/1.0/configuration/pairs")
        if not isinstance(payload, dict):
            raise RevolutXError("account pair configuration response must be an object")
        return {str(key): dict(value) for key, value in payload.items() if isinstance(value, dict)}

    def get_tickers(self, symbols: Sequence[str] | None = None) -> tuple[list[JsonObject], int]:
        """Return authenticated ticker snapshots and exchange timestamp in milliseconds."""
        params: dict[str, QueryValue] = {}
        if symbols:
            params["symbols"] = list(symbols)
        payload = self._request("GET", "/1.0/tickers", params=params)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RevolutXError("ticker response shape is invalid")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("timestamp"), int):
            raise RevolutXError("ticker metadata timestamp missing")
        return [dict(item) for item in payload["data"] if isinstance(item, dict)], metadata["timestamp"]

    def get_active_orders(self, symbols: Sequence[str] | None = None) -> list[JsonObject]:
        """Return all active orders, following pagination cursors."""
        orders: list[JsonObject] = []
        cursor: str | None = None
        while True:
            params: dict[str, QueryValue] = {"limit": 100}
            if symbols:
                params["symbols"] = list(symbols)
            if cursor:
                params["cursor"] = cursor
            payload = self._request("GET", "/1.0/orders/active", params=params)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise RevolutXError("active orders response shape is invalid")
            orders.extend(dict(item) for item in payload["data"] if isinstance(item, dict))
            metadata = payload.get("metadata")
            next_cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
            if not next_cursor:
                break
            if not isinstance(next_cursor, str):
                raise RevolutXError("active orders cursor is invalid")
            cursor = next_cursor
        return orders

    def place_limit_order(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        base_size: FinancialWireValue,
        price: FinancialWireValue,
    ) -> JsonObject:
        """Place a post-only limit order with Decimal-safe financial fields."""
        body = {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side.lower(),
            "order_configuration": {
                "limit": {
                    "base_size": self._financial_string(base_size, field_name="base_size"),
                    "price": self._financial_string(price, field_name="price"),
                    "execution_instructions": ["post_only"],
                }
            },
        }
        payload = self._request("POST", "/1.0/orders", body=body)
        return self._extract_order_data(payload)

    def replace_limit_order(
        self,
        *,
        venue_order_id: str,
        client_order_id: str,
        base_size: FinancialWireValue,
        price: FinancialWireValue,
    ) -> JsonObject:
        """Atomically replace an order with Decimal-safe post-only parameters."""
        body = {
            "client_order_id": client_order_id,
            "base_size": self._financial_string(base_size, field_name="base_size"),
            "price": self._financial_string(price, field_name="price"),
            "execution_instructions": ["post_only"],
        }
        payload = self._request("PUT", f"/1.0/orders/{venue_order_id}", body=body)
        return self._extract_order_data(payload)

    def cancel_all_orders(self) -> None:
        """Cancel all active account orders."""
        self._request("DELETE", "/1.0/orders", expected_statuses=(204,))

    @staticmethod
    def _extract_order_data(payload: Any) -> JsonObject:
        """Extract the single order object from a successful response."""
        if not isinstance(payload, dict) or "data" not in payload:
            raise RevolutXError("order response shape is invalid")
        data = payload["data"]
        if isinstance(data, dict):
            return dict(data)
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            return dict(data[0])
        raise RevolutXError("order response data is invalid")
