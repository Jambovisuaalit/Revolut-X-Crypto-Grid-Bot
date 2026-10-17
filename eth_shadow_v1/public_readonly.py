"""Bounded, unauthenticated, read-only Revolut X ETH/EUR public market capture.

No trading client, credentials, signing, account endpoint, user-defined URL,
HTTP mutation, automatic market replay, or background loop exists here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import time
from typing import Any

import requests

from .config import Config, number
from .grid import PairRules
from .market import indicators, parse_book

ROOT = "https://revx.revolut.com"
PAIR_PATH = "/api/1.0/public/configuration/pairs"
BOOK_PATH = "/api/2.0/public/order-book/ETH-EUR"
CANDLE_PATH = "/api/1.0/public/candles/ETH-EUR"
APPROVED_PATHS = frozenset((PAIR_PATH, BOOK_PATH, CANDLE_PATH))
LIMIT = 50
INTERVAL_MIN = 15
INTERVAL_MS = INTERVAL_MIN * 60000
MAX_BYTES = 2 * 1024 * 1024
D = Decimal


def timestamp_ms() -> int:
    return time.time_ns() // 1_000_000


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class PublicOnlyClient:
    """Allowlisted requests Session; HTTPS GET only; no redirects, no auth, no retry."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        # Avoid ~/.netrc, credentials, local proxy injection and ambient auth.
        self.session.trust_env = False

    def get(self, path: str, params: dict[str, str | int]) -> dict:
        """Read one bounded JSON response from an exact documented public path."""
        if path not in APPROVED_PATHS:
            raise ValueError("NONPUBLIC_ENDPOINT_REFUSED")
        fixed = {
            PAIR_PATH: {"region": "EEA"},
            BOOK_PATH: {"limit": LIMIT, "region": "EEA"},
        }
        if path in fixed and params != fixed[path]:
            raise ValueError("PUBLIC_QUERY_NOT_ALLOWLISTED")
        if path == CANDLE_PATH and (
            set(params) != {"interval", "since", "until", "region"}
            or params["interval"] != INTERVAL_MIN
            or params["region"] != "EEA"
            or type(params["since"]) is not int or type(params["until"]) is not int
            or params["until"] <= params["since"]
            or params["until"] - params["since"] > 210 * INTERVAL_MS
        ):
            raise ValueError("PUBLIC_CANDLE_QUERY_NOT_ALLOWLISTED")
        response = self.session.get(
            ROOT + path, params=params,
            headers={"Accept": "application/json", "User-Agent": "RevX-ETH-EUR-Shadow-ReadOnly/1.0"},
            timeout=8, allow_redirects=False,
        )
        if response.status_code != 200:
            raise ValueError(f"PUBLIC_HTTP_{response.status_code}")
        if len(response.content) > MAX_BYTES:
            raise ValueError("PUBLIC_RESPONSE_TOO_LARGE")
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("PUBLIC_RESPONSE_NOT_JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("PUBLIC_RESPONSE_NOT_OBJECT")
        return data

    def close(self) -> None:
        self.session.close()


def parse_candles(payload: dict, source_ms: int) -> list[dict]:
    """Map documented candle start-ms to CLOSED 15-minute bars; never use current bar."""
    if payload.get("metadata", {}).get("region") != "EEA":
        raise ValueError("CANDLE_REGION_NOT_EEA")
    meta_time = payload["metadata"].get("timestamp")
    if type(meta_time) is not int or abs(meta_time - source_ms) > 120000:
        raise ValueError("CANDLE_METADATA_TIMESTAMP_UNVERIFIED")
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) < 200:
        raise ValueError("CANDLE_HISTORY_UNDER_200")
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("start")) is not int:
            raise ValueError("CANDLE_BAD_START")
        start = row["start"]
        # OHLCV is quoted as decimal strings in the documented public payload.
        o, h, l, c, v = (number(row.get(k), k) for k in
                         ("open", "high", "low", "close", "volume"))
        if o <= 0 or l <= 0 or h <= 0 or c <= 0 or v < 0 or l > min(o, c) or h < max(o, c):
            raise ValueError("CANDLE_BAD_OHLCV")
        close = start + INTERVAL_MS
        if close <= source_ms:
            normalized.append({
                "close_ts_ms": close, "high": str(h), "low": str(l),
                "close": str(c), "volume": str(v), "closed": True,
            })
    normalized.sort(key=lambda row: row["close_ts_ms"])
    if len({row["close_ts_ms"] for row in normalized}) != len(normalized):
        raise ValueError("CANDLE_DUPLICATE_START")
    if len(normalized) < 200:
        raise ValueError("CANDLE_CLOSED_HISTORY_UNDER_200")
    result = normalized[-200:]
    if any(result[i+1]["close_ts_ms"] - result[i]["close_ts_ms"] != INTERVAL_MS
           for i in range(len(result)-1)):
        raise ValueError("CANDLE_GAP")
    if source_ms - result[-1]["close_ts_ms"] > 2*INTERVAL_MS:
        raise ValueError("CANDLE_TOO_OLD")
    return result


