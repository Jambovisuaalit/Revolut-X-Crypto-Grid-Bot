from decimal import Decimal
import unittest

from precision_engine import (
    PairRules,
    PrecisionError,
    decimal_from_api,
    decimal_string,
    round_down,
    validate_order,
    validate_post_only,
)


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

    def test_round_down_returns_decimal(self) -> None:
        result = round_down(Decimal("123.456"), Decimal("0.01"))
        self.assertIsInstance(result, Decimal)
        self.assertEqual(result, Decimal("123.45"))

    def test_float_is_rejected_everywhere(self) -> None:
        with self.assertRaises(PrecisionError):
            decimal_from_api(0.01, field_name="quote_step")
        with self.assertRaises(TypeError):
            round_down(123.456, Decimal("0.01"))
        with self.assertRaises(TypeError):
            decimal_string(1.0)

    def test_pair_rules_parse_documented_decimal_strings(self) -> None:
        rules = PairRules.from_api(
            "BTC/USD",
            {
                "base": "BTC",
                "quote": "USD",
                "base_step": "0.0000001",
                "quote_step": "0.01",
                "min_order_size": "0.0000001",
                "max_order_size": "1000",
                "min_order_size_quote": "0.01",
                "status": "active",
            },
        )
        self.assertIsInstance(rules.quote_step, Decimal)
        self.assertEqual(rules.quote_step, Decimal("0.01"))

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
