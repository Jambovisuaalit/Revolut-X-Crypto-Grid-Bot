"""Offline contract and fault-injection tests; no real API credentials or trading."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bot_main import GridBotSupervisor
from grid_executor import GridExecutor, ReconciliationError
from precision_engine import PairRules, PrecisionError
from revolut_x_client import RevolutXClient, UncertainMutationError, ApiError


def uid() -> str:
    return str(uuid4())


def rules() -> PairRules:
    return PairRules("BTC/USD", Decimal("0.00000001"), Decimal("0.01"),
                     Decimal("0.0001"), Decimal("10"), "active")


def venue(cid, vid, *, side="buy", price="100.00", quantity="0.01000000",
          filled="0", status="new", symbol="BTC/USD", parent=None):
    result = {
        "id": vid, "client_order_id": cid, "symbol": symbol, "side": side,
        "type": "limit", "price": price, "quantity": quantity, "filled_quantity": filled,
        "leaves_quantity": str(Decimal(quantity) - Decimal(filled)), "status": status,
        "execution_instructions": ["post_only"],
    }
    if parent:
        result["previous_order_id"] = parent
    return result


class FakeClient:
    def __init__(self):
        self.active, self.history, self.fills = [], [], []
        self.placed, self.replaced = [], []
        self.cancelled = False
        self.lost_post_reply = False
        self.lost_put_reply = False

    def get_open_orders(self):
        return list(self.active)

    def get_historical_orders(self):
        return list(self.history)

    def get_fills(self, venue_order_id):
        return list(self.fills)

    def place_order(self, payload):
        self.placed.append(payload)
        new_id = uid()
        lim = payload["order_configuration"]["limit"]
        self.active.append(venue(payload["client_order_id"], new_id, side=payload["side"],
                                 price=lim["price"], quantity=lim["base_size"]))
        if self.lost_post_reply:
            raise UncertainMutationError("simulated lost POST acknowledgement")
        return {"venue_order_id": new_id, "client_order_id": payload["client_order_id"], "state": "new"}

    def replace_order(self, venue_order_id, payload):
        self.replaced.append(payload)
        parent_row = next(x for x in self.active if x["id"] == venue_order_id)
        self.active = [x for x in self.active if x["id"] != venue_order_id]
        new_id = uid()
        self.active.append(venue(payload["client_order_id"], new_id, side=parent_row["side"],
                                 price=payload["price"], quantity=payload["base_size"],
                                 parent=venue_order_id))
        if self.lost_put_reply:
            raise UncertainMutationError("simulated lost PUT acknowledgement")
        return {"venue_order_id": new_id, "client_order_id": payload["client_order_id"], "state": "new"}

    def cancel_all_orders(self):
        self.cancelled = True


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "bot.sqlite")
        self.client = FakeClient()
        self.executor = GridExecutor(self.client, {"BTC/USD": rules()}, database_path=self.db,
                                     live_trading=True)
        self.executor.reconcile_open_orders()

    def tearDown(self):
        self.executor.close()
        self.tmp.cleanup()

    def create(self, cid=None, *, side="buy", price="100", quantity="0.01", free=None):
        return self.executor.create_order(
            client_order_id=cid or uid(), symbol="BTC/USD", side=side,
            price=Decimal(price), quantity=Decimal(quantity),
            free_balances=free or {"USD": Decimal("100000"), "BTC": Decimal("10")}
        )

    def restart(self):
        self.executor.close()
        self.executor = GridExecutor(self.client, {"BTC/USD": rules()}, database_path=self.db,
                                     live_trading=True)
        self.executor.reconcile_open_orders()

    def test_post_only_payload_and_idempotent_creation(self):
        cid = uid()
        self.create(cid)
        lim = self.client.placed[0]["order_configuration"]["limit"]
        self.assertEqual(lim, {"base_size": "0.01000000", "price": "100.00",
                               "execution_instructions": ["post_only"]})
        self.assertNotIn("time_in_force", lim)
        self.assertTrue(self.create(cid)["idempotent"])
        self.assertEqual(len(self.client.placed), 1)
        with self.assertRaises(ReconciliationError):
            self.create(cid, price="101")
        with self.assertRaises(PrecisionError):
            self.create("invalid-order-id")
        self.assertEqual(len(self.client.placed), 1)

    def test_lost_post_reply_recovered_after_restart_without_new_post(self):
        cid = uid()
        self.client.lost_post_reply = True
        with self.assertRaises(UncertainMutationError):
            self.create(cid)
        self.assertFalse(self.executor.reconciled)
        with self.assertRaises(ReconciliationError):
            self.create(cid)
        self.restart()
        self.assertTrue(self.create(cid)["idempotent"])
        self.assertEqual(len(self.client.placed), 1)

    def test_lost_post_reply_without_venue_proof_stays_blocked(self):
        cid = uid()
        self.client.lost_post_reply = True
        with self.assertRaises(UncertainMutationError):
            self.create(cid)
        self.client.active.clear()
        with self.assertRaises(ReconciliationError):
            self.restart()
        self.assertFalse(self.executor.reconciled)
        self.assertEqual(len(self.client.placed), 1)

    def test_lost_post_reply_is_adopted_from_terminal_history(self):
        cid = uid()
        self.client.lost_post_reply = True
        with self.assertRaises(UncertainMutationError):
            self.create(cid)
        row = self.client.active.pop()
        row.update(status="filled", filled_quantity="0.01000000", leaves_quantity="0")
        self.client.history.append(row)
        self.client.fills = [{"tid": uid(), "p": "100", "q": "0.01", "pc": "USD", "qc": "BTC"}]
        self.restart()
        stored = self.executor.connection.execute(
            "SELECT status FROM order_lineage WHERE client_order_id=?", (cid,)
        ).fetchone()
        self.assertEqual(stored["status"], "filled")
        self.assertEqual(self.executor.connection.execute("SELECT count(*) FROM fills_wire").fetchone()[0], 1)
        self.assertEqual(len(self.client.placed), 1)

    def test_lost_put_reply_adopts_child_without_repeat(self):
        cid = uid()
        result = self.create(cid)
        self.client.lost_put_reply = True
        child = uid()
        with self.assertRaises(UncertainMutationError):
            self.executor.replace_order(result["venue_order_id"], new_price=Decimal("101"),
                                        free_balances={"USD": Decimal("100000")}, client_order_id=child)
        self.assertFalse(self.executor.reconciled)
        self.restart()
        a = self.executor.connection.execute(
            "SELECT status FROM order_lineage WHERE client_order_id=?", (cid,)
        ).fetchone()
        b = self.executor.connection.execute(
            "SELECT status FROM order_lineage WHERE client_order_id=?", (child,)
        ).fetchone()
        self.assertEqual(a["status"], "replaced")
        self.assertEqual(b["status"], "new")
        self.assertEqual(len(self.client.replaced), 1)
        self.assertEqual(set(self.client.replaced[0]), {"client_order_id", "base_size", "price", "execution_instructions"})
        self.assertNotIn("order_configuration", self.client.replaced[0])

    def test_lost_put_without_child_proof_stays_blocked(self):
        result = self.create()
        self.client.lost_put_reply = True
        with self.assertRaises(UncertainMutationError):
            self.executor.replace_order(result["venue_order_id"], new_price=Decimal("101"),
                                        free_balances={"USD": Decimal("100000")})
        self.client.active.clear()
        with self.assertRaises(ReconciliationError):
            self.restart()
        self.assertEqual(len(self.client.replaced), 1)

    def test_mismatched_price_or_untracked_order_blocks_startup(self):
        self.create()
        self.client.active[0]["price"] = "9999"
        with self.assertRaises(ReconciliationError):
            self.executor.reconcile_open_orders()
        self.client.active = [venue(uid(), uid())]
        with self.assertRaises(ReconciliationError):
            self.executor.reconcile_open_orders()

    def test_5pct_funding_guard_and_dry_run(self):
        with self.assertRaises(PrecisionError):
            self.create(side="sell", quantity="0.051",
                        free={"USD": Decimal("100"), "BTC": Decimal("1")})
        dry = GridExecutor(self.client, {"BTC/USD": rules()},
                           database_path=str(Path(self.tmp.name) / "dry.sqlite"), live_trading=False)
        dry.reconcile_open_orders()
        response = dry.create_order(client_order_id=uid(), symbol="BTC/USD", side="buy",
                                    price=Decimal("100"), quantity=Decimal("0.01"),
                                    free_balances={"USD": Decimal("100")})
        self.assertTrue(response["dry_run"])
        self.assertEqual(dry.connection.execute("SELECT count(*) FROM order_lineage").fetchone()[0], 0)
        dry.close()

    def test_drawdown_state_persists(self):
        supervisor = GridBotSupervisor(self.executor, "USD")
        self.assertFalse(supervisor.check_drawdown(Decimal("100")))
        self.assertTrue(supervisor.check_drawdown(Decimal("97")))
        self.assertTrue(self.client.cancelled)
        self.assertEqual(supervisor._metadata("kill_switch_triggered"), "true")


class OfficialApiContracts(unittest.TestCase):
    def test_canonical_api_signature(self):
        key = Ed25519PrivateKey.generate()
        client = RevolutXClient("key", key, clock_ms=lambda: 123)
        body = json.dumps({"a": 1}, separators=(",", ":"))
        signature = base64.b64decode(client.signed_headers("POST", "/1.0/orders", "", body)["X-Revx-Signature"])
        key.public_key().verify(signature, b"123POST/api/1.0/orders" + body.encode())

    def test_official_pair_configuration_schema(self):
        r = PairRules.from_api({"base": "BTC", "quote": "USD",
                                "base_step": "0.0000001", "quote_step": "0.01",
                                "min_order_size": "0.0001", "max_order_size": "1000",
                                "min_order_size_quote": "0.01", "status": "active"})
        self.assertEqual(r.symbol, "BTC/USD")

    def test_active_cursor_pagination(self):
        first = Mock(status_code=200, content=b"x")
        first.json.return_value = {"data": [venue(uid(), "venue-a")],
                                   "metadata": {"timestamp": 1, "next_cursor": "cursor2"}}
        second = Mock(status_code=200, content=b"x")
        second.json.return_value = {"data": [venue(uid(), "venue-b")],
                                    "metadata": {"timestamp": 2}}
        session = Mock()
        session.request.side_effect = [first, second]
        client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
        self.assertEqual(len(client.get_open_orders()), 2)
        self.assertIn("/1.0/orders/active", session.request.call_args_list[0].args[1])
        self.assertIn("cursor=cursor2", session.request.call_args_list[1].args[1])

    def test_placement_data_wrapper(self):
        cid, vid = uid(), uid()
        response = Mock(status_code=200, content=b"x")
        response.json.return_value = {"data": {"venue_order_id": vid, "client_order_id": cid, "state": "new"}}
        session = Mock()
        session.request.return_value = response
        client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
        self.assertEqual(client.place_order({"client_order_id": cid})["venue_order_id"], vid)

    def test_mutation_timeout_is_not_retried(self):
        session = Mock()
        session.request.side_effect = requests.Timeout("lost response")
        client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
        with self.assertRaises(UncertainMutationError):
            client.place_order({"client_order_id": uid()})
        self.assertEqual(session.request.call_count, 1)

    def test_mutation_429_and_5xx_never_retry(self):
        for code in (429, 500, 502, 503):
            session = Mock()
            session.request.return_value = Mock(status_code=code, headers={"Retry-After": "250"}, content=b"x")
            client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
            with self.assertRaises(UncertainMutationError):
                client.place_order({"client_order_id": uid()})
            self.assertEqual(session.request.call_count, 1)

    def test_get_429_retry_after_is_milliseconds(self):
        rate = Mock(status_code=429, headers={"Retry-After": "250"}, content=b"x")
        success = Mock(status_code=200, headers={}, content=b"x")
        success.json.return_value = {"data": [], "metadata": {"timestamp": 1}}
        session = Mock()
        session.request.side_effect = [rate, success]
        sleeps = []
        client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session, sleeper=sleeps.append)
        self.assertEqual(client.get_open_orders(), [])
        self.assertEqual(sleeps, [0.25])

    def test_invalid_mutation_ack_needs_reconciliation(self):
        response = Mock(status_code=200, content=b"x")
        response.json.return_value = {"data": {"venue_order_id": uid(), "state": "new"}}
        session = Mock()
        session.request.return_value = response
        client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
        with self.assertRaises(UncertainMutationError):
            client.place_order({"client_order_id": uid()})
        self.assertEqual(session.request.call_count, 1)

    def test_malformed_pagination_blocks(self):
        response = Mock(status_code=200, content=b"x")
        response.json.return_value = {"data": [], "metadata": {"timestamp": 1, "next_cursor": "same"}}
        session = Mock()
        session.request.return_value = response
        client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
        with self.assertRaises(ApiError):
            client.get_open_orders()
        self.assertEqual(session.request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
