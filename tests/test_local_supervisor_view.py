"""Read-only supervisor journal + localhost contract. No exchange/account calls."""
from __future__ import annotations

from contextlib import closing
import http.client
import json
from pathlib import Path
import sqlite3
from threading import Thread
import tempfile
import unittest

from product_shell.local_runtime_readonly import inspect_local_runtime
from product_shell.server import local_server

NOW = 1_792_214_000_000


class LocalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "offline-supervisor.sqlite"
        with closing(sqlite3.connect(self.db)) as con:
            con.executescript("""
                CREATE TABLE bot_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE order_lineage(
                    client_order_id TEXT,venue_order_id TEXT,side TEXT NOT NULL,
                    status TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE fills_wire(fill_id TEXT,venue_order_id TEXT,
                    created_at TEXT NOT NULL);
            """)
            con.commit()

    def put_meta(self, **values):
        with closing(sqlite3.connect(self.db)) as con:
            for k,v in values.items():
                con.execute("INSERT OR REPLACE INTO bot_metadata(key,value) VALUES(?,?)",
                            (k,str(v)))
            con.commit()

    def put_order(self, status="new", side="buy", order_id="SECRET-ORDER-1234"):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(
                "INSERT INTO order_lineage(client_order_id,venue_order_id,side,status,created_at) "
                "VALUES(?,?,?,?,?)",
                (order_id, "SECRET-VENUE-UUID", side, status, "2026-10-17 08:00:00")
            )
            con.commit()

    def test_01_unconfigured_is_not_running_or_verified(self):
        result=inspect_local_runtime(None,NOW)
        self.assertEqual(result["status"],"NOT_CONFIGURED")
        self.assertEqual(result["risk_lock"],"UNKNOWN")
        self.assertFalse(result["reconciled_for_trading"])
        self.assertEqual(result["live_orders"],"DISABLED")
        self.assertEqual(result["events"],[])

    def test_02_db_with_schema_but_no_supervisor_is_not_started(self):
        result=inspect_local_runtime(self.db,NOW)
        self.assertEqual(result["status"],"NOT_STARTED")
        self.assertEqual(result["uncertain_order_count"],0)
        self.assertEqual(result["risk_lock"],"UNKNOWN")

    def test_03_stop_state_reads_heartbeat_and_cancellation(self):
        self.put_meta(supervisor_state="STOPPED",
                      supervisor_reason="OPERATOR_STOP",
                      supervisor_heartbeat_ms=NOW-500,
                      supervisor_lease_until_ms=0,
                      kill_switch_triggered="false",
                      cancellation_verification_status="VERIFIED_NO_ACTIVE_ORDERS")
        status=inspect_local_runtime(self.db,NOW)
        self.assertEqual(status["status"],"STOPPED")
        self.assertEqual(status["heartbeat_age_ms"],500)
        self.assertEqual(status["heartbeat"],"FRESH")
        self.assertEqual(status["risk_lock"],"NOT_TRIGGERED")
        self.assertEqual(status["cancellation"],"VERIFIED_NO_ACTIVE_ORDERS")
        self.assertFalse(status["reconciled_for_trading"])

    def test_04_drawdown_risk_lock_overrides_stopped(self):
        self.put_meta(supervisor_state="STOPPED",supervisor_reason="HARD_DRAWDOWN_3PCT",
                      kill_switch_triggered="true")
        status=inspect_local_runtime(self.db,NOW)
        self.assertEqual(status["status"],"RISK_LOCKED")
        self.assertEqual(status["risk_lock"],"TRIGGERED")
        self.assertIn("kolmen", status["reason_label"].lower())

    def test_05_active_lease_expiry_not_misreported_as_running(self):
        self.put_meta(supervisor_state="OFFLINE_MONITORING",
                      supervisor_heartbeat_ms=NOW-30_000,
                      supervisor_lease_until_ms=NOW-15_000,
                      kill_switch_triggered="false")
        status=inspect_local_runtime(self.db,NOW)
        self.assertEqual(status["status"],"BLOCKED")
        self.assertEqual(status["heartbeat"],"STALE")
        self.assertEqual(status["reason"],"LEASE_EXPIRED_OR_HEARTBEAT_STALE")

    def test_06_uncertain_order_blocks_even_if_supervisor_stopped(self):
        self.put_meta(supervisor_state="STOPPED",kill_switch_triggered="false")
        self.put_order("pending_new")
        status=inspect_local_runtime(self.db,NOW)
        self.assertEqual(status["status"],"BLOCKED")
        self.assertEqual(status["reason"],"UNCERTAIN_LOCAL_ORDERS")
        self.assertEqual(status["uncertain_order_count"],1)
        self.assertTrue(status["events"][0]["uncertain"])

    def test_07_local_journal_redacts_ids_prices_and_balances(self):
        self.put_meta(supervisor_state="STOPPED",supervisor_reason="OPERATOR_STOP")
        self.put_order()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("INSERT INTO fills_wire(fill_id,venue_order_id,created_at) "
                        "VALUES(?,?,?)",
                        ("SECRET-FILL-ID","SECRET-VENUE-UUID","2026-10-17 08:01:00"))
            con.commit()
        result=inspect_local_runtime(self.db,NOW)
        self.assertEqual(result["order_event_count"],1)
        self.assertEqual(result["fill_event_count"],1)
        self.assertEqual(len(result["events"]),2)
        self.assertEqual(result["events"][0]["category"],"FILL_LOCAL")
        out=json.dumps(result)
        for forbidden in ("SECRET-ORDER-1234","SECRET-VENUE-UUID",
                          "SECRET-FILL-ID",str(self.db)):
            self.assertNotIn(forbidden,out)
        self.assertIn("LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED",out)

    def test_08_invalid_metadata_and_unsafe_strings_fail_closed(self):
        for k,v in (("supervisor_state","LIVE"),
                    ("supervisor_state","<script>attack</script>"),
                    ("kill_switch_triggered","UNKNOWN"),
                    ("supervisor_heartbeat_ms","yesterday")):
            with self.subTest(k=k,v=v):
                with closing(sqlite3.connect(self.db)) as con:
                    con.execute("DELETE FROM bot_metadata")
                    con.execute("INSERT INTO bot_metadata(key,value) VALUES (?,?)",(k,v))
                    con.commit()
                result=inspect_local_runtime(self.db,NOW)
                self.assertEqual(result["status"],"BLOCKED")
                if v not in ("UNKNOWN",):
                    self.assertNotIn(v,json.dumps(result))

    def test_09_corrupted_foreign_schema_and_missing_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            corrupt=Path(td)/"corrupt.sqlite"
            corrupt.write_text("not sqlite",encoding="utf-8")
            foreign=Path(td)/"foreign.sqlite"
            with closing(sqlite3.connect(foreign)) as con:
                con.execute("CREATE TABLE random_table(x TEXT)")
                con.commit()
            for path in (corrupt, foreign, Path(td)/"nonexistent.sqlite"):
                with self.subTest(path=path):
                    report=inspect_local_runtime(path,NOW)
                    self.assertEqual(report["status"],"BLOCKED")
                    self.assertNotIn(str(path),json.dumps(report))

    def test_10_read_only_does_not_modify_db_file(self):
        self.put_meta(supervisor_state="STOPPED",kill_switch_triggered="false")
        before=self.db.read_bytes()
        inspect_local_runtime(self.db,NOW)
        after=self.db.read_bytes()
        self.assertEqual(before,after)

    def test_11_max_events_bounded(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.executemany(
                "INSERT INTO order_lineage(client_order_id,venue_order_id,side,status,created_at) "
                "VALUES(?,?,?,?,?)",
                [(f"id-{i}",None,"buy","filled","2026-10-17 08:00:00") for i in range(90)])
            con.commit()
        result=inspect_local_runtime(self.db,NOW)
        self.assertEqual(len(result["events"]),40)
        self.assertEqual(result["order_event_count"],40)
        self.assertNotIn("id-89",json.dumps(result))

    def test_12_unknown_order_status_blocks_without_raw_value(self):
        self.put_order(status="TOP_SECRET_MALFORMED_STATUS")
        status=inspect_local_runtime(self.db,NOW)
        self.assertEqual(status["status"],"BLOCKED")
        self.assertNotIn("TOP_SECRET",json.dumps(status))

    def test_13_hidden_old_bad_order_cannot_escape_capped_journal_validation(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("INSERT INTO order_lineage(client_order_id,venue_order_id,side,status,created_at) "
                        "VALUES(?,?,?,?,?)",("old-secret",None,"buy","UNSAFE_LEGACY_STATUS","2000-01-01 00:00:00"))
            con.executemany(
                "INSERT INTO order_lineage(client_order_id,venue_order_id,side,status,created_at) "
                "VALUES(?,?,?,?,?)",
                [(f"new-{i}",None,"buy","filled","2026-10-17 08:00:00") for i in range(45)])
            con.commit()
        result=inspect_local_runtime(self.db,NOW)
        self.assertEqual(result["status"],"BLOCKED")
        self.assertNotIn("UNSAFE_LEGACY_STATUS",json.dumps(result))

    def test_14_loopback_http_api_runtime_is_only_readable(self):
        self.put_meta(supervisor_state="STOPPED",kill_switch_triggered="true",
                      supervisor_reason="HARD_DRAWDOWN_3PCT")
        server=local_server(None,0,clock=lambda:NOW,supervisor_db=self.db)
        worker=Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            def request(method,path,headers=None):
                with closing(http.client.HTTPConnection(
                    "127.0.0.1",server.server_port,timeout=4)) as conn:
                    conn.request(method,path,headers=headers or {})
                    resp=conn.getresponse()
                    return resp.status,resp.read(),dict(resp.getheaders())
            code,data,headers=request("GET","/api/runtime")
            self.assertEqual(code,200)
            self.assertEqual(headers["Cache-Control"],"no-store")
            self.assertEqual(json.loads(data)["status"],"RISK_LOCKED")
            for method in ("POST","PUT","PATCH","DELETE"):
                self.assertEqual(request(method,"/api/runtime")[0],405)
            self.assertEqual(request("GET","/api/runtime?x=1")[0],400)
            self.assertEqual(request("GET","/api/runtime",{"Host":"attacker.test"})[0],403)
            self.assertEqual(request("HEAD","/api/runtime")[1],b"")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

    def test_15_healthy_lease_still_only_offline_monitored_not_live(self):
        self.put_meta(supervisor_state="OFFLINE_MONITORING",
                      supervisor_heartbeat_ms=NOW-500,
                      supervisor_lease_until_ms=NOW+10000,
                      kill_switch_triggered="false",
                      supervisor_reason="SYNTHETIC_FIXTURE_ONLY")
        report=inspect_local_runtime(self.db,NOW)
        self.assertEqual(report["status"],"OFFLINE_MONITORING")
        self.assertFalse(report["reconciled_for_trading"])
        self.assertEqual(report["account_connection"],"NOT_CONNECTED")


if __name__ == "__main__":
    unittest.main()