def normalize_book(raw: dict, observed_ms: int, cfg: Config,
                   sequence: int) -> tuple[dict, dict]:
    """Normalize v2 arrays of {price, quantity, count} without inventing fills."""
    if not isinstance(raw.get("metadata"), dict) or not isinstance(raw.get("data"), dict):
        raise ValueError("BOOK_ENVELOPE_INVALID")
    meta = raw["metadata"]
    levels = {}
    for side in ("bids", "asks"):
        rows = raw["data"].get(side)
        if not isinstance(rows, list) or not rows or len(rows) > LIMIT:
            raise ValueError("BOOK_LEVELS_INVALID")
        parsed = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("BOOK_ROW_NOT_OBJECT")
            p, q = number(row.get("price"), "price"), number(row.get("quantity"), "quantity")
            if p <= 0 or q <= 0:
                raise ValueError("BOOK_NONPOSITIVE")
            parsed.append([str(p), str(q)])
        levels[side] = parsed
    event = {
        "event_id": f"revx-public-{sequence:03d}-{meta.get('timestamp')}",
        "observed_ms": observed_ms,
        "metadata": {"region": meta.get("region"), "timestamp": meta.get("timestamp")},
        "symbol": "ETH-EUR",
        "bids": levels["bids"], "asks": levels["asks"],
    }
    book = parse_book(event, cfg)  # freshness, region, crossing, ordering, depth
    summary = {
        "source_utc_ms": book.source_ms, "observed_utc_ms": observed_ms,
        "age_ms": observed_ms-book.source_ms,
        "best_bid_eur": str(book.bid), "best_ask_eur": str(book.ask),
        "spread_pct": str(book.spread * 100),
        "bid_depth_eur": str(book.bid_depth_eur),
        "ask_depth_eur": str(book.ask_depth_eur),
        "book_sha256": _hash(raw),
        "levels_bid": len(levels["bids"]), "levels_ask": len(levels["asks"]),
    }
    return event, summary


