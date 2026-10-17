"""No real Revolut X calls. Test all pilot bounds using deterministic GET mocks."""
from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
import unittest
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from account_readonly import AccountReadError, ReadLimits
from account_pilot_gate import (
    BoundedPilotSession, GuardedGetTransport, PilotBounds, PilotGateError,
)
from tests.test_authenticated_account_readonly import Response, Transport, balance, order, page, TIME, KEY


class PilotGateTests(unittest.TestCase):
    def make(self, responses, *, bounds=PilotBounds()):
        transport = Transport(responses)
        session = BoundedPilotSession(
            KEY, Ed25519PrivateKey.generate(), transport=transport,
            bounds=bounds, clock_ms=lambda: TIME,
        )
        return session, transport

    def test_01_three_get_snapshot_all_or_nothing(self):
        s, t = self.make([Response(balance()), Response(page([order()])),
                          Response(page([]))])
        snapshot = s.run_once()
        self.assertEqual(len(snapshot.active_orders), 1)
        self.assertEqual(s.status, "SCHEMA_PARSED_NOT_RECONCILED")
        self.assertEqual(s.guard.attempts, 3)
        self.assertTrue(s.guard.blocked)
        self.assertFalse(snapshot.reconciled_for_trading)
        self.assertEqual(s.report()["live_trading_approval"], "NO_GO")
        self.assertFalse(s.report()["account_scope_verified"])
        self.assertNotIn("balances", s.report())
        self.assertNotIn(KEY, json.dumps(s.report()))
        self.assertEqual(len(t.calls), 3)
        with self.assertRaisesRegex(PilotGateError, "CANNOT_RESTART"):
            s.run_once()
        self.assertEqual(len(t.calls), 3)

    def test_02_max_requests_counts_attempts_before_network(self):
        s, t = self.make([Response(balance()), Response(page([])),
                          Response(page([]))], bounds=PilotBounds(max_attempts=2))
        with self.assertRaisesRegex(PilotGateError, "SNAPSHOT_REJECTED"):
            s.run_once()
        self.assertEqual(s.guard.attempts, 2)
        self.assertEqual(len(t.calls), 2)
        self.assertEqual(s.report()["status"], "BLOCKED")
        self.assertEqual(s.report()["get_attempts"], 2)
        with self.assertRaises(PilotGateError):
            s.run_once()
        self.assertEqual(len(t.calls), 2)

    def test_03_pagination_hard_stop_before_eleventh_page(self):
        pages = [Response(balance())]
        for n in range(1, 11):
            pages.append(Response(page([order()], cursor=f"next{n}")))
        s, t = self.make(pages, bounds=PilotBounds(max_pages_per_list=10))
        with self.assertRaises(PilotGateError):
            s.run_once()
        self.assertEqual(s.guard.attempts, 11)  # 1 balance + 10 active pages
        self.assertEqual(len(t.calls), 11)
        self.assertEqual(s.status, "BLOCKED")

    def test_04_retry_after_429_is_not_retried_in_pilot(self):
        s, t = self.make([Response({}, status=429, headers={"Retry-After":"250"}),
                          Response(balance())])
        with self.assertRaises(PilotGateError):
            s.run_once()
        self.assertEqual(s.guard.attempts, 1)
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(s.report()["status"], "BLOCKED")

    def test_05_api_auth_and_server_errors_immediately_block(self):
        for status in (401,403,409,500,503):
            with self.subTest(status=status):
                s,t=self.make([Response({},status=status),Response(balance())])
                with self.assertRaises(PilotGateError):
                    s.run_once()
                self.assertEqual(len(t.calls),1)
                self.assertEqual(s.guard.attempts,1)
                self.assertEqual(s.status,"BLOCKED")

    def test_06_partial_schema_never_returned(self):
        s,t=self.make([Response(balance()),Response(page([order()])),
                       Response({"data":[]})])
        with self.assertRaises(PilotGateError):
            s.run_once()
        self.assertEqual(s.status,"BLOCKED")
        self.assertEqual(s.guard.attempts,3)
        self.assertEqual(len(t.calls),3)
        self.assertNotIn("active_orders", s.report())

    def test_07_transport_exception_counts_and_redacts_secrets(self):
        import requests
        s,t=self.make([requests.Timeout("SECRET-CUSTOMER-KEY")])
        with self.assertRaises(PilotGateError) as cm:
            s.run_once()
        self.assertNotIn("SECRET",str(cm.exception))
        self.assertEqual(s.guard.attempts,1)
        self.assertTrue(s.guard.blocked)

    def test_08_invalid_bounds_rejected(self):
        for args in (
            {"max_attempts":0}, {"max_attempts":26},
            {"max_attempts":True}, {"max_pages_per_list":0},
            {"max_pages_per_list":11},
        ):
            with self.subTest(args=args),self.assertRaises(ValueError):
                PilotBounds(**args)
        with self.assertRaises(ValueError):
            ReadLimits(max_pages_per_list=11)
        with self.assertRaises(ValueError):
            ReadLimits(max_rate_retries=2)

    def test_09_only_fixed_host_and_get_allowlist(self):
        attempts=[
            "https://revx.revolut.com.evil.test/api/1.0/balances",
            "http://revx.revolut.com/api/1.0/balances",
            "https://revx.revolut.com/api/1.0/orders",
            "https://revx.revolut.com/api/1.0/orders/%2e%2e/active",
            "https://revx.revolut.com/api/1.0/balances?unexpected=1",
            "https://revx.revolut.com/api/1.0/orders/active?limit=999",
            "https://revx.revolut.com/api/1.0/orders/active?limit=100&x=1",
            "https://revx.revolut.com/api/1.0/orders/fills/not_uuid",
            "https://revx.revolut.com/api/1.0/balances#fragment",
        ]
        for url in attempts:
            with self.subTest(url=url):
                guard=GuardedGetTransport(Transport([]))
                with self.assertRaises(PilotGateError):
                    guard.get(url, headers={}, timeout=10,
                              allow_redirects=False, stream=True)
                self.assertEqual(guard.attempts,0)
                self.assertTrue(guard.blocked)

    def test_10_parallel_attempt_budget_cannot_be_bypassed(self):
        headers={
            "X-Revx-API-Key":KEY,
            "X-Revx-Timestamp":str(TIME),
            "X-Revx-Signature":"ZmFrZQ==",
            "Accept":"application/json",
            "User-Agent":"GridConsoleReadOnly/0.1",
        }
        class CountingTransport:
            def __init__(self):
                self.n=0
            def get(self,*args,**kwargs):
                self.n+=1
                return Response(balance())
        target=CountingTransport()
        guard=GuardedGetTransport(target,max_attempts=3)
        def task(_):
            try:
                response=guard.get("https://revx.revolut.com/api/1.0/balances",
                                   headers=headers,timeout=10,
                                   allow_redirects=False,stream=True)
                response.close()
                return True
            except PilotGateError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            done=list(pool.map(task,range(20)))
        self.assertEqual(sum(done),3)
        self.assertEqual(guard.attempts,3)
        self.assertEqual(target.n,3)
        self.assertTrue(guard.blocked)

    def test_11_no_mutation_routes_or_silent_real_transport(self):
        s,t=self.make([])
        self.assertFalse(hasattr(s.reader,"place_order"))
        self.assertFalse(hasattr(s.reader,"cancel_all_orders"))
        self.assertFalse(hasattr(s.guard,"post"))
        self.assertFalse(hasattr(s.guard,"request"))
        self.assertFalse(hasattr(s.guard,"delete"))
        with self.assertRaises(TypeError):
            BoundedPilotSession(KEY,Ed25519PrivateKey.generate(),transport=None)

    def test_12_only_one_attempt_per_route_and_schema_redacted(self):
        s,t=self.make([Response(balance()),Response(page([order()])),
                       Response(page([order()]))])
        snapshot=s.run_once()
        report=s.report()
        self.assertEqual(report["get_attempts"],3)
        self.assertEqual(report["mutations_sent"],0)
        self.assertEqual(report["orders_placed"],0)
        self.assertEqual(report["owner_ui_comparison"],"NOT_PERFORMED")
        self.assertEqual(report["status"],"SCHEMA_PARSED_NOT_RECONCILED")
        self.assertNotIn("api_key",report)
        self.assertNotIn("ETH",json.dumps(report))
        self.assertEqual(snapshot.live_trading_approval,"NO_GO")


