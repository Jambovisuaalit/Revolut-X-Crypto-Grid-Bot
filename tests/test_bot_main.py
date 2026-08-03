from decimal import Decimal
import os
import unittest
from unittest.mock import patch

from bot_main import BotConfig, _market_snapshot, drawdown_pct, portfolio_equity, run
from precision_engine import PrecisionError
from revolut_x_client import RevolutXError


def make_config(*, dry_run: bool = True) -> BotConfig:
    return BotConfig(
        api_key="x" * 64,
        private_key_path="/tmp/key.pem",
        symbol="BTC-USD",
        quote_currency="USD",
        region="EEA",
        levels_per_side=2,
        spacing_bps=Decimal("25"),
        allocation_pct=Decimal("1"),
        recenter_threshold_bps=Decimal("75"),
        loop_interval_seconds=5,
        max_market_age_ms=5000,
        dry_run=dry_run,
        db_path=":memory:",
    )


PAIR_PAYLOAD = {
    "base": "BTC",
    "quote": "USD",
    "base_step": "0.0001",
    "quote_step": "0.01",
    "min_order_size": "0.0001",
    "max_order_size": "100",
    "min_order_size_quote": "0.01",
    "status": "active",
}


class FakeRunClient:
    def __init__(self, equity: str = "970") -> None:
        self.equity = equity
        self.account_config_calls = 0

    def get_account_pair_configuration(self):
        self.account_config_calls += 1
        return {"BTC/USD": dict(PAIR_PAYLOAD)}

    def get_pair_configuration(self, region=None):
        raise AssertionError("trading supervisor must use authenticated account pair configuration")

    def get_active_orders(self, symbols=None):
        return []

    def get_tickers(self):
        return (
            [{
                "symbol": "BTC/USD",
                "bid": "99",
                "ask": "101",
                "mid": "100",
            }],
            1_000_000,
        )

    def get_balances(self):
        return [{"currency": "USD", "total": self.equity}]


class FakeRunExecutor:
    def __init__(
        self,
        *,
        initial_equity: Decimal = Decimal("1000"),
        persisted_kill: bool = False,
        trigger_error: Exception | None = None,
    ) -> None:
        self.initial_equity = initial_equity
        self.persisted_kill = persisted_kill
        self.trigger_error = trigger_error
        self.trigger_calls = 0
        self.reconcile_calls = 0
        self.maintain_calls = 0
        self.closed = False

    def get_meta(self, key):
        if key == "kill_switch_triggered" and self.persisted_kill:
            return "true"
        return None

    def trigger_kill_switch(self):
        self.trigger_calls += 1
        if self.trigger_error:
            raise self.trigger_error

    def reconcile(self, symbol):
        self.reconcile_calls += 1

    def get_or_set_initial_equity(self, current):
        return self.initial_equity

    def maintain_grid(self, **kwargs):
        self.maintain_calls += 1

    def close(self):
        self.closed = True


