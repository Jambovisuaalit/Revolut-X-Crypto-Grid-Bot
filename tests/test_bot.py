"""Guardrail tests for the locked MVP behavior."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bot_main import GridBotSupervisor
from grid_executor import GridExecutor, ReconciliationError
from precision_engine import PairRules, PrecisionError, SymbolNormalizer
from revolut_x_client import RevolutXClient


class FakeClient:
    def __init__(self) -> None:
        self.open_orders: list[dict[str, Any]] = []
        self.fills: list[dict[str, Any]] = []
        self.placed: list[dict[str, Any]] = []
        self.replaced: list[dict[str, Any]] = []
        self.cancelled = False

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self.open_orders

    def get_fills(self, venue_order_id: str) -> list[dict[str, Any]]:
        return self.fills

    def place_order(self, payload: dict[str, Any]) -> dict[str, str]:
        self.placed.append(payload)
        return {"order_id": "venue-1"}

    def replace_order(self, venue_order_id: str, payload: dict[str, Any]) -> dict[str, str]:
        self.replaced.append(payload)
        return {"order_id": "venue-2"}

    def cancel_all_orders(self) -> None:
        self.cancelled = True


def rules() -> PairRules:
    return PairRules("BTC/USD", Decimal("0.00000001"), Decimal("0.01"), Decimal("0.0001"), Decimal("10"), "active")


class BotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.client = FakeClient()
        self.executor = GridExecutor(
            self.client, {"BTC/USD": rules()}, database_path=str(Path(self.temp.name) / "state.db"), live_trading=True
        )
        self.executor.reconcile_open_orders()

    def tearDown(self) -> None:
        self.executor.close()
        self.temp.cleanup()

    def test_symbols_and_post_only_payload(self) -> None:
        self.assertEqual(SymbolNormalizer.to_api_symbol("btc/usd"), "BTC-USD")
        self.assertEqual(SymbolNormalizer.to_display_symbol("btc_usd"), "BTC/USD")
        result = self.executor.create_order(
            client_order_id="logical-1", symbol="BTC/USD", side="buy", price=Decimal("120000.509"),
            quantity=Decimal("0.01"), free_balances={"USD": Decimal("3000000")}
        )
        self.assertEqual(result["order_id"], "venue-1")
        limit = self.client.placed[0]["order_configuration"]["limit"]
        self.assertEqual(limit["price"], "120000.50")
        self.assertEqual(limit["execution_instructions"], ["post_only"])

    def test_idempotency_and_aggregate_reservation(self) -> None:
        kwargs = dict(client_order_id="same", symbol="BTC/USD", side="sell", price=Decimal("100000"),
                      quantity=Decimal("0.05"), free_balances={"BTC": Decimal("1")})
        self.executor.create_order(**kwargs)
        self.executor.create_order(**kwargs)
        self.assertEqual(len(self.client.placed), 1)
        with self.assertRaises(PrecisionError):
            self.executor.create_order(**{**kwargs, "client_order_id": "too-large", "quantity": Decimal("0.051")})

    def test_replace_uses_partial_fill_leaves_and_records_fill(self) -> None:
        self.executor.create_order(
            client_order_id="root", symbol="BTC/USD", side="sell", price=Decimal("100000"),
            quantity=Decimal("0.05"), free_balances={"BTC": Decimal("1")}
        )
        self.client.open_orders = [{"order_id": "venue-1", "status": "partially_filled", "filled_quantity": "0.02"}]
        self.client.fills = [{"fill_id": "f1", "quantity": "0.02", "price": "100000", "fee": "1", "fee_currency": "USD", "im": True}]
        self.executor.reconcile_open_orders()
        self.executor.replace_order("venue-1", new_price=Decimal("101000"), free_balances={"BTC": Decimal("1")}, client_order_id="child")
        self.assertEqual(self.client.replaced[0]["order_configuration"]["limit"]["base_size"], "0.03000000")
        row = self.executor.connection.execute("SELECT parent_venue_order_id,version FROM order_lineage WHERE client_order_id='child'").fetchone()
        self.assertEqual(tuple(row), ("venue-1", 2))
        self.assertEqual(self.executor.connection.execute("SELECT is_maker FROM fills WHERE fill_id='f1'").fetchone()[0], 1)

    def test_drawdown_cancels_and_persists_halt(self) -> None:
        supervisor = GridBotSupervisor(self.executor, "USD")
        self.assertFalse(supervisor.check_drawdown(Decimal("100")))
        self.assertTrue(supervisor.check_drawdown(Decimal("97")))
        self.assertTrue(self.client.cancelled)
        self.assertEqual(supervisor._metadata("kill_switch_triggered"), "true")

    def test_dry_run_does_not_mutate_or_persist(self) -> None:
        dry = GridExecutor(self.client, {"BTC/USD": rules()}, database_path=str(Path(self.temp.name) / "dry.db"))
        dry.reconcile_open_orders()
        result = dry.create_order(client_order_id="dry", symbol="BTC/USD", side="buy", price=Decimal("100"),
                                  quantity=Decimal("0.01"), free_balances={"USD": Decimal("100")})
        self.assertTrue(result["dry_run"])
        self.assertEqual(dry.connection.execute("SELECT count(*) FROM order_lineage").fetchone()[0], 0)
        dry.close()


class ClientTests(unittest.TestCase):
    def test_signature_uses_minified_body_and_api_path(self) -> None:
        key = Ed25519PrivateKey.generate()
        client = RevolutXClient("public", key, clock_ms=lambda: 123)
        body = {"hello": "world"}
        body_text = json.dumps(body, separators=(",", ":"))
        headers = client.signed_headers("POST", "/1.0/orders", "", body_text)
        signature = base64.b64decode(headers["X-Revx-Signature"])
        key.public_key().verify(signature, b"123POST/api/1.0/orders" + body_text.encode())

    def test_429_retry_after_is_milliseconds(self) -> None:
        key = Ed25519PrivateKey.generate()
        first = Mock(status_code=429, headers={"Retry-After": "250"}, content=b"")
        second = Mock(status_code=200, headers={}, content=b"{}")
        second.json.return_value = {}
        session = Mock()
        session.request.side_effect = [first, second]
        sleeps: list[float] = []
        client = RevolutXClient("public", key, session=session, sleeper=sleeps.append)
        self.assertEqual(client.request("GET", "/1.0/orders"), {})
        self.assertEqual(sleeps, [0.25])


if __name__ == "__main__":
    unittest.main()
