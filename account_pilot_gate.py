"""Bounded authenticated GET-only pilot harness — MOCK TESTS ONLY.

This module has no execution CLI, no credential reader and never constructs
a network transport. A future real-account pilot requires a separate reviewed
and signed entrypoint, consent, and an explicitly approved connection.

The budget guards every attempted HTTP GET including 429/transport errors.
Successful response count is NOT used for rate limiting.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, parse_qs

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pilot_budget_store import DurablePilotLedger, PilotLedgerError

from account_readonly import (
    AccountReadError, AccountSnapshot, AccountTransport,
    AuthenticatedAccountReader, BALANCES, ACTIVE, HISTORY, ReadLimits,
    BASE_URL, _is_order_detail, _is_fills_detail,
)


class PilotGateError(AccountReadError):
    """Safety gate failure, deliberately without credentials or order details."""


@dataclass(frozen=True)
class PilotBounds:
    """No more than 25 total GET attempts, 10 pages/list, zero 429 retries."""

    max_attempts: int = 25
    max_pages_per_list: int = 10

    def __post_init__(self) -> None:
        if (type(self.max_attempts) is not int or
                not 1 <= self.max_attempts <= 25 or
                type(self.max_pages_per_list) is not int or
                not 1 <= self.max_pages_per_list <= 10):
            raise ValueError("PILOT_LIMITS_OUT_OF_RANGE")


class GuardedGetTransport:
    """Only GET exists. Authorizes immutable host + path, bounds attempts.

    The wrapped transport must be explicitly provided, never created here.
    Refuses reuse after transport error, budget exhaustion, or manual halt.
    """

    def __init__(self, inner: AccountTransport, *, max_attempts: int = 25,
                 ledger: DurablePilotLedger | None = None) -> None:
        if inner is None or not callable(getattr(inner, "get", None)):
            raise TypeError("EXPLICIT_GET_TRANSPORT_REQUIRED")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 25:
            raise ValueError("GET_BUDGET_INVALID")
        self._inner = inner
        self._ledger = ledger
        self._max_attempts = max_attempts
        self._lock = threading.Lock()
        self._attempts = 0
        self._blocked = False

    @property
    def attempts(self) -> int:
        with self._lock:
            return self._attempts

    @property
    def blocked(self) -> bool:
        with self._lock:
            return self._blocked

    def halt(self) -> None:
        with self._lock:
            self._blocked = True
            if self._ledger is not None:
                try:
                    self._ledger.seal()
                except Exception:
                    raise PilotGateError("PILOT_LEDGER_SEAL_FAILED") from None

    @staticmethod
    def _check_url(url: str) -> None:
        # urllib's parser is intentionally used instead of prefix matching:
        # no subdomain confusion, encoded slash, remote host or fragment.
        if not isinstance(url, str):
            raise PilotGateError("GET_URL_NOT_ALLOWED")
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != "revx.revolut.com"
                or parts.username or parts.password or parts.fragment):
            raise PilotGateError("GET_HOST_NOT_ALLOWED")
        prefix = urlsplit(BASE_URL).path
        if not parts.path.startswith(prefix + "/"):
            raise PilotGateError("GET_PATH_NOT_ALLOWED")
        path = parts.path[len(prefix):]
        allowed = (path in (BALANCES, ACTIVE, HISTORY)
                   or _is_order_detail(path) or _is_fills_detail(path))
        if not allowed or "%" in path or "\\" in path:
            raise PilotGateError("GET_ENDPOINT_NOT_ALLOWED")
        if path in (ACTIVE, HISTORY):
            query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
            if "limit" not in query or set(query) - {"limit", "cursor"}:
                raise PilotGateError("GET_QUERY_NOT_ALLOWED")
            if len(query["limit"]) != 1 or query["limit"][0] != "100":
                raise PilotGateError("GET_LIMIT_NOT_ALLOWED")
            if "cursor" in query and (len(query["cursor"]) != 1 or not query["cursor"][0]):
                raise PilotGateError("GET_CURSOR_NOT_ALLOWED")
        elif parts.query:
            raise PilotGateError("GET_QUERY_NOT_ALLOWED")

    def get(self, url: str, *, headers: Mapping[str, str], timeout: int,
            allow_redirects: bool, params: None = None, stream: bool = True) -> Any:
        with self._lock:
            if self._blocked:
                raise PilotGateError("PILOT_SESSION_BLOCKED")
            # Any misuse of transport is an irreversible per-session halt.
            try:
                self._check_url(url)
                if (allow_redirects is not False or params is not None or
                        stream is not True or type(timeout) is not int or
                        not 1 <= timeout <= 10):
                    raise PilotGateError("GET_TRANSPORT_OPTIONS_REJECTED")
                if (not isinstance(headers, Mapping)
                        or set(headers) != {
                            "X-Revx-API-Key", "X-Revx-Timestamp",
                            "X-Revx-Signature", "Accept", "User-Agent",
                        }):
                    raise PilotGateError("GET_SIGNING_HEADERS_REJECTED")
            except Exception:
                self._blocked = True
                raise PilotGateError("PILOT_REQUEST_REJECTED") from None
            if self._attempts >= self._max_attempts:
                self._blocked = True
                raise PilotGateError("PILOT_GET_BUDGET_EXHAUSTED")
            if self._ledger is not None:
                try:
                    self._ledger.reserve_before_get()
                except Exception:
                    self._blocked = True
                    raise PilotGateError("PILOT_DURABLE_BUDGET_BLOCKED") from None
            self._attempts += 1  # before network, even for timeout/429
            try:
                # Serialize the *entire* GET call. This guarantees that two
                # threads cannot circumvent the budget by racing.
                return self._inner.get(url, headers=headers, timeout=timeout,
                                       allow_redirects=False, params=None, stream=True)
            except BaseException:
                self._blocked = True
                raise PilotGateError("PILOT_GET_TRANSPORT_FAILED") from None


class BoundedPilotSession:
    """One-shot all-or-nothing snapshot with a redacted, conservative report."""

    def __init__(
        self,
        api_key: str,
        private_key: Ed25519PrivateKey,
        *,
        transport: AccountTransport,
        bounds: PilotBounds = PilotBounds(),
        clock_ms: Callable[[], int] | None = None,
        ledger: DurablePilotLedger | None = None,
    ) -> None:
        if not isinstance(bounds, PilotBounds):
            raise TypeError("PILOT_BOUNDS_INVALID")
        self.guard = GuardedGetTransport(transport, max_attempts=bounds.max_attempts,
                                         ledger=ledger)
        self.reader = AuthenticatedAccountReader(
            api_key, private_key, transport=self.guard, clock_ms=clock_ms,
            read_limits=ReadLimits(max_pages_per_list=bounds.max_pages_per_list,
                                   max_rate_retries=0),
        )
        self._used = False
        self._status = "NOT_STARTED"

    @property
    def status(self) -> str:
        return self._status

    def run_once(self) -> AccountSnapshot:
        if self._used or self.guard.blocked:
            raise PilotGateError("PILOT_SESSION_CANNOT_RESTART")
        self._used = True
        try:
            result = self.reader.snapshot()
            self.guard.halt()  # budget must seal before success is reported
            self._status = "SCHEMA_PARSED_NOT_RECONCILED"
            return result
        except BaseException as exc:
            self._status = "BLOCKED"
            try:
                self.guard.halt()
            except Exception:
                pass  # already blocked; cannot approve a failed seal
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise PilotGateError("PILOT_SNAPSHOT_REJECTED") from None

    def report(self) -> dict[str, Any]:
        """Do not export balances, order IDs, tokens or user identifiers."""
        return {
            "schema": "bounded-account-readonly-pilot-v1",
            "status": self._status,
            "get_attempts": self.guard.attempts,
            "http_methods": ["GET"],
            "mutations_sent": 0,
            "orders_placed": 0,
            "owner_ui_comparison": "NOT_PERFORMED",
            "account_scope_verified": False,
            "reconciled_for_trading": False,
            "live_trading_approval": "NO_GO",
        }
