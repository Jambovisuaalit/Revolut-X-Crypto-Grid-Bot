"""Authenticated Revolut X REST client with bounded 429 backoff."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class ApiError(RuntimeError):
    """Raised when an API response cannot safely be acted upon."""


class UncertainMutationError(ApiError):
    """Raised when a mutation may have reached the venue but was not acknowledged."""


class RevolutXClient:
    """Small fail-closed client implementing Revolut X request signing."""

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
        """Initialize the client; credentials are retained but never logged."""
        self.api_key = api_key
        self.private_key = private_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_429_retries = max_429_retries
        self.session = session or requests.Session()
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.sleeper = sleeper

    @classmethod
    def from_pem(cls, api_key: str, path: str, **kwargs: Any) -> "RevolutXClient":
        """Load an unencrypted Ed25519 PEM private key from disk."""
        loaded = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError("private key must be Ed25519")
        return cls(api_key, loaded, **kwargs)

    @staticmethod
    def _body_text(body: Mapping[str, Any] | None) -> str:
        return "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    def signed_headers(self, method: str, path: str, query: str = "", body_text: str = "") -> dict[str, str]:
        """Create authentication headers over timestamp+method+path+query+body."""
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
        """Send a request, retrying only explicit HTTP 429 responses."""
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
                if attempt == self.max_429_retries:
                    raise ApiError("rate-limit retry budget exhausted")
                raw_delay = response.headers.get("Retry-After")
                try:
                    delay_ms = max(0, int(raw_delay or "0"))
                except ValueError as exc:
                    raise ApiError("malformed Retry-After header") from exc
                self.sleeper(delay_ms / 1000)
                continue
            if response.status_code in {401, 403}:
                raise ApiError("authentication or authorization rejected")
            if not 200 <= response.status_code < 300:
                raise ApiError(f"API returned HTTP {response.status_code}")
            if response.status_code == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise ApiError("API returned malformed JSON") from exc
        raise ApiError("unreachable retry state")

    def get_open_orders(self) -> Any:
        """Fetch venue open orders."""
        return self.request("GET", "/1.0/orders", params={"status": "open"})

    def get_fills(self, venue_order_id: str) -> Any:
        """Fetch fills and fees for one venue order."""
        return self.request("GET", f"/1.0/orders/fills/{venue_order_id}")

    def place_order(self, payload: Mapping[str, Any]) -> Any:
        """Place a new order without retrying ambiguous transport failures."""
        return self.request("POST", "/1.0/orders", body=payload, mutation=True)

    def replace_order(self, venue_order_id: str, payload: Mapping[str, Any]) -> Any:
        """Atomically replace an existing venue order."""
        return self.request("PUT", f"/1.0/orders/{venue_order_id}", body=payload, mutation=True)

    def cancel_all_orders(self) -> Any:
        """Cancel every open order at the venue."""
        return self.request("DELETE", "/1.0/orders", mutation=True)

