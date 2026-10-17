"""Persistent ETH/EUR collector tests; no network, live client or accounts."""
from __future__ import annotations

from copy import deepcopy
import json
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from eth_shadow_v1.collector import ContinuousReadOnlyCollector
from eth_shadow_v1.collector_store import CollectorStore
from eth_shadow_v1.collector_quality import audit_chain, evaluate_quality
from eth_shadow_v1.config import Config
from scripts.run_eth_eur_shadow import CONFIG
from tests.test_eth_public_readonly import BASE, Session, book, candles, pair
from eth_shadow_v1.public_readonly import PublicOnlyClient


class PersistentReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(CONFIG)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "collector.sqlite"

    def collector(self, fake: Session, clock, owner="fixed-owner"):
        return ContinuousReadOnlyCollector(
            self.cfg, self.path, owner=owner, client=PublicOnlyClient(fake),
            clock_ms=lambda: clock[0], monotonic=lambda: 100.0,
            sleep=lambda _: None)

    def test_persist_01_raw_book_pair_candles_audit_and_restart(self):
        clock = [BASE]
        fake = Session(books=[book(0),book(1),book(2)])
        with self.collector(fake, clock) as collector:
            collector.store.acquire(clock[0])
            collector.poll()
            clock[0] += 5000
            collector.poll()
            collector.store.release(clock[0])
        report = evaluate_quality(self.path, BASE+5000)
        self.assertEqual(report["sample_count"], 2)
        self.assertEqual(report["accepted_count"], 2)
        self.assertEqual(report["closed_candles"], 200)
        self.assertEqual(report["audit"]["status"], "PASS")
        self.assertEqual(report["shadow_forward_status"], "NOT_STARTED")
        self.assertEqual(report["economic_approval"], "NO_GO")
        self.assertIn("WARMUP_UNDER_24H", report["alerts"])
        with sqlite3.connect(self.path) as db:
            raw = json.loads(db.execute(
                "SELECT payload_json FROM books WHERE accepted=1 LIMIT 1").fetchone()[0])
            self.assertEqual(raw["payload"]["metadata"]["region"], "EEA")
            self.assertEqual(raw["payload"]["data"]["bids"][0]["price"], "2999.75")
        with self.collector(fake, clock, owner="another-owner") as resumed:
            resumed.store.acquire(BASE+10000)
            clock[0] += 5000
            resumed.poll()
        report = evaluate_quality(self.path, clock[0])
        self.assertEqual(report["sample_count"], 3)
        self.assertEqual(report["accepted_count"], 3)
        self.assertEqual(report["audit"]["rows_checked"], 3)

    def test_persist_02_single_writer_lease_and_crash_takeover(self):
        with CollectorStore(self.path,"first") as first:
            first.acquire(BASE)
            with CollectorStore(self.path,"second") as second:
                with self.assertRaisesRegex(RuntimeError,"ALREADY_OWNED"):
                    second.acquire(BASE+5000)
                second.acquire(BASE+61000)
                with self.assertRaisesRegex(RuntimeError,"LEASE_LOST"):
                    first.record_book(BASE+62000, None, None, None, "stale")
                second.record_book(BASE+62000, None, None, None, "no-source")
            with sqlite3.connect(self.path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM books").fetchone()[0],1)

    def test_persist_03_pair_rules_drift_blocks_future_accepted_book(self):
        clock=[BASE]
        first=Session(books=[book(0)])
        with self.collector(first,clock) as c:
            c.store.acquire(BASE)
            c.poll()
            c.store.release(BASE)
        modified = pair()
        modified["ETH/EUR"]["base_step"]="0.00001"
        clock[0]+=60000
        new_book=book(0)
        new_book["metadata"]["timestamp"]=clock[0]
        second=Session(pairs=modified,books=[new_book],bars=candles())
        with self.collector(second,clock,owner="second") as c:
            c.store.acquire(clock[0])
            c.poll()
            c.store.release(clock[0])
        q=evaluate_quality(self.path,clock[0])
        self.assertTrue(q["pair_rules_changed"])
        self.assertIn("PAIR_RULES_CHANGED_MANUAL_REVIEW",q["alerts"])
        self.assertEqual(q["accepted_count"],1)
        self.assertEqual(q["rejected_count"],1)

    def test_persist_04_bad_book_preserved_as_rejection(self):
        clock=[BASE]
        crossed=book()
        crossed["data"]["bids"][0]["price"]="5000"
        fake=Session(books=[crossed])
        with self.collector(fake,clock) as c:
            c.store.acquire(BASE)
            c.poll()
            c.store.release(BASE)
        r=evaluate_quality(self.path,BASE)
        self.assertEqual(r["accepted_count"],0)
        self.assertEqual(r["rejected_count"],1)
        self.assertTrue(r["rejection_reasons"])
        self.assertEqual(r["audit"]["status"],"PASS")

    def test_persist_05_tamper_full_book_detected(self):
        clock=[BASE]
        with self.collector(Session(books=[book(0)]),clock) as c:
            c.store.acquire(BASE)
            c.poll()
            c.store.release(BASE)
        with sqlite3.connect(self.path) as db:
            row=db.execute("SELECT seq,payload_json FROM books LIMIT 1").fetchone()
            tampered=json.loads(row[1])
            tampered["payload"]["data"]["bids"][0]["price"]="1"
            db.execute("UPDATE books SET payload_json=? WHERE seq=?",
                       (json.dumps(tampered),row[0]))
            db.commit()
        with self.assertRaisesRegex(ValueError,"TAMPER"):
            evaluate_quality(self.path,BASE)

    def test_persist_06_no_false_24h_pass_after_gap(self):
        clock=[BASE]
        with self.collector(Session(books=[book(0)]),clock) as c:
            c.store.acquire(BASE)
            c.poll()
            c.store.release(BASE)
        report=evaluate_quality(self.path,BASE+86400000)
        self.assertEqual(report["status"],"BLOCKED")
        self.assertIn("SAMPLE_COVERAGE_BELOW_99PCT",report["alerts"])
        self.assertIn("COLLECTOR_HEARTBEAT_STALE",report["alerts"])

    def test_persist_07_persistence_keeps_history_after_reopen(self):
        with CollectorStore(self.path,"owner") as c:
            c.acquire(BASE)
            c.record_book(BASE,None,None,None,"missing")
            c.release(BASE)
        with CollectorStore(self.path,"new") as c:
            c.acquire(BASE+5000)
            c.record_book(BASE+5000,None,None,None,"missing")
            c.release(BASE+5000)
        report=evaluate_quality(self.path,BASE+5000)
        self.assertEqual(report["sample_count"],2)
        self.assertEqual(report["audit"]["rows_checked"],2)

    def test_persist_08_capture_is_public_get_only(self):
        clock=[BASE]
        fake=Session(books=[book(0),book(1)])
        with self.collector(fake,clock) as c:
            c.store.acquire(BASE)
            c.poll()
            clock[0]+=5000
            c.poll()
            c.store.release(clock[0])
        self.assertEqual(len(fake.calls),4)
        for url,kwargs in fake.calls:
            self.assertIn("/public/", url)
            self.assertTrue(url.startswith("https://revx.revolut.com/api/"))
            self.assertFalse(kwargs["allow_redirects"])
            self.assertNotIn("Authorization",kwargs["headers"])

if __name__=="__main__":
    unittest.main()
