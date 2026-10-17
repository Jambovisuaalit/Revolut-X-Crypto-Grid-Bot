"""Persistent read-only ETH/EUR poller. A host service is required to keep it running.

Only invokes PublicOnlyClient.get on the three existing allowlisted GET paths.
It NEVER imports or calls an order execution client, and NEVER starts shadow trading.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from decimal import Decimal
from typing import Callable

import requests

from .collector_store import CollectorStore
from .config import Config
from .grid import PairRules
from .public_readonly import (
    PublicOnlyClient, PAIR_PATH, BOOK_PATH, CANDLE_PATH, LIMIT, INTERVAL_MIN,
    INTERVAL_MS, normalize_book, parse_candles,
)

LOGGER = logging.getLogger(__name__)
D = Decimal
BOOK_POLL_SECONDS = 5
PAIR_REFRESH_MS = 6 * 60 * 60 * 1000
CANDLE_REFRESH_MS = 15 * 60 * 1000
GET_MIN_GAP_SECONDS = 1.1


class ContinuousReadOnlyCollector:
    """Single-process scheduling, with durable restarts and rate-limited GET calls."""

    def __init__(
        self, cfg: Config, db: Path,
        *,
        client: PublicOnlyClient | None = None,
        clock_ms: Callable[[], int] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        owner: str | None = None,
    ) -> None:
        if cfg["mode"] != "shadow" or cfg["symbol"] != "ETH-EUR" or cfg["region"] != "EEA":
            raise ValueError("READONLY_REQUIRES_ETH_EUR_SHADOW")
        self.cfg = cfg
        self.client = client or PublicOnlyClient()
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.monotonic = monotonic or time.monotonic
        self.sleep = sleep or time.sleep
        self.owner = owner or str(uuid.uuid4())
        self.store = CollectorStore(db, self.owner)
        self.next_request: float | None = None
        self.last_pair_refresh_ms = 0
        self.last_candle_refresh_ms = 0
        self.pair: PairRules | None = None
        self.pair_payload: dict | None = None
        self.rules_version_hash: str | None = None
        self.last_candle_time_ms: int | None = None

    def _get(self, path: str, params: dict) -> dict:
        """Enforce >=1.1 sec between *all* public endpoint requests."""
        now = self.monotonic()
        if self.next_request is not None and now < self.next_request:
            self.sleep(self.next_request - now)
        self.next_request = self.monotonic() + GET_MIN_GAP_SECONDS
        return self.client.get(path, params)

    def _pair_rules(self, now_ms: int, mid: Decimal) -> None:
        if (self.pair is not None
            and now_ms - self.last_pair_refresh_ms < PAIR_REFRESH_MS):
            return
        payload = self._get(PAIR_PATH, {"region": "EEA"})
        rules = PairRules.from_public_config(payload, resolved_region="EEA", book_mid=mid)
        snapshot = {
            "status": rules.status, "region": rules.region, "symbol": rules.symbol,
            "base_step": str(rules.base_step), "quote_step": str(rules.quote_step),
            "min_base_qty": str(rules.min_base_qty), "max_base_qty": str(rules.max_base_qty),
            "min_notional_eur": str(rules.min_notional),
        }
        self.rules_version_hash = self.store.record_pair(self.clock_ms(), snapshot, active=rules.active())
        self.pair_payload = payload
        self.pair = rules
        self.last_pair_refresh_ms = self.clock_ms()
        if not rules.active():
            raise ValueError("ETH_EUR_PAIR_NOT_ACTIVE")

    def _candles(self, now_ms: int) -> None:
        if (self.last_candle_time_ms is not None and
            now_ms - self.last_candle_refresh_ms < CANDLE_REFRESH_MS):
            return
        payload = self._get(CANDLE_PATH, {
            "interval": INTERVAL_MIN,
            "since": now_ms - 205*INTERVAL_MS,
            "until": now_ms,
            "region": "EEA",
        })
        candles = parse_candles(payload, self.clock_ms())
        self.store.record_candles(self.clock_ms(), candles)
        self.last_candle_time_ms = candles[-1]["close_ts_ms"]
        self.last_candle_refresh_ms = self.clock_ms()

    def poll(self) -> None:
        """Exactly one public book poll, with paired data and candle refresh gates.

        An invalid/absent candle or pair config creates a rejected observation.
        No last-known-good market data are silently relabeled current.
        """
        checked = self.clock_ms()
        try:
            raw = self._get(BOOK_PATH, {"region": "EEA", "limit": LIMIT})
            captured = self.clock_ms()
            event, summary = normalize_book(raw, captured, self.cfg, sequence=0)
            mid = (D(summary["best_bid_eur"]) + D(summary["best_ask_eur"])) / 2
            self._pair_rules(captured, mid)
            self._candles(captured)
            if self.store._info("pair_drift") == "true":
                raise ValueError("ETH_EUR_PAIR_RULES_DRIFTED")
            if self.last_candle_time_ms is None:
                raise ValueError("CANDLES_NOT_VERIFIED")
            if event["metadata"]["timestamp"] - self.last_candle_time_ms > 2*INTERVAL_MS:
                raise ValueError("CANDLES_TOO_OLD")
            self.store.record_book(self.clock_ms(), event["metadata"]["timestamp"],
                                   raw, summary, "")
        except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
            # An error is not treated as a valid observation or fill.
            reason = f"{type(exc).__name__}:{str(exc)[:110]}"
            LOGGER.warning("READONLY_BOOK_REJECTED %s", reason)
            observed = self.clock_ms()
            self.store.record_book(observed, None, None, None, reason)
            self.store.record_fault(self.clock_ms(), "POLL_REJECTED", reason)

    def run(self, *, max_polls: int = 0) -> int:
        """Run until SIGTERM/KeyboardInterrupt or optional *test-only* max_polls."""
        if type(max_polls) is not int or max_polls < 0:
            raise ValueError("max_polls must be a nonnegative integer")
        self.store.acquire(self.clock_ms())
        count = 0
        deadline = self.monotonic()
        try:
            while max_polls == 0 or count < max_polls:
                now = self.monotonic()
                if now < deadline:
                    self.sleep(deadline - now)
                self.poll()
                count += 1
                # Minimum 5 s poll spacing; no catch-up on missed slots.
                deadline = max(deadline + BOOK_POLL_SECONDS, self.monotonic() + 0.001)
        except KeyboardInterrupt:
            LOGGER.info("read-only collector stopped by operator")
        finally:
            self.store.release(self.clock_ms())
        return count

    def close(self) -> None:
        self.client.close()
        self.store.close()

    def __enter__(self) -> "ContinuousReadOnlyCollector":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
