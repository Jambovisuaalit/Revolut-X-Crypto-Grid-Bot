"""Offline-only tests: shadow risk, quote validation and audit chain."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from shadow_runner import ShadowRunner, parse_book

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)

def snapshot(bid="100", ask="100.1", stamp=NOW):
    return dict(timestamp=stamp.isoformat(), bids=[[bid, "1"]], asks=[[ask, "1"]])

class ShadowRunnerTests(unittest.TestCase):
    def test_paper_accounting_and_cap(self):
        runner = ShadowRunner()
        event = runner.evaluate(snapshot(), NOW)
        self.assertEqual(event["action"], "PAPER_POST_ONLY_BUY_PLACED")
        self.assertLessEqual(runner.btc * Decimal("100.1"), Decimal("50"))
        self.assertEqual(runner.costs, 0)

    def test_spread_guard(self):
        runner = ShadowRunner()
        self.assertEqual(runner.evaluate(snapshot(ask="110"), NOW)["action"], "HOLD")
        self.assertEqual(runner.trades, 0)

    def test_stale_data_halts(self):
        runner = ShadowRunner()
        old = NOW - timedelta(seconds=60)
        self.assertTrue(runner.evaluate(snapshot(stamp=old), NOW)["halted"])
        self.assertEqual(runner.evaluate(snapshot(), NOW)["action"], "HOLD")

    def test_drawdown_stops_future_orders(self):
        runner = ShadowRunner()
        runner.evaluate(snapshot(), NOW)
        later = NOW + timedelta(seconds=1)
        event = runner.evaluate(snapshot("1", "1.01", later), later)
        self.assertIn("DRAWDOWN_LIMIT_POST_FILL", event["reasons"])
        self.assertTrue(event["halted"])
        self.assertEqual(runner.trades, 0)

    def test_restored_state_keeps_pending_order_and_hash(self):
        first = ShadowRunner()
        event = first.evaluate(snapshot(), NOW)
        second = ShadowRunner()
        second.restore(first.state())
        self.assertEqual(second.pending_buy, first.pending_buy)
        self.assertEqual(second.previous_hash, event["hash"])
        later = NOW + timedelta(seconds=1)
        next_event = second.evaluate(snapshot("99", "99.5", later), later)
        self.assertEqual(next_event["action"], "PAPER_MAKER_FILL_ASSUMED")
        self.assertEqual(next_event["previous_hash"], event["hash"])

    def test_hash_linkage(self):
        runner = ShadowRunner()
        a = runner.evaluate(snapshot(), NOW)
        b = runner.evaluate(snapshot(), NOW)
        self.assertEqual(b["previous_hash"], a["hash"])
        self.assertTrue(b["halted"])

    def test_timestamp_fail_closed_and_logged(self):
        invalid = [
            {},
            {"timestamp": "not-a-time"},
            {"timestamp": "2026-10-17T00:00:00"},
            {"timestamp": (NOW + timedelta(seconds=1)).isoformat()},
        ]
        for item in invalid:
            with self.subTest(item=item):
                runner = ShadowRunner()
                payload = dict(snapshot(), **item)
                if "timestamp" not in item:
                    payload.pop("timestamp")
                decision = runner.evaluate(payload, NOW)
                self.assertTrue(decision["halted"])
                self.assertEqual(decision["action"], "HOLD")
                self.assertTrue(decision["hash"])
                self.assertEqual(runner.violations, 1)

    def test_post_fill_drawdown_is_rechecked(self):
        runner = ShadowRunner()
        runner.evaluate(snapshot(), NOW)
        later = NOW + timedelta(seconds=1)
        # With a high maker fee, the assumed fill itself breaches the 3% floor.
        runner.cfg.fee_rate = Decimal("0.90")
        decision = runner.evaluate(snapshot("99", "99.5", later), later)
        self.assertTrue(decision["halted"])
        self.assertIn("DRAWDOWN_LIMIT_POST_FILL", decision["reasons"])

    def test_invalid_book(self):
        with self.assertRaises(ValueError):
            parse_book(snapshot("101", "100"))

if __name__ == "__main__":
    unittest.main()
