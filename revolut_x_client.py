"""Fail-closed Revolut X REST transport. No live requests are made by tests."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from uuid import UUID

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class ApiError(RuntimeError):
    """Response is invalid or the request was safely rejected."""


class UncertainMutationError(ApiError):
    """A mutation may have been accepted; never automatically replay it."""


class RevolutXClient:
    """Authenticated API client with conservative mutation error handling."""

    def __init__(
        self,
        api_key: str,
        private_key: Ed25519PrivateKey,
        *,
        base_url: str = "https://revx.revolut.com/api",
        timeout_seconds: int = 10,
        max_429_retries: int = 5,
        session: requests.Session | None = None,
        clock_ms: Callable[[], int] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.private_key = private_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_429_retries = max_429_retries
        self.session = session if session is not None else requests.Session()
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.sleeper = sleeper

    @classmethod
    def from_pem(cls, api_key: str, path: str, **kwargs: Any) -> "RevolutXClient":
        loaded = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError("private key must be Ed25519")
        return cls(api_key, loaded, **kwargs)

    @staticmethod
    def _body_text(body: Mapping[str, Any] | None) -> str:
        return "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    def signed_headers(self, method: str, path: str, query: str = "", body_text: str = "") -> dict[str, str]:
        timestamp = str(self.clock_ms())
        signing_path = path if path.startswith("/api/") else f"/api{path}"
        message = f"{timestamp}{method.upper()}{signing_path}{query}{body_text}".encode()
        signature = base64.b64encode(self.private_key.sign(message)).decode("ascii")
        return {
            "X-Revx-API-Key": self.api_key,
            "X-Revx-Timestamp": timestamp,
            "X-Revx-Signature": signature,
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        mutation: bool = False,
    ) -> Any:
        """Retry GET 429 only. Any potentially delivered mutation is uncertain."""
        query = urlencode(sorted((params or {}).items()))
        body_text = self._body_text(body)
        relative_path = path if path.startswith("/") else f"/{path}"
        for attempt in range(self.max_429_retries + 1):
            headers = self.signed_headers(method, relative_path, query, body_text)
            url = f"{self.base_url}{relative_path}"
            if query:
                url = f"{url}?{query}"
            try:
                response = self.session.request(
                    method.upper(), url, headers=headers, data=body_text or None, timeout=self.timeout_seconds
                )
            except requests.RequestException as exc:
                error = UncertainMutationError if mutation else ApiError
                raise error("transport failure during API request") from exc
            if response.status_code == 429:
                if mutation:
                    raise UncertainMutationError("mutation returned 429; reconcile before any further write")
                if attempt == self.max_429_retries:
                    raise ApiError("rate-limit retry budget exhausted")
                try:
                    delay_ms = int(response.headers["Retry-After"])
                    if delay_ms < 0 or delay_ms > 300_000:
                        raise ValueError("out-of-range Retry-After")
                except (KeyError, ValueError) as exc:
                    raise ApiError("invalid Retry-After header") from exc
                self.sleeper(delay_ms / 1000)
                continue
            if response.status_code in {401, 403}:
                raise ApiError("authentication or authorization rejected")
            if mutation and (response.status_code >= 500 or response.status_code == 409):
                raise UncertainMutationError("mutation may have reached venue; reconcile before retry")
            if not 200 <= response.status_code < 300:
                raise ApiError(f"API returned HTTP {response.status_code}")
            if response.status_code == 204:
                return None
            if not response.content:
                if mutation:
                    raise UncertainMutationError("empty mutation acknowledgement")
                raise ApiError("empty API response")
            try:
                return response.json()
            except ValueError as exc:
                error = UncertainMutationError if mutation else ApiError
                raise error("API returned malformed JSON") from exc
        raise ApiError("retry budget exhausted")

    @staticmethod
    def _data_object(payload: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ApiError(f"{endpoint}: expected data object")
        return payload["data"]

    @staticmethod
    def _data_list(payload: Any, endpoint: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ApiError(f"{endpoint}: expected data array")
        rows = payload["data"]
        if not all(isinstance(row, dict) for row in rows):
            raise ApiError(f"{endpoint}: malformed item")
        return rows

    def _paged_orders(self, endpoint: str) -> list[dict[str, Any]]:
        """Read every page or fail closed; never treat a truncated page as complete."""
        result: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(100):
            params = {"limit": "300"} if endpoint.endswith("/active") else {"limit": "100"}
            if cursor is not None:
                params["cursor"] = cursor
            payload = self.request("GET", endpoint, params=params)
            items = self._data_list(payload, endpoint)
            for item in items:
                venue_id = item.get("id")
                if not isinstance(venue_id, str) or not venue_id or venue_id in seen_ids:
                    raise ApiError(f"{endpoint}: invalid/duplicate order id")
                seen_ids.add(venue_id)
                result.append(item)
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                raise ApiError(f"{endpoint}: missing pagination metadata")
            cursor = metadata.get("next_cursor")
            if cursor is None or cursor == "":
                return result
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise ApiError(f"{endpoint}: invalid/repeated pagination cursor")
            seen_cursors.add(cursor)
        raise ApiError(f"{endpoint}: pagination limit exceeded")

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self._paged_orders("/1.0/orders/active")

    def get_historical_orders(self) -> list[dict[str, Any]]:
        """Recent history only (API default window); an absence is NOT proof of rejection."""
        return self._paged_orders("/1.0/orders/historical")

    def get_order(self, venue_order_id: str) -> dict[str, Any]:
        return self._data_object(self.request("GET", f"/1.0/orders/{venue_order_id}"), "get order")

    def get_fills(self, venue_order_id: str) -> list[dict[str, Any]]:
        return self._data_list(self.request("GET", f"/1.0/orders/fills/{venue_order_id}"), "fills")

    def get_pair_configuration(self) -> dict[str, Any]:
        payload = self.request("GET", "/1.0/configuration/pairs")
        if not isinstance(payload, dict) or not all(isinstance(v, dict) for v in payload.values()):
            raise ApiError("pair configuration must be a symbol-keyed object")
        return payload

    @staticmethod
    def _placement_result(payload: Any, client_order_id: str, *, replacement: bool = False) -> dict[str, Any]:
        """Validate immutable acknowledgement fields, never interpret ambiguity as rejection.

        PUT docs describe data as an object but show a list in their example.
        Only for PUT, accept a singleton array as well; reject empty/multi.
        """
        if not isinstance(payload, dict):
            raise UncertainMutationError("unexpected mutation acknowledgement")
        data = payload.get("data")
        if replacement and isinstance(data, list):
            if len(data) != 1:
                raise UncertainMutationError("replacement acknowledgement is not singular")
            data = data[0]
        if not isinstance(data, dict):
            raise UncertainMutationError("unexpected mutation acknowledgement data")
        venue_id = data.get("venue_order_id")
        returned_client_id = data.get("client_order_id")
        if not isinstance(venue_id, str) or not isinstance(returned_client_id, str):
            raise UncertainMutationError("missing order identity in acknowledgement")
        try:
            if str(UUID(venue_id)) != venue_id.lower() or str(UUID(returned_client_id)) != returned_client_id.lower():
                raise ValueError("noncanonical identity")
        except (ValueError, AttributeError) as exc:
            raise UncertainMutationError("invalid UUID in acknowledgement") from exc
        if returned_client_id != client_order_id or data.get("state") not in {
            "pending_new", "new", "partially_filled", "filled", "cancelled", "rejected"
        }:
            raise UncertainMutationError("incomplete/mismatched mutation acknowledgement")
        return data

    def place_order(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = self.request("POST", "/1.0/orders", body=payload, mutation=True)
        return self._placement_result(result, str(payload["client_order_id"]))

    def replace_order(self, venue_order_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = self.request("PUT", f"/1.0/orders/{venue_order_id}", body=payload, mutation=True)
        return self._placement_result(result, str(payload["client_order_id"]), replacement=True)

    def cancel_all_orders(self) -> Any:
        return self.request("DELETE", "/1.0/orders", mutation=True)
