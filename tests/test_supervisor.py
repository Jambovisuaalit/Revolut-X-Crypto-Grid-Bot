from decimal import Decimal
import unittest

from bot_main import drawdown_pct, portfolio_equity
from revolut_x_client import RevolutXError


class SupervisorTests(unittest.TestCase):
    def test_drawdown_exact_three_percent(self) -> None:
        self.assertEqual(drawdown_pct(Decimal("1000"), Decimal("970")), Decimal("3.00"))

    def test_portfolio_equity_direct_pair(self) -> None:
        balances = [
            {"currency": "USD", "total": "100"},
            {"currency": "BTC", "total": "0.01"},
        ]
        tickers = [{"symbol": "BTC/USD", "mid": "100000"}]
        self.assertEqual(
            portfolio_equity(balances=balances, tickers=tickers, quote_currency="USD"),
            Decimal("1100.00"),
        )

    def test_portfolio_equity_fails_closed_without_conversion(self) -> None:
        with self.assertRaises(RevolutXError):
            portfolio_equity(
                balances=[{"currency": "SOL", "total": "1"}],
                tickers=[],
                quote_currency="USD",
            )


if __name__ == "__main__":
    unittest.main()
