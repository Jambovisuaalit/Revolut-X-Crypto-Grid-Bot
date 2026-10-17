"""Offline contract tests for Revolut X public ETH/EUR data adapter."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest
from urllib.parse import urlparse

from eth_shadow_v1.config import Config
from eth_shadow_v1.engine import fresh_state, transition
from eth_shadow_v1.grid import PairRules
from eth_shadow_v1.public_readonly import (
    PAIR_PATH, BOOK_PATH, CANDLE_PATH, PublicOnlyClient,
    capture_once, normalize_book, parse_candles,
)
from scripts.run_eth_eur_shadow import CONFIG


BASE = 1792195500000  # test-only UNIX ms, not a live observation
BAR = 900000


def pair() -> dict:
    return {"ETH/EUR": {
        "base": "ETH", "quote": "EUR",
        "base_step": "0.0000001", "quote_step": "0.01",
        "min_order_size": "0.00001", "max_order_size": "9000",
        "min_order_size_quote": "0.01", "status": "active",
    }}


def book(index: int = 0) -> dict:
    return {
        "metadata": {"region": "EEA", "timestamp": BASE + index*5000},
        "data": {
            "bids": [{"price": "2999.75", "quantity": "1.0", "count": 2}],
            "asks": [{"price": "3000.25", "quantity": "1.0", "count": 3}],
        }
    }


def candles() -> dict:
    # Closed candle sequence for the initial book source; the 201st is open.
    last_closed = (BASE//BAR)*BAR
    data = []
    for i in range(201):
        data.append({
            "start": last_closed - (200-i)*BAR,
            "open": "3000", "high": "3003", "low": "2997",
            "close": "3000", "volume": "10.5",
        })
    return {"metadata": {"region": "EEA", "timestamp": BASE}, "data": data}


class Response:
    def __init__(self, data: dict, status: int = 200):
        self.status_code = status
        self._data = data
        self.content = json.dumps(data).encode()
    def json(self) -> dict:
        return self._data


class Session:
    def __init__(self, *, pairs=None, books=None, bars=None, status=200):
        self.calls = []
        self.trust_env = True
        self._pairs = pair() if pairs is None else pairs
        self._books = list(books if books is not None else [book(i) for i in range(3)])
        self._bars = candles() if bars is None else bars
        self.status = status
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        path = urlparse(url).path
        if path == PAIR_PATH:
            return Response(self._pairs, self.status)
        if path == BOOK_PATH:
            return Response(self._books.pop(0), self.status)
        if path == CANDLE_PATH:
            return Response(self._bars, self.status)
        raise AssertionError("unexpected URL")
    def close(self):
        pass


class ReadOnlyPublicAdapterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(CONFIG)

    def client(self, session: Session) -> PublicOnlyClient:
        return PublicOnlyClient(session)

    def test_public_01_three_books_and_actual_pair_fields(self):
        fake = Session()
        clock_values = iter(BASE + 100 + i*5000 for i in range(3))
        capture, report = capture_once(
            cfg=self.cfg, client=self.client(fake), samples=3,
            clock=lambda: next(clock_values), pause=lambda _: None,
        )
        self.assertEqual(report["status"], "READY_FOR_OFFLINE_INSPECTION")
        self.assertEqual(report["collected_order_books"], 3)
        self.assertEqual(report["pair_rules_verified_from_live_public_api"], True)
        self.assertEqual(report["closed_15m_candles"], 200)
        self.assertEqual(report["exchange_mutation_requests"], 0)
        self.assertEqual(capture["pair_rules"]["base_step"], "1E-7")
        self.assertEqual(capture["pair_rules"]["min_base_qty"], "0.00001")
        self.assertEqual(capture["events"][0]["bids"], [["2999.75", "1.0"]])
        self.assertEqual(len(capture["events"][1]["candles"]), 200)
        self.assertEqual(len(fake.calls), 5)
        for url, kwargs in fake.calls:
            self.assertEqual(urlparse(url).netloc, "revx.revolut.com")
            self.assertIn(urlparse(url).path, (PAIR_PATH, BOOK_PATH, CANDLE_PATH))
            self.assertFalse(kwargs["allow_redirects"])
            self.assertNotIn("Authorization", kwargs["headers"])
            self.assertNotIn("X-Revx-API-Key", kwargs["headers"])
        self.assertFalse(fake.trust_env)

    def test_public_02_refuses_non_public_paths_and_query_override(self):
        fake = Session()
        client = self.client(fake)
        with self.assertRaisesRegex(ValueError, "NONPUBLIC"):
            client.get("/api/1.0/orders", {"region": "EEA"})
        with self.assertRaisesRegex(ValueError, "ALLOWLISTED"):
            client.get(BOOK_PATH, {"limit": 999, "region": "EEA"})
        with self.assertRaisesRegex(ValueError, "ALLOWLISTED"):
            client.get(PAIR_PATH, {"region": "UK"})
        self.assertEqual(fake.calls, [])

    def test_public_03_wrong_region_or_stale_book_fails_closed(self):
        raw = book()
        raw["metadata"]["region"] = "UK"
        with self.assertRaises(ValueError):
            normalize_book(raw, BASE+100, self.cfg, 0)
        with self.assertRaises(ValueError):
            normalize_book(book(), BASE+3500, self.cfg, 0)

    def test_public_04_pair_rules_require_live_public_schema(self):
        pair_rules = PairRules.from_public_config(
            pair(), resolved_region="EEA", book_mid=Decimal("3000"))
        self.assertTrue(pair_rules.active())
        self.assertEqual(pair_rules.base_step, Decimal("0.0000001"))
        self.assertEqual(pair_rules.min_base_qty, Decimal("0.00001"))
        self.assertEqual(pair_rules.max_base_qty, Decimal("9000"))
        self.assertEqual(pair_rules.min_notional, Decimal("0.01"))
        with self.assertRaises(ValueError):
            PairRules.from_public_config(pair(), resolved_region="UK", book_mid=Decimal("3000"))
        with self.assertRaises(ValueError):
            PairRules.from_public_config({"ETH/EUR": {"base": "BTC"}}, resolved_region="EEA", book_mid=Decimal("3000"))
        poor = pair()
        poor["ETH/EUR"]["base_step"] = "NaN"
        with self.assertRaises(ValueError):
            PairRules.from_public_config(poor, resolved_region="EEA", book_mid=Decimal("3000"))

    def test_public_05_inactive_pair_report_remains_blocked(self):
        value = pair()
        value["ETH/EUR"]["status"] = "inactive"
        report = capture_once(cfg=self.cfg, samples=1, client=self.client(Session(pairs=value)),
                              clock=lambda: BASE+100, pause=lambda _: None)[1]
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["exchange_mutation_requests"], 0)

    def test_public_06_http_429_has_no_unsafe_retry(self):
        fake = Session(status=429)
        _, report = capture_once(cfg=self.cfg, samples=1, client=self.client(fake),
                                 clock=lambda: BASE+100, pause=lambda _: None)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(len(fake.calls), 2)  # one pairs GET + one book GET
        self.assertTrue(any("PUBLIC_HTTP_429" in item for item in report["errors"]))

    def test_public_07_missing_candles_never_fabricated(self):
        bad = candles()
        bad["data"] = bad["data"][:20]
        capture, report = capture_once(cfg=self.cfg, samples=1,
                                       client=self.client(Session(bars=bad)),
                                       clock=lambda: BASE+100, pause=lambda _: None)
        self.assertEqual(len(capture["events"]), 1)
        self.assertIsNone(capture["candles"])
        self.assertEqual(report["closed_15m_candles"], 0)
        self.assertEqual(report["status"], "BLOCKED")

    def test_public_08_partial_book_archive_when_pair_absent(self):
        captured, report = capture_once(cfg=self.cfg, samples=1,
                                        client=self.client(Session(pairs={})),
                                        clock=lambda: BASE+100, pause=lambda _: None)
        self.assertEqual(len(captured["events"]), 1)
        self.assertIsNone(captured["pair_configuration"])
        self.assertEqual(report["status"], "BLOCKED")

    def test_public_09_no_duplicate_or_future_candle(self):
        data = candles()
        parsed = parse_candles(data, BASE)
        self.assertEqual(len(parsed), 200)
        self.assertLessEqual(parsed[-1]["close_ts_ms"], BASE)
        data["data"][1]["start"] = data["data"][0]["start"]
        with self.assertRaises(ValueError):
            parse_candles(data, BASE)

    def test_public_10_public_min_qty_blocks_virtual_fill(self):
        pair_payload = pair()
        pair_payload["ETH/EUR"]["min_order_size"] = "0.1"
        rule = PairRules.from_public_config(pair_payload, resolved_region="EEA",
                                            book_mid=Decimal("3000"))
        from scripts.run_eth_eur_shadow import fixture
        seq = fixture()["events"]
        state = fresh_state(self.cfg)
        for event in seq[:3]:
            state, decision = transition(state, event, self.cfg, rule)
        self.assertEqual(state["synthetic_fills"], 0)
        self.assertIn("LOT_1_CAP_OR_PAIR_RULE", decision["reasons"])

    def test_public_11_archived_real_shape_feeds_only_offline_shadow(self):
        import tempfile
        from scripts.inspect_eth_eur_public_capture import inspect_capture
        fake = Session()
        ticks = iter(BASE+100+i*5000 for i in range(3))
        captured, report = capture_once(
            cfg=self.cfg, samples=3, client=self.client(fake),
            clock=lambda: next(ticks), pause=lambda _: None)
        self.assertEqual(report["status"], "READY_FOR_OFFLINE_INSPECTION")
        with tempfile.TemporaryDirectory() as folder:
            replay = inspect_capture(captured, self.cfg, Path(folder)/"replay.sqlite")
            self.assertEqual(replay["observations"], 3)
            self.assertEqual(replay["audit_integrity"], "PASS")
            self.assertEqual(replay["real_mutation_requests"], 0)
            self.assertEqual(replay["adapter_type"], "OFFLINE_REPLAY_OF_SAVED_PUBLIC_GET")
            self.assertEqual(replay["acceptance"], "NO_GO_INSUFFICIENT_REAL_SAMPLE")
            self.assertEqual(replay["validation_30d"], "NOT_RUN")

    def test_public_12_tampered_archive_never_replayed(self):
        import tempfile
        from scripts.inspect_eth_eur_public_capture import inspect_capture
        captured, _ = capture_once(
            cfg=self.cfg, samples=1, client=self.client(Session()),
            clock=lambda: BASE+100, pause=lambda _: None)
        captured["events"][0]["bids"][0][0] = "1"
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "HASH_MISMATCH"):
                inspect_capture(captured, self.cfg, Path(folder)/"replay.sqlite")
