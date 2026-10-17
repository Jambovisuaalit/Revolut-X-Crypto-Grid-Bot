"""Revolut X authenticated GET-only account reader contract and failure tests.

Every HTTP response is mocked. No API credentials, network, orders or money.
"""
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from account_readonly import (
    AccountReadError, AuthenticatedAccountReader, BASE_URL,
    _balances, _order, ACTIVE, BALANCES, HISTORY,
)


TIME = 1_792_214_000_000
KEY = "A" * 64


def order(id=None):
    return {
        "id": id or str(uuid4()), "client_order_id": str(uuid4()),
        "symbol": "ETH/EUR", "side": "buy", "status": "new", "type": "limit",
        "price": "2000.01", "quantity": "0.025", "filled_quantity": "0.005",
        "leaves_quantity": "0.020",
    }


def balance():
    return [
        {"currency":"EUR","available":"997.5","reserved":"2.5","total":"1000"},
        {"currency":"ETH","available":"0.01","reserved":"0.01",
         "staked":"0.02","total":"0.04"},
    ]


def page(orders=None, cursor=None):
    return {"data": orders if orders is not None else [],
            "metadata": {"timestamp": TIME, **({"next_cursor": cursor} if cursor else {})}}


class Response:
    def __init__(self, data=None, *, status=200, headers=None, raw=None):
        self.content = raw if raw is not None else json.dumps(data).encode("utf-8")
        self.status_code = status
        self.headers = headers or {}


class Transport:
    """No POST, PUT, DELETE or generic request method."""
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.outputs:
            raise AssertionError("mock GET queue exhausted")
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output


