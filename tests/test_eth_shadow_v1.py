"""ETH/EUR offline shadow matrix; every observation and fill remains virtual."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from eth_shadow_v1.config import Config
from eth_shadow_v1.engine import fresh_state, transition
from eth_shadow_v1.grid import PairRules, buy_math, levels
from eth_shadow_v1.journal import Journal
from eth_shadow_v1.market import Book, Indicators, entry_blocks, indicators, parse_book
from scripts.run_eth_eur_shadow import CONFIG, fixture, sample_event, sample_pair, synthetic_candles

D = Decimal


class ShadowGridV1Tests(unittest.TestCase):
    """Locked software validation, not evidence of live trading or economic advantage."""

    def setUp(self) -> None:
        self.cfg = Config.load(CONFIG)
        self.rules = PairRules.from_dict(sample_pair())
        self.demo = fixture()
        self.events = self.demo["events"]
        self.state = fresh_state(self.cfg)

    def step(self, event: dict) -> dict:
        self.state, decision = transition(self.state, event, self.cfg, self.rules)
        return decision

    def test_CFG_01_mode_and_pair_fail_closed(self) -> None:
        self.assertEqual(self.cfg["mode"], "shadow")
        self.assertEqual(self.cfg["initial_cash_eur"], D("1000"))
        self.assertEqual(self.cfg["max_cost_basis_eur"], D("400"))
        self.assertEqual(self.cfg["hard_drawdown_eur"], D("30"))
        self.assertEqual(self.cfg.sha256, Config.load(CONFIG).sha256)
        with tempfile.TemporaryDirectory() as work:
            p = Path(work) / "unsafe.toml"
            p.write_text(Path(CONFIG).read_text().replace('mode = "shadow"', 'mode = "live"'))
            with self.assertRaises(ValueError):
                Config.load(p)
        inactive = PairRules.from_dict({**sample_pair(), "status": "halted"})
        _, dec = transition(self.state, self.events[0], self.cfg, inactive)
        self.assertEqual(dec["fills"], [])
        self.assertIn("CONFIG_BLOCKED", dec["reasons"])
        invalid = {**self.events[0], "metadata": {"timestamp": self.events[0]["metadata"]["timestamp"],
                                                   "region": "UK"}}
        self.assertEqual(self.step(invalid)["action"], "DATA_REJECTED")

    def test_GRID_01_exact_price_steps(self) -> None:
        self.assertEqual(levels(D("3000"), self.cfg, self.rules), (
            (D("2977.50"), D("3000.00")),
            (D("2955.00"), D("2977.50")),
            (D("2932.50"), D("2955.00")),
            (D("2910.00"), D("2932.50")),
        ))

    def test_FILL_01_02_two_snapshot_buy_and_single_slot(self) -> None:
        self.step(self.events[0])
        first = self.step(self.events[1])
        self.assertEqual(first["fills"], [])
        second = self.step(self.events[2])
        self.assertEqual([x["side"] for x in second["fills"]], ["BUY"])
        self.assertTrue(all(x["synthetic"] for x in second["fills"]))
        self.assertEqual(self.state["lots"]["1"]["status"], "OPEN_LOT")
        self.assertGreater(D(self.state["eth"]), 0)
        self.assertLessEqual(D(self.state["cash"]), D("1000"))
        self.assertLessEqual(D(self.state["eth"]), D("0.04"))

    def test_FILL_03_04_sell_only_owned_and_net_cost(self) -> None:
        self.step(self.events[0])
        self.assertEqual(self.step(self.events[3])["fills"], [])
        self.state = fresh_state(self.cfg)  # restart independent test chronology
        self.step(self.events[0])
        self.step(self.events[1])
        self.step(self.events[2])
        self.step(self.events[3])
        ending = self.step(self.events[4])
        self.assertEqual([x["side"] for x in ending["fills"]], ["SELL"])
        self.assertEqual(self.state["closed_cycles"], 1)
        self.assertEqual(D(self.state["eth"]), 0)
        self.assertGreater(D(self.state["realized_pnl"]), D("0.45"))
        self.assertLess(D(self.state["realized_pnl"]), D("0.50"))
        self.assertEqual(D(self.state["cash"]), D("1000") + D(self.state["realized_pnl"]))
        self.assertGreater(D(self.state["fees"]), 0)

    def test_FILL_05_REC_01_replay_restart_and_hash_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            location = Path(work) / "journal.sqlite"
            with Journal(location, "replay-fixture", self.cfg, self.rules) as j:
                first = j.process(self.events[0])
                self.assertEqual(j.process(self.events[0]), first)
                for event in self.events[1:3]:
                    j.process(event)
                self.assertEqual(j.verify()["synthetic_fills"], 1)
            with Journal(location, "replay-fixture", self.cfg, self.rules) as j:
                for event in self.events[2:]:
                    j.process(event)
                report = j.report()
                self.assertEqual(report["closed_cycles"], 1)
                self.assertEqual(report["audit_integrity"], "PASS")
                self.assertEqual(report["synthetic_fills"], 2)
                self.assertEqual(report["observations"], 5)
                self.assertEqual(report["exchange_orders"], 0)
                self.assertEqual(report["acceptance"], "NO_GO_SYNTHETIC_ONLY")
                with self.assertRaises(ValueError):
                    j.process({**self.events[2], "observed_ms": self.events[2]["observed_ms"] + 1})
            with Journal(location, "replay-fixture", self.cfg, self.rules) as j:
                self.assertEqual(j.verify()["synthetic_fills"], 2)

    def test_CAP_01_four_100_eur_lots_max_400(self) -> None:
        self.step(self.events[0])
        candles = self.events[0]["candles"]
        origin = self.events[0]["metadata"]["timestamp"]
        for n, (b,a) in enumerate([
            ("2975.50","2976.00"), ("2975.50","2976.00"),
            ("2952.50","2953.00"), ("2952.50","2953.00"),
            ("2930.00","2930.50"), ("2930.00","2930.50"),
            ("2907.50","2908.00"), ("2907.50","2908.00"),
        ], 1):
            self.step(sample_event(n, origin + 5000*n, b, a, candles))
        self.assertEqual(self.state["synthetic_fills"], 4)
        cost = sum((D(v["cost"]) for v in self.state["lots"].values()), D(0))
        self.assertLessEqual(cost, D("400"))
        self.assertGreaterEqual(D(self.state["cash"]), D("600"))

    def test_COST_01_lot_budget_including_fees(self) -> None:
        qty, total, fee = buy_math(D("2977.50"), self.cfg, self.rules)
        self.assertGreater(qty, 0)
        self.assertLessEqual(total, D("100"))
        self.assertEqual(fee, qty * D("2977.50") * D("1.0005") * D("0.0009"))

    def test_FILT_01_spread_depth_and_exit_still_works(self) -> None:
        poor = {**self.events[0], "asks": [["3005", "1"]]}
        self.assertIn("SPREAD_LIMIT", self.step(poor)["reasons"])
        self.assertIsNone(self.state["anchor"])
        thin = {**self.events[1], "bids": [["2975.50", "0.01"]]}
        self.assertIn("DEPTH_LIMIT", self.step(thin)["reasons"])
        self.assertIsNone(self.state["anchor"])
        self.state = fresh_state(self.cfg)
        for e in self.events[:3]:
            self.step(e)
        bad_exit = {**self.events[3], "asks": [["3012.00", "1"]]}
        self.step(bad_exit)
        exit_event = {**self.events[4], "asks": [["3012.00", "1"]]}
        self.assertEqual(self.step(exit_event)["fills"][0]["side"], "SELL")

    def test_FILT_02_ema_adx_atr_24h_volume(self) -> None:
        book = parse_book(self.events[0], self.cfg)
        good = indicators(self.events[0], book)
        self.assertLess(good.adx14, D(25))
        self.assertEqual(entry_blocks(book, good, self.cfg, self.rules), [])
        bad = replace(good, adx14=D(25), ema_divergence=D("0.013"),
                      atr_rate=D("0.014"), change_24h=D("0.05"),
                      zero_volume_share=D("0.26"))
        reasons = entry_blocks(book, bad, self.cfg, self.rules)
        self.assertEqual(set(reasons), {
            "ADX_LIMIT", "EMA_LIMIT", "ATR_LIMIT", "CHANGE_24H_LIMIT",
            "ZERO_VOLUME_LIMIT",
        })

    def test_DATA_01_timestamp_and_candle_fail_closed(self) -> None:
        invalid = [
            {**self.events[0], "metadata": {"timestamp": 1781654700, "region": "EEA"}},
            {**self.events[0], "metadata": {"timestamp": self.events[0]["metadata"]["timestamp"] + 4000, "region":"EEA"}},
            {**self.events[0], "bids": [["3001", "1"]]},
        ]
        for item in invalid:
            with self.subTest(invalid=item):
                s, decision = transition(fresh_state(self.cfg), item, self.cfg, self.rules)
                self.assertEqual(decision["fills"], [])
                self.assertEqual(decision["action"], "DATA_REJECTED")
        malformed = {**self.events[0], "candles": self.events[0]["candles"][:10]}
        self.assertIn("CANDLES_INSUFFICIENT", self.step(malformed)["reasons"])

    def test_DATA_02_three_bad_polls_and_recovery_without_backfills(self) -> None:
        self.step(self.events[0])
        for n in range(1,4):
            bad = {**self.events[0], "event_id": f"bad-{n}",
                   "observed_ms": self.events[0]["observed_ms"] + n*5000,
                   "metadata": {"timestamp": -1, "region": "EEA"}}
            self.step(bad)
        self.assertEqual(self.state["status"], "DATA_HALT")
        self.assertEqual(self.state["synthetic_fills"], 0)
        recovered = self.step(self.events[4])
        self.assertNotEqual(recovered["status"], "STOPPED")
        self.assertEqual(self.state["synthetic_fills"], 0)

    def test_RISK_01_daily_threshold_pause(self) -> None:
        self.step(self.events[0])
        self.state["cash"] = "990"
        self.assertIn("DAILY_LOSS_PAUSE", self.step(self.events[1])["reasons"])
        self.assertEqual(self.state["status"], "PAUSED_ENTRIES")
        self.assertEqual(self.state["synthetic_fills"], 0)

    def test_RISK_02_trailing_soft_pause(self) -> None:
        self.step(self.events[0])
        self.state["cash"] = "985"
        decision = self.step(self.events[1])
        self.assertIn("SOFT_DRAWDOWN_PAUSE", decision["reasons"])
        self.assertEqual(self.state["status"], "PAUSED_ENTRIES")
        self.assertIsNotNone(self.state["soft_since_ms"])

    def test_RISK_03_hard_stop_liquidates_and_is_sticky(self) -> None:
        for e in self.events[:3]:
            self.step(e)
        hard = {**self.events[3], "bids": [["1900.00", "1"]],
                "asks": [["1900.50", "1"]]}
        decision = self.step(hard)
        self.assertEqual(decision["action"], "HARD_STOP")
        self.assertEqual(decision["fills"][0]["side"], "SELL_HARD_STOP")
        self.assertEqual(self.state["status"], "STOPPED")
        self.assertEqual(D(self.state["eth"]), 0)
        self.assertLess(D(self.state["last_equity"]), D("970"))
        next_decision = self.step(self.events[4])
        self.assertEqual(next_decision["fills"], [])
        self.assertEqual(next_decision["status"], "STOPPED")

    def test_REBASE_01_never_move_with_open_lot(self) -> None:
        for e in self.events[:3]:
            self.step(e)
        old = self.state["anchor"]
        later = self.events[2]["metadata"]["timestamp"] + 86405000
        future = sample_event(101, later, "2850", "2850.5", synthetic_candles(later))
        self.step(future)
        self.assertEqual(self.state["anchor"], old)

    def test_SEC_01_no_network_and_no_live_imports(self) -> None:
        with patch.object(socket, "socket", side_effect=AssertionError("NETWORK_FORBIDDEN")):
            with patch("urllib.request.urlopen", side_effect=AssertionError("NETWORK_FORBIDDEN")):
                for e in self.events:
                    self.step(e)
        root = Path(__file__).resolve().parent.parent / "eth_shadow_v1"
        for py in root.glob("*.py"):
            code = py.read_text(encoding="utf-8")
            self.assertNotIn("import revolut_x_client", code)
            self.assertNotIn("import requests", code)
        self.assertEqual(self.state["synthetic_fills"], 2)


if __name__ == "__main__":
    unittest.main()