def capture_once(*, cfg: Config, samples: int = 3,
                 client: PublicOnlyClient | None = None,
                 clock=timestamp_ms, pause=time.sleep) -> tuple[dict, dict]:
    """Capture at most 3 real observations and current pair schema in one invocation.

    Never starts the shadow strategy, even if all data is valid.
    Errors retain partial evidence with unambiguous blocked status.
    """
    if type(samples) is not int or samples not in (1, 2, 3):
        raise ValueError("samples must be 1..3")
    client = client or PublicOnlyClient()
    capture: dict[str, Any] = {
        "schema": "revx-eth-eur-public-capture-v1",
        "symbol": "ETH-EUR", "region_requested": "EEA",
        "mode": "READ_ONLY_CAPTURE", "synthetic": False,
        "config_hash": cfg.sha256, "pair_configuration": None,
        "events": [], "books": [], "candles": None,
        "errors": [], "sample_count_requested": samples,
        "exchange_mutations": 0, "forward_shadow_started": False,
    }
    try:
        pair_response = client.get(PAIR_PATH, {"region": "EEA"})
        pair = pair_response.get("ETH/EUR")
        if not isinstance(pair, dict):
            raise ValueError("PUBLIC_ETH_EUR_PAIR_ABSENT")
        capture["pair_configuration"] = {"ETH/EUR": pair}
        capture["pair_response_sha256"] = _hash(pair_response)
    except (ValueError, requests.RequestException) as exc:
        capture["errors"].append(f"PAIR:{type(exc).__name__}:{str(exc)[:80]}")
    # Public documentation: each endpoint has <=1 request per second.
    pause(1.25)
    first_source_ms = None
    for index in range(samples):
        if index > 0:
            pause(5.0)
        try:
            raw = client.get(BOOK_PATH, {"limit": LIMIT, "region": "EEA"})
            observed = clock()
            event, summary = normalize_book(raw, observed, cfg, index)
            if capture["events"] and event["metadata"]["timestamp"] <= capture["events"][-1]["metadata"]["timestamp"]:
                raise ValueError("BOOK_NONMONOTONIC_SOURCE")
            if first_source_ms is None:
                first_source_ms = event["metadata"]["timestamp"]
            capture["events"].append(event)
            capture["books"].append(summary)
            if capture["pair_configuration"] is not None:
                mid = (D(summary["best_bid_eur"]) + D(summary["best_ask_eur"])) / 2
                rules = PairRules.from_public_config(
                    capture["pair_configuration"], resolved_region="EEA", book_mid=mid)
                if not rules.active():
                    capture["errors"].append("PAIR:PUBLIC_PAIR_NOT_ACTIVE")
                capture["pair_rules"] = {
                    "symbol": rules.symbol, "region": rules.region, "status": rules.status,
                    "base_step": str(rules.base_step), "quote_step": str(rules.quote_step),
                    "min_base_qty": str(rules.min_base_qty), "max_base_qty": str(rules.max_base_qty),
                    "min_notional_eur": str(rules.min_notional),
                    "source": "revolut_public_configuration",
                }
        except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
            capture["errors"].append(f"BOOK_{index}:{type(exc).__name__}:{str(exc)[:80]}")
            continue
        # Candles fetched once and capped. Their 'until' is BEFORE first book.
        if index == 0:
            pause(1.25)
            try:
                source_ms = first_source_ms
                rows = client.get(CANDLE_PATH, {
                    "interval": INTERVAL_MIN,
                    "since": source_ms - 205*INTERVAL_MS,
                    "until": source_ms,
                    "region": "EEA",
                })
                candle_data = parse_candles(rows, source_ms)
                capture["candles"] = candle_data
                capture["candle_response_sha256"] = _hash(rows)
                # A closed-only indicator proof, no trading decisions.
                synthetic_event = dict(event, candles=candle_data)
                indicators(synthetic_event, parse_book(event, cfg))
            except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
                capture["errors"].append(f"CANDLES:{type(exc).__name__}:{str(exc)[:80]}")
    # Each individual book is preserved regardless of eventual strategy eligibility.
    if capture["candles"]:
        for event in capture["events"]:
            event["candles"] = capture["candles"]
    complete = (len(capture["events"]) == samples and
                capture.get("pair_rules", {}).get("status") == "active" and
                capture["candles"] is not None and not capture["errors"])
    report = {
        "status": "READY_FOR_OFFLINE_INSPECTION" if complete else "BLOCKED",
        "mode": "READ_ONLY_CAPTURE", "pair": "ETH/EUR", "region": "EEA",
        "collected_order_books": len(capture["events"]),
        "requested_order_books": samples,
        "pair_rules_verified_from_live_public_api": bool(capture.get("pair_rules")),
        "closed_15m_candles": 0 if capture["candles"] is None else len(capture["candles"]),
        "errors": capture["errors"],
        "exchange_mutation_requests": 0, "live_config_changed": False,
        "forward_shadow_30d": "NOT_STARTED", "economic_approval": "NO_GO",
        "raw_provenance": "LIVE_PUBLIC_GET" if capture["events"] else "NO_VERIFIED_BOOK",
    }
    capture["capture_sha256"] = _hash(capture)
    report["capture_sha256"] = capture["capture_sha256"]
    return capture, report