class AccountReaderTests(unittest.TestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()

    def reader(self, transport, *, sleeps=None, clock=lambda: TIME):
        return AuthenticatedAccountReader(
            KEY, self.private_key, transport=transport, clock_ms=clock,
            sleeper=(sleeps.append if sleeps is not None else lambda _:None),
        )

    def test_01_full_snapshot_three_gets_not_reconciled(self):
        t = Transport([Response(balance()), Response(page([order()])), Response(page([]))])
        result = self.reader(t).snapshot()
        self.assertEqual(len(result.balances),2)
        self.assertEqual(result.balances[1].staked,Decimal("0.02"))
        self.assertEqual(len(result.active_orders),1)
        self.assertEqual(result.active_pages,1)
        self.assertEqual(result.historical_pages,1)
        self.assertFalse(result.reconciled_for_trading)
        self.assertEqual(result.live_trading_approval,"NO_GO")
        self.assertEqual(result.data_provenance,"SIGNED_GET_NOT_ACCOUNT_VERIFIED")
        self.assertEqual([urlsplit(u).path for u,_ in t.calls],
                         ["/api"+BALANCES, "/api"+ACTIVE, "/api"+HISTORY])
        for url,kwargs in t.calls:
            self.assertEqual(urlsplit(url).scheme,"https")
            self.assertEqual(urlsplit(url).netloc,"revx.revolut.com")
            self.assertIs(kwargs["allow_redirects"],False)
            self.assertEqual(kwargs["timeout"],10)
            self.assertEqual(kwargs["params"],None)

    def test_02_exact_ed25519_signature_for_encoded_cursor(self):
        t = Transport([Response(page([order()], cursor="ab+/==")),
                       Response(page([order()]))])
        rows=self.reader(t).active_orders()
        self.assertEqual(len(rows),2)
        for url,kwargs in t.calls:
            split=urlsplit(url)
            signed=kwargs["headers"]
            self.assertEqual(set(signed).intersection({
                "X-Revx-API-Key","X-Revx-Timestamp","X-Revx-Signature"
            }),{"X-Revx-API-Key","X-Revx-Timestamp","X-Revx-Signature"})
            self.assertEqual(signed["X-Revx-Timestamp"],str(TIME))
            message=f"{TIME}GET{split.path}{split.query}".encode()
            self.private_key.public_key().verify(
                base64.b64decode(signed["X-Revx-Signature"]),message
            )
        self.assertIn("cursor=ab%2B%2F%3D%3D", t.calls[1][0])
        self.assertEqual(parse_qs(urlsplit(t.calls[1][0]).query)["cursor"],["ab+/=="])

    def test_03_paginated_history_reads_all(self):
        t=Transport([Response(page([order()],cursor="next")),
                     Response(page([order()]))])
        rows=self.reader(t).historical_orders()
        self.assertEqual(len(rows),2)
        self.assertEqual(len(t.calls),2)

    def test_04_repeated_cursor_or_duplicate_order_halts(self):
        same=order()
        for outputs in (
            [Response(page([order()],cursor="same")),
             Response(page([order()],cursor="same"))],
            [Response(page([same],cursor="next")),
             Response(page([same]))],
        ):
            with self.subTest(outputs=outputs):
                t=Transport(outputs)
                with self.assertRaises(AccountReadError):
                    self.reader(t).active_orders()
                self.assertEqual(len(t.calls),2)

    def test_05_invalid_balances_and_json_fail_closed(self):
        invalid=[
            {"data":balance()},
            [{"currency":"EUR","available":"NaN","reserved":"1","total":"1"}],
            [{"currency":"EUR","available":"1","reserved":"1","total":"100"}],
            [{"currency":"EUR","available":"-1","reserved":"2","total":"1"}],
            [{"currency":"EUR","available":1,"reserved":"0","total":"1"}],
            [balance()[0],balance()[0]],
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(AccountReadError):
                    self.reader(Transport([Response(payload)])).balances()

    def test_06_invalid_orders_block_all_page_results(self):
        wrong=order()
        wrong["leaves_quantity"]="100"
        bad_uuid=order(id="id/no")
        unknown=order()
        unknown["status"]="unspecified_hazard"
        for item in (wrong,bad_uuid,unknown):
            with self.subTest(item=item):
                with self.assertRaises(AccountReadError):
                    self.reader(Transport([Response(page([item]))])).active_orders()

    def test_07_redirect_and_bad_auth_never_follow(self):
        for status in (301,302,307,308,401,403,409,500,503):
            t=Transport([Response({},status=status)])
            with self.subTest(status=status):
                with self.assertRaises(AccountReadError):
                    self.reader(t).balances()
                self.assertEqual(len(t.calls),1)
                self.assertFalse(t.calls[0][1]["allow_redirects"])

    def test_08_rate_limit_retry_after_is_bounded(self):
        sleeps=[]
        t=Transport([Response({},status=429,headers={"Retry-After":"250"}),
                     Response(balance())])
        self.assertEqual(len(self.reader(t,sleeps=sleeps).balances()),2)
        self.assertEqual(sleeps,[0.25])
        self.assertEqual(len(t.calls),2)
        for retry_after in ("-1","infinity","20000",""):
            with self.subTest(retry_after=retry_after):
                u=Transport([Response({},status=429,headers={"Retry-After":retry_after})])
                with self.assertRaises(AccountReadError):
                    self.reader(u,sleeps=[]).balances()
                self.assertEqual(len(u.calls),1)
        v=Transport([Response({},status=429,headers={"Retry-After":"1"}) for _ in range(3)])
        with self.assertRaises(AccountReadError):
            self.reader(v,sleeps=[]).balances()
        self.assertEqual(len(v.calls),3)

    def test_09_transport_failure_and_invalid_payload_never_leak_secrets(self):
        for payload in (
            requests.Timeout("SECRET do not leak"),
            Response(raw=b"invalid json"),
            Response(raw=b"x"*(2*1024*1024+1)),
            Response(raw=b""),
        ):
            with self.subTest(payload=type(payload).__name__):
                reader=self.reader(Transport([payload]))
                with self.assertRaises(AccountReadError) as error:
                    reader.balances()
                self.assertNotIn("SECRET",str(error.exception))
                self.assertNotIn(KEY,str(error.exception))
                self.assertNotIn(KEY,repr(reader))

    def test_10_details_and_fills_use_canonical_uuid(self):
        record=order()
        tid="trade-123"
        f={"tid":tid,"p":"2000.1","q":"0.02","pc":"EUR","qc":"ETH"}
        t=Transport([Response({"data":record}),
                     Response({"data":[f],"metadata":{"timestamp":"2026-10-17T00:00:00Z"}})])
        reader=self.reader(t)
        self.assertEqual(reader.order_details(record["id"])["id"],record["id"])
        self.assertEqual(reader.order_fills(record["id"])[0]["tid"],tid)
        for url,_ in t.calls:
            self.assertEqual(urlsplit(url).netloc,"revx.revolut.com")
        before=len(t.calls)
        for suspect in ("../orders",record["id"]+"?x=1","", "a"*36):
            with self.subTest(suspect=suspect),self.assertRaises(AccountReadError):
                reader.order_details(suspect)
        self.assertEqual(len(t.calls),before)

    def test_11_fills_reject_bad_and_duplicate(self):
        uid=str(uuid4())
        for rows in ([{"tid":"x","p":"-1","q":"2"}],
                     [{"tid":"x","p":"1","q":"2"},{"tid":"x","p":"1","q":"2"}],
                     [{"tid":"x","p":"1","q":"0"}]):
            with self.subTest(rows=rows):
                with self.assertRaises(AccountReadError):
                    self.reader(Transport([Response({"data":rows})])).order_fills(uid)

    def test_12_path_allowlist_blocks_all_mutations_and_unsafe_paths(self):
        t=Transport([])
        reader=self.reader(t)
        for path in (
            "/1.0/orders","/1.0/orders/active/../cancel",
            "/1.0/orders/../../1.0/balances","/1.0/wallet/withdraw",
            "/1.0/orders/fills/invalid",
        ):
            with self.subTest(path=path),self.assertRaises(AccountReadError):
                reader._get(path)
        self.assertEqual(t.calls,[])
        self.assertFalse(hasattr(reader,"place_order"))
        self.assertFalse(hasattr(reader,"cancel_all_orders"))
        self.assertFalse(hasattr(reader,"replace_order"))
        self.assertFalse(hasattr(reader,"request"))
        import account_readonly
        raw=Path(account_readonly.__file__).read_text("utf-8")
        self.assertNotIn("from revolut_x_client import",raw)
        self.assertNotIn("session.request(",raw)
        self.assertNotIn("session.post(",raw)

    def test_13_snapshot_is_all_or_nothing(self):
        t=Transport([Response(balance()),Response(page([order()])),Response({"data":[]})])
        with self.assertRaises(AccountReadError):
            self.reader(t).snapshot()
        self.assertEqual(len(t.calls),3)

    def test_14_invalid_api_credentials_and_clock_block_before_transport(self):
        with self.assertRaises(ValueError):
            AuthenticatedAccountReader("short",self.private_key,transport=Transport([]))
        with self.assertRaises(TypeError):
            AuthenticatedAccountReader(KEY,"not-a-private-key",transport=Transport([]))
        t=Transport([])
        with self.assertRaises(AccountReadError):
            self.reader(t,clock=lambda:12).balances()
        self.assertEqual(t.calls,[])

    def test_15_nonterminal_cursor_is_not_completeness_proof(self):
        outputs=[Response(page([order()],cursor="cursor"+str(i))) for i in range(100)]
        t=Transport(outputs)
        with self.assertRaisesRegex(AccountReadError,"not provably complete"):
            self.reader(t).active_orders()
        self.assertEqual(len(t.calls),100)


if __name__=="__main__":
    unittest.main()