class BotMainTests(unittest.TestCase):
    def test_config_rejects_invalid_boolean(self) -> None:
        env = {
            "REVX_API_KEY": "x" * 64,
            "REVX_PRIVATE_KEY_PATH": "/tmp/key.pem",
            "DRY_RUN": "maybe",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                BotConfig.from_env()

    def test_config_requires_explicit_live_gate(self) -> None:
        env = {
            "REVX_API_KEY": "x" * 64,
            "REVX_PRIVATE_KEY_PATH": "/tmp/key.pem",
            "DRY_RUN": "false",
            "LIVE_TRADING_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                BotConfig.from_env()

    def test_config_rejects_grid_reaching_100_percent(self) -> None:
        with self.assertRaises(ValueError):
            BotConfig(
                api_key="x" * 64,
                private_key_path="/tmp/key.pem",
                symbol="BTC-USD",
                quote_currency="USD",
                region=None,
                levels_per_side=4,
                spacing_bps=Decimal("2500"),
                allocation_pct=Decimal("1"),
                recenter_threshold_bps=Decimal("75"),
                loop_interval_seconds=5,
                max_market_age_ms=5000,
                dry_run=True,
                db_path=":memory:",
            )

    def test_portfolio_equity_uses_decimal_exactly(self) -> None:
        equity = portfolio_equity(
            balances=[
                {"currency": "USD", "total": "100.25"},
                {"currency": "BTC", "total": "2"},
            ],
            tickers=[{"symbol": "BTC/USD", "mid": "50.125"}],
            quote_currency="USD",
        )
        self.assertEqual(equity, Decimal("200.500"))

    def test_portfolio_equity_rejects_float_wire_value(self) -> None:
        with self.assertRaises(PrecisionError):
            portfolio_equity(
                balances=[{"currency": "USD", "total": 100.0}],
                tickers=[{"symbol": "BTC/USD", "mid": "50"}],
                quote_currency="USD",
            )

    def test_drawdown_exact_three_percent(self) -> None:
        self.assertEqual(
            drawdown_pct(Decimal("1000"), Decimal("970")),
            Decimal("3.00"),
        )

    def test_drawdown_rejects_float(self) -> None:
        with self.assertRaises(TypeError):
            drawdown_pct(1000.0, Decimal("970"))  # type: ignore[arg-type]

    def test_market_snapshot_rejects_float_price(self) -> None:
        class Client:
            def get_tickers(self):
                return (
                    [{"symbol": "BTC/USD", "bid": 99.0, "ask": "101", "mid": "100"}],
                    1_000_000,
                )

        with patch("bot_main.time.time_ns", return_value=1_000_000 * 1_000_000):
            with self.assertRaises(PrecisionError):
                _market_snapshot(Client(), "BTC-USD", 5000)  # type: ignore[arg-type]

    def test_run_triggers_kill_switch_at_three_percent(self) -> None:
        config = make_config(dry_run=False)
        client = FakeRunClient("970")
        executor = FakeRunExecutor(initial_equity=Decimal("1000"))

        with (
            patch("bot_main.BotConfig.from_env", return_value=config),
            patch("bot_main.RevolutXClient", return_value=client),
            patch("bot_main.GridExecutor", return_value=executor),
            patch("bot_main.signal.signal"),
            patch("bot_main.time.time_ns", return_value=1_000_000 * 1_000_000),
        ):
            result = run()

        self.assertEqual(result, 10)
        self.assertEqual(executor.trigger_calls, 1)
        self.assertEqual(executor.maintain_calls, 0)
        self.assertEqual(client.account_config_calls, 1)
        self.assertTrue(executor.closed)

    def test_run_halts_if_cancel_all_fails_after_kill_switch(self) -> None:
        config = make_config(dry_run=False)
        client = FakeRunClient("970")
        executor = FakeRunExecutor(
            initial_equity=Decimal("1000"),
            trigger_error=RevolutXError("cancel failed"),
        )

        with (
            patch("bot_main.BotConfig.from_env", return_value=config),
            patch("bot_main.RevolutXClient", return_value=client),
            patch("bot_main.GridExecutor", return_value=executor),
            patch("bot_main.signal.signal"),
            patch("bot_main.time.time_ns", return_value=1_000_000 * 1_000_000),
        ):
            result = run()

        self.assertEqual(result, 11)
        self.assertEqual(executor.trigger_calls, 1)
        self.assertTrue(executor.closed)

    def test_persisted_kill_switch_retries_cancel_before_halt(self) -> None:
        config = make_config(dry_run=False)
        client = FakeRunClient("1000")
        executor = FakeRunExecutor(persisted_kill=True)

        with (
            patch("bot_main.BotConfig.from_env", return_value=config),
            patch("bot_main.RevolutXClient", return_value=client),
            patch("bot_main.GridExecutor", return_value=executor),
            patch("bot_main.signal.signal"),
        ):
            result = run()

        self.assertEqual(result, 3)
        self.assertEqual(executor.trigger_calls, 1)
        self.assertEqual(client.account_config_calls, 0)
        self.assertTrue(executor.closed)


if __name__ == "__main__":
    unittest.main()
