"""SQLite multi-connection race tests; all exchange mutations are fake/offline.

Two GridExecutor instances represent independent processes with distinct
SQLite connections and Python locks, sharing one local database file.
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from grid_executor import GridExecutor, ReconciliationError
from precision_engine import PrecisionError
from test_bot import FakeClient, rules, uid


class AtomicIntentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "state.sqlite")
        self.venue = FakeClient()
        self.a = GridExecutor(self.venue, {"BTC/USD": rules()},
                              database_path=self.db, live_trading=True)
        self.b = GridExecutor(self.venue, {"BTC/USD": rules()},
                              database_path=self.db, live_trading=True)
        self.a.reconcile_open_orders()
        self.b.reconcile_open_orders()

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.temp.cleanup()

    @staticmethod
    def place(executor, cid, *, amount="0.01", free="100000"):
        return executor.create_order(
            client_order_id=cid, symbol="BTC/USD", side="buy",
            price=Decimal("100"), quantity=Decimal(amount),
            free_balances={"USD": Decimal(free)},
        )

    def test_same_client_id_during_inflight_post_never_sends_twice(self):
        original = self.venue.place_order
        entered = threading.Event()
        release = threading.Event()

        def delayed(payload):
            entered.set()
            if not release.wait(5):
                raise AssertionError("mock send held for too long")
            return original(payload)

        self.venue.place_order = delayed
        client_id = uid()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.place, self.a, client_id)
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(ReconciliationError):
                    self.place(self.b, client_id)
            finally:
                release.set()
            result = first.result(timeout=5)
        self.assertEqual(len(self.venue.placed), 1)
        self.assertEqual(result["client_order_id"], client_id)
        self.assertEqual(
            self.a.connection.execute(
                "SELECT count(*) FROM order_lineage WHERE client_order_id=?",
                (client_id,),
            ).fetchone()[0], 1
        )
        self.assertTrue(self.place(self.b, client_id)["idempotent"])
        self.assertEqual(len(self.venue.placed), 1)

    def test_other_new_order_blocks_while_first_post_is_inflight(self):
        original = self.venue.place_order
        entered, release = threading.Event(), threading.Event()

        def delayed(payload):
            entered.set()
            if not release.wait(5):
                raise AssertionError("venue mock timeout")
            return original(payload)

        self.venue.place_order = delayed
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.place, self.a, uid())
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(ReconciliationError):
                    self.place(self.b, uid())
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual(len(self.venue.placed), 1)

    def test_atomic_reservation_from_distinct_connections(self):
        # Reproducibly build 95 USD of active intents using valid 5% orders.
        # 100 USD free snapshot: at most one further 4 USD order is possible.
        for _ in range(19):
            self.place(self.a, uid(), amount="0.05", free="100")
        self.b.reconcile_open_orders()
        entered, release = threading.Event(), threading.Event()
        original_funding = self.a._assert_funding

        def delayed_funding(*args, **kwargs):
            # This callback is reached only AFTER BEGIN IMMEDIATE.
            entered.set()
            if not release.wait(5):
                raise AssertionError("SQLite lock test timed out")
            return original_funding(*args, **kwargs)

        self.a._assert_funding = delayed_funding
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.place, self.a, uid(), amount="0.04", free="100")
            self.assertTrue(entered.wait(5))
            second_started = threading.Event()

            def second():
                second_started.set()
                return self.place(self.b, uid(), amount="0.04", free="100")

            other = pool.submit(second)
            self.assertTrue(second_started.wait(5))
            try:
                time.sleep(.1)
                # Second independent SQLite writer must wait on A's write lock.
                self.assertFalse(other.done())
            finally:
                release.set()
            first.result(timeout=5)
            with self.assertRaises(PrecisionError):
                other.result(timeout=5)
        self.assertEqual(len(self.venue.placed), 20)
        self.assertEqual(self.a._reserved("USD"), Decimal("99.00000000"))

    def test_replace_preflight_funding_rejection_rolls_back_parent(self):
        parent = self.place(self.a, uid())
        with self.assertRaises(PrecisionError):
            self.a.replace_order(
                parent["venue_order_id"], new_price=Decimal("10000"),
                free_balances={"USD": Decimal("100")}, client_order_id=uid(),
            )
        parent_row = self.a.connection.execute(
            "SELECT status FROM order_lineage WHERE venue_order_id=?",
            (parent["venue_order_id"],),
        ).fetchone()
        self.assertEqual(parent_row["status"], "new")
        self.assertEqual(self.a.connection.execute(
            "SELECT count(*) FROM order_lineage WHERE parent_venue_order_id=?",
            (parent["venue_order_id"],),
        ).fetchone()[0], 0)
        self.assertEqual(len(self.venue.replaced), 0)

    def test_invalid_root_linkage_rejected_before_put(self):
        parent = self.place(self.a, uid())
        self.a.connection.execute(
            "UPDATE order_lineage SET root_client_order_id=? WHERE venue_order_id=?",
            (uid(), parent["venue_order_id"]),
        )
        with self.assertRaises(ReconciliationError):
            self.a.replace_order(
                parent["venue_order_id"], new_price=Decimal("101"),
                free_balances={"USD": Decimal("100000")}, client_order_id=uid(),
            )
        self.assertEqual(len(self.venue.replaced), 0)

    def test_two_connections_cannot_replace_same_parent_twice(self):
        parent = self.place(self.a, uid())
        self.b.reconcile_open_orders()
        entered, release = threading.Event(), threading.Event()
        original_funding = self.a._assert_funding

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("held replacement lock too long")
            return original_funding(*args, **kwargs)

        self.a._assert_funding = delayed

        def replace(executor, cid):
            return executor.replace_order(
                parent["venue_order_id"], new_price=Decimal("101"),
                free_balances={"USD": Decimal("100000")}, client_order_id=cid,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(replace, self.a, uid())
            self.assertTrue(entered.wait(5))
            other = pool.submit(replace, self.b, uid())
            try:
                time.sleep(.1)
                self.assertFalse(other.done())
            finally:
                release.set()
            first.result(timeout=5)
            with self.assertRaises(ReconciliationError):
                other.result(timeout=5)
        self.assertEqual(len(self.venue.replaced), 1)
        rows = self.a.connection.execute(
            "SELECT status FROM order_lineage WHERE parent_venue_order_id=?",
            (parent["venue_order_id"],),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "new")

    def test_existing_successor_guard_even_if_parent_status_was_reset(self):
        parent = self.place(self.a, uid())
        self.a.replace_order(
            parent["venue_order_id"], new_price=Decimal("101"),
            free_balances={"USD": Decimal("100000")}, client_order_id=uid(),
        )
        # Simulates stale/externally corrupted parent state. UNIQUE lineage
        # constraint and explicit check must still refuse a second successor.
        self.a.connection.execute(
            "UPDATE order_lineage SET status='new' WHERE venue_order_id=?",
            (parent["venue_order_id"],),
        )
        with self.assertRaises(ReconciliationError):
            self.a.replace_order(
                parent["venue_order_id"], new_price=Decimal("102"),
                free_balances={"USD": Decimal("100000")}, client_order_id=uid(),
            )
        self.assertEqual(len(self.venue.replaced), 1)

    def test_dry_run_does_not_persist_reservation(self):
        self.a.close()
        self.a = GridExecutor(self.venue, {"BTC/USD": rules()},
                              database_path=self.db, live_trading=False)
        self.a.reconcile_open_orders()
        cid = uid()
        result = self.place(self.a, cid)
        self.assertTrue(result["dry_run"])
        self.assertIsNone(self.a.connection.execute(
            "SELECT 1 FROM order_lineage WHERE client_order_id=?", (cid,),
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