class DurablePilotLedgerTests(unittest.TestCase):
    """Local mock-only crash/restart budgets, independent of account credentials."""

    def test_13_success_seals_ledger_and_prevents_new_session(self):
        import tempfile
        from pilot_budget_store import DurablePilotLedger, PilotLedgerError
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"pilot-once.sqlite"
            with DurablePilotLedger(db) as ledger:
                transport=Transport([Response(balance()),Response(page([])),Response(page([]))])
                s=BoundedPilotSession(KEY,Ed25519PrivateKey.generate(),
                                      transport=transport,ledger=ledger,clock_ms=lambda:TIME)
                snapshot=s.run_once()
                self.assertEqual(s.guard.attempts,3)
                self.assertEqual(ledger.attempts(),3)
                self.assertEqual(s.status,"SCHEMA_PARSED_NOT_RECONCILED")
            with self.assertRaisesRegex(PilotLedgerError,"ALREADY_EXISTS"):
                DurablePilotLedger(db)

    def test_14_timeout_consumes_durable_attempt_and_restart_not_rearmed(self):
        import tempfile
        import requests
        from pilot_budget_store import DurablePilotLedger, PilotLedgerError
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"pilot-crash.sqlite"
            with DurablePilotLedger(db) as ledger:
                transport=Transport([requests.Timeout("secret")])
                s=BoundedPilotSession(KEY,Ed25519PrivateKey.generate(),
                                      transport=transport,ledger=ledger,clock_ms=lambda:TIME)
                with self.assertRaises(PilotGateError):
                    s.run_once()
                self.assertEqual(ledger.attempts(),1)
            with self.assertRaises(PilotLedgerError):
                DurablePilotLedger(db)

    def test_15_durable_budget_overrides_volatile_allowance(self):
        import tempfile
        from pilot_budget_store import DurablePilotLedger
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"pilot-two.sqlite"
            with DurablePilotLedger(db,max_attempts=2) as ledger:
                transport=Transport([Response(balance()),Response(page([])),
                                     Response(page([]))])
                s=BoundedPilotSession(KEY,Ed25519PrivateKey.generate(),
                                      transport=transport,ledger=ledger,clock_ms=lambda:TIME)
                with self.assertRaises(PilotGateError):
                    s.run_once()
                self.assertEqual(ledger.attempts(),2)
                self.assertEqual(len(transport.calls),2)
                self.assertEqual(s.status,"BLOCKED")

    def test_16_process_crash_without_seal_requires_manual_review(self):
        import tempfile
        from pilot_budget_store import DurablePilotLedger, PilotLedgerError
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"pilot-interrupted.sqlite"
            ledger=DurablePilotLedger(db)
            self.assertEqual(ledger.reserve_before_get(),1)
            ledger.close()  # simulate process stop before HTTP response
            with self.assertRaisesRegex(PilotLedgerError,"ALREADY_EXISTS"):
                DurablePilotLedger(db)

    def test_17_live_mode_and_bot_database_rejected(self):
        import tempfile
        from pilot_budget_store import DurablePilotLedger, PilotLedgerError
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(PilotLedgerError,"REAL_ACCOUNT"):
                DurablePilotLedger(Path(td)/"fixture.sqlite",mode="REAL_ACCOUNT")
            with self.assertRaisesRegex(PilotLedgerError,"LIVE_DATABASE"):
                DurablePilotLedger(Path(td)/"bot_state.db")

    def test_18_durable_reservation_thread_safety(self):
        import tempfile
        from pilot_budget_store import DurablePilotLedger, PilotLedgerError
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"pilot-parallel.sqlite"
            with DurablePilotLedger(db,max_attempts=5) as ledger:
                def reserve(_):
                    try:
                        ledger.reserve_before_get()
                        return True
                    except PilotLedgerError:
                        return False
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results=list(pool.map(reserve,range(40)))
                self.assertEqual(sum(results),5)
                self.assertEqual(ledger.attempts(),5)

    def test_19_invalid_existing_foreign_database_not_modified(self):
        import tempfile
        from pilot_budget_store import DurablePilotLedger, PilotLedgerError
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"existing.sqlite"
            db.write_text("opaque existing file",encoding="utf-8")
            before=db.read_bytes()
            with self.assertRaises(Exception):
                DurablePilotLedger(db)
            self.assertEqual(db.read_bytes(),before)


if __name__=="__main__":
    unittest.main()
