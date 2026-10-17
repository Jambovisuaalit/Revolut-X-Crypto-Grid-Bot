"""Fail-closed, single-owner lifecycle around the EXISTING GridBotSupervisor.

Version 1 deliberately accepts ONLY offline synthetic input and an executor
whose live_trading is False. It cannot instantiate RevolutXClient, access
API credentials, place or cancel venue orders, or start a public data collector.
The authenticated read-only account adapter is a separate future work package.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import os
import time
import uuid
from typing import Callable, Mapping, Protocol

from bot_main import GridBotSupervisor
from grid_executor import GridExecutor

MAX_AGE_MS = 3_000
LEASE_MS = 15_000
SOURCE_KIND = "SYNTHETIC_OFFLINE_ONLY"


class SupervisorState(str, Enum):
    STOPPED = "STOPPED"
    RECONCILING = "RECONCILING"
    OFFLINE_MONITORING = "OFFLINE_MONITORING"
    BLOCKED = "BLOCKED"
    RISK_LOCKED = "RISK_LOCKED"


class SupervisorBlocked(RuntimeError):
    """Fail closed: operation needs manual review before any new run."""


@dataclass(frozen=True)
class PortfolioSample:
    """Explicit and timestamped synthetic balances and portfolio marks."""

    source_ms: int
    received_ms: int
    balances: Mapping[str, Decimal]
    marks: Mapping[str, Decimal]
    provenance: str = SOURCE_KIND


class SnapshotSource(Protocol):
    """Injectable *offline-only* source for deterministic tests."""

    def read(self) -> PortfolioSample:
        ...


class OfflineFixtureSource:
    """Locally generated sample. Contains no exchange/API client or network call."""

    def __init__(self, clock_ms: Callable[[], int], equity_eur: Decimal = Decimal("1000")) -> None:
        if not isinstance(equity_eur, Decimal) or not equity_eur.is_finite() or equity_eur <= 0:
            raise ValueError("offline equity must be positive finite Decimal")
        self.clock_ms, self.equity_eur = clock_ms, equity_eur

    def read(self) -> PortfolioSample:
        now = self.clock_ms()
        return PortfolioSample(source_ms=now, received_ms=now,
                               balances={"EUR": self.equity_eur}, marks={})


class SupervisorRuntime:
    """A bounded, local-only, fail-closed lifecycle with durable risk state."""

    def __init__(
        self, supervisor: GridBotSupervisor, source: SnapshotSource,
        *,
        clock_ms: Callable[[], int] | None = None,
        owner: str | None = None,
    ) -> None:
        if supervisor.executor.live_trading is not False:
            raise SupervisorBlocked("LIVE_EXECUTOR_FORBIDDEN")
        if os.getenv("LIVE_TRADING", "false").strip().lower() != "false":
            raise SupervisorBlocked("LIVE_ENV_FORBIDDEN")
        self.supervisor = supervisor
        self.executor: GridExecutor = supervisor.executor
        self.source = source
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.owner = owner or str(uuid.uuid4())
        if not self.owner or len(self.owner) > 128:
            raise ValueError("invalid runtime owner")
        self.state = SupervisorState.STOPPED
        self.started = False
        self.last_source_ms: int | None = None
        self.last_equity: Decimal | None = None
        self.ticks = 0

    def _value(self, key: str) -> str | None:
        return self.supervisor._metadata(key)

    def _set(self, key: str, value: str) -> None:
        self.supervisor._set_metadata(key, value)

    def _owned(self, now: int) -> None:
        if (self._value("supervisor_owner") != self.owner or
            int(self._value("supervisor_lease_until_ms") or "0") < now):
            self.state = SupervisorState.BLOCKED
            raise SupervisorBlocked("SUPERVISOR_LEASE_LOST")

    def _lease(self, now: int) -> None:
        self._set("supervisor_lease_until_ms", str(now + LEASE_MS))
        self._set("supervisor_heartbeat_ms", str(now))

    def _store_state(self, value: SupervisorState, *, reason: str = "NONE") -> None:
        now = self.clock_ms()
        with self.executor._write_transaction():
            self._owned(now)
            self._set("supervisor_state", value.value)
            self._set("supervisor_reason", reason)
            self._lease(now)
        self.state = value

    def _block(self, reason: str, *, risk: bool = False) -> None:
        value = SupervisorState.RISK_LOCKED if risk else SupervisorState.BLOCKED
        try:
            self._store_state(value, reason=reason)
        except Exception:
            # Never overwrite another process's lease or mask original fault.
            self.state = value
        self.started = False

    def start(self) -> None:
        """Acquire exclusive durable lease then reconcile BEFORE any monitoring."""
        if self.started:
            raise SupervisorBlocked("SUPERVISOR_ALREADY_STARTED")
        now = self.clock_ms()
        with self.executor._write_transaction():
            prev_owner = self._value("supervisor_owner")
            prev_until = int(self._value("supervisor_lease_until_ms") or "0")
            if prev_owner and prev_until >= now:
                raise SupervisorBlocked("SUPERVISOR_ALREADY_OWNED")
            if self._value("kill_switch_triggered") == "true":
                self.state = SupervisorState.RISK_LOCKED
                raise SupervisorBlocked("PERSISTENT_RISK_LOCK")
            # Incomplete previous start/stop is not assumed to be safe.
            old = self._value("supervisor_state")
            if old in (SupervisorState.BLOCKED.value, SupervisorState.RISK_LOCKED.value):
                raise SupervisorBlocked("PREVIOUS_SUPERVISOR_BLOCKED")
            self._set("supervisor_owner", self.owner)
            self._set("supervisor_state", SupervisorState.RECONCILING.value)
            self._set("supervisor_reason", "STARTUP")
            self._lease(now)
        self.state = SupervisorState.RECONCILING
        try:
            self.supervisor.start()
        except Exception:
            self._block("STARTUP_RECONCILIATION_FAILED")
            raise SupervisorBlocked("STARTUP_RECONCILIATION_FAILED") from None
        self._store_state(SupervisorState.OFFLINE_MONITORING, reason="SYNTHETIC_FIXTURE_ONLY")
        self.started = True

    def _validate(self, sample: PortfolioSample, now: int) -> None:
        if not isinstance(sample, PortfolioSample) or sample.provenance != SOURCE_KIND:
            raise SupervisorBlocked("NON_OFFLINE_SOURCE_FORBIDDEN")
        for n in (sample.source_ms, sample.received_ms):
            if type(n) is not int or n <= 0:
                raise SupervisorBlocked("INVALID_SAMPLE_CLOCK")
        if not sample.source_ms <= sample.received_ms <= now:
            raise SupervisorBlocked("INVALID_SAMPLE_TIMESTAMP_ORDER")
        if now - sample.source_ms > MAX_AGE_MS or now - sample.received_ms > MAX_AGE_MS:
            raise SupervisorBlocked("STALE_PORTFOLIO_SAMPLE")
        if self.last_source_ms is not None and sample.source_ms < self.last_source_ms:
            raise SupervisorBlocked("BACKWARDS_SAMPLE_TIMESTAMP")
        if not sample.balances or not isinstance(sample.balances, Mapping):
            raise SupervisorBlocked("MISSING_BALANCES")
        if not isinstance(sample.marks, Mapping):
            raise SupervisorBlocked("MISSING_MARKS")
        for currency, amount in sample.balances.items():
            if (not isinstance(currency, str) or not isinstance(amount, Decimal)
                or not amount.is_finite() or amount < 0):
                raise SupervisorBlocked("INVALID_BALANCE")
        for symbol, price in sample.marks.items():
            if (not isinstance(symbol, str) or not isinstance(price, Decimal)
                or not price.is_finite() or price <= 0):
                raise SupervisorBlocked("INVALID_MARK")

    def tick(self) -> Decimal:
        """One read-only synthetic observation and the EXISTING 3% drawdown guard."""
        if not self.started or self.state != SupervisorState.OFFLINE_MONITORING:
            raise SupervisorBlocked("SUPERVISOR_NOT_MONITORING")
        now = self.clock_ms()
        try:
            with self.executor._write_transaction():
                self._owned(now)
                self._lease(now)
            sample = self.source.read()
            self._validate(sample, self.clock_ms())
            equity = self.supervisor.mark_to_market(sample.balances, sample.marks)
            if self.supervisor.check_drawdown(equity):
                self._block("HARD_DRAWDOWN_3PCT", risk=True)
                raise SupervisorBlocked("HARD_DRAWDOWN_3PCT")
            with self.executor._write_transaction():
                self._owned(self.clock_ms())
                if self._value("kill_switch_triggered") == "true":
                    raise SupervisorBlocked("PERSISTENT_RISK_LOCK")
                self._set("supervisor_last_sample_ms", str(sample.source_ms))
                self._set("supervisor_last_equity_eur", str(equity))
                self._lease(self.clock_ms())
            self.last_source_ms = sample.source_ms
            self.last_equity = equity
            self.ticks += 1
            return equity
        except SupervisorBlocked as exc:
            if self.state not in (SupervisorState.BLOCKED, SupervisorState.RISK_LOCKED):
                self._block(str(exc), risk=str(exc) in ("PERSISTENT_RISK_LOCK", "HARD_DRAWDOWN_3PCT"))
            raise
        except Exception:
            self._block("INVALID_SAMPLE_OR_RUNTIME_FAILURE")
            raise SupervisorBlocked("INVALID_SAMPLE_OR_RUNTIME_FAILURE") from None

    def stop(self) -> None:
        """Release owned lease, retaining BLOCKED/RISK_LOCKED after failures."""
        now = self.clock_ms()
        with self.executor._write_transaction():
            if self._value("supervisor_owner") == self.owner:
                if self.state in (SupervisorState.OFFLINE_MONITORING, SupervisorState.RECONCILING):
                    self._set("supervisor_state", SupervisorState.STOPPED.value)
                    self._set("supervisor_reason", "OPERATOR_STOP")
                    self.state = SupervisorState.STOPPED
                self._set("supervisor_owner", "")
                self._set("supervisor_lease_until_ms", "0")
                self._set("supervisor_heartbeat_ms", str(now))
        self.started = False

    def run_bounded(self, ticks: int, *, poll_interval_seconds: float = 0,
                    sleeper: Callable[[float], None] = time.sleep) -> dict:
        """Explicit, bounded offline fixture only; never runs a background daemon."""
        if type(ticks) is not int or not 1 <= ticks <= 1000:
            raise ValueError("ticks must be in [1,1000]")
        if not 0 <= poll_interval_seconds <= 10:
            raise ValueError("invalid poll interval")
        result = "FAIL"
        try:
            self.start()
            for index in range(ticks):
                if index:
                    sleeper(poll_interval_seconds)
                self.tick()
            result = "PASS"
        except (SupervisorBlocked, KeyboardInterrupt):
            result = "BLOCKED"
        finally:
            self.stop()
        return {
            "schema": "offline-supervisor-v1",
            "result": result,
            "state": self.state.value,
            "ticks_completed": self.ticks,
            "last_equity_eur": str(self.last_equity) if self.last_equity is not None else None,
            "source": SOURCE_KIND,
            "exchange_orders_sent": 0,
            "authenticated_account_test": "NOT_RUN",
            "live_trading": "DISABLED",
        }
