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
        self.assertEqual(event["action"], "PAPER_BUY")
        self.assertLessEqual(runner.btc * Decimal("100.1"), Decimal("50"))
        self.assertGreater(runner.costs, 0)

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
        self.assertIn("DRAWDOWN_LIMIT", event["reasons"])
        self.assertTrue(event["halted"])
        self.assertEqual(runner.trades, 1)

    def test_hash_linkage(self):
        runner = ShadowRunner()
        a = runner.evaluate(snapshot(), NOW)
        b = runner.evaluate(snapshot(), NOW)
        self.assertEqual(b["previous_hash"], a["hash"])
        self.assertTrue(b["halted"])

    def test_invalid_book(self):
        with self.assertRaises(ValueError):
            parse_book(snapshot("101", "100"))

if __name__ == "__main__":
    unittest.main()
