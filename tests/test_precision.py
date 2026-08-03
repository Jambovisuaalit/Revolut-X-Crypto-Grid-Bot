from decimal import Decimal
import unittest

from precision_engine import PairRules, PrecisionError, round_down, validate_order, validate_post_only


class PrecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = PairRules(
            symbol="BTC/USD",
            base="BTC",
            quote="USD",
            base_step=Decimal("0.0000001"),
            quote_step=Decimal("0.01"),
            min_order_size=Decimal("0.0000001"),
            max_order_size=Decimal("1000"),
            min_order_size_quote=Decimal("0.01"),
            status="active",
        )

    def test_round_down(self) -> None:
        self.assertEqual(round_down(Decimal("123.456"), Decimal("0.01")), Decimal("123.45"))

    def test_five_percent_buy_guard(self) -> None:
        with self.assertRaises(PrecisionError):
            validate_order(
                rules=self.rules,
                side="buy",
                price=Decimal("100"),
                base_size=Decimal("0.51"),
                available_base=Decimal("10"),
                available_quote=Decimal("1000"),
                allocation_pct=Decimal("5"),
            )

    def test_post_only_cross_rejected(self) -> None:
        with self.assertRaises(PrecisionError):
            validate_post_only("buy", Decimal("101"), Decimal("100"), Decimal("101"))


if __name__ == "__main__":
    unittest.main()
