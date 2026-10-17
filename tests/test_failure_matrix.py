"""Failure matrix for write-ahead POST/PUT and authoritative venue acknowledgements.

Every order/fill/response in this suite is a fake. No account/network access.
"""
from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from grid_executor import GridExecutor, ReconciliationError
from precision_engine import PrecisionError
from revolut_x_client import RevolutXClient, UncertainMutationError
from test_bot import FakeClient, uid, rules


class CrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tmp.name) / "venue.sqlite")
        self.venue = FakeClient()
        self.exe = GridExecutor(self.venue, {"BTC/USD": rules()},
                                database_path=self.database, live_trading=True)
        self.exe.reconcile_open_orders()

    def tearDown(self):
        self.exe.close()
        self.tmp.cleanup()

    def restart(self):
        self.exe.close()
        self.exe = GridExecutor(self.venue, {"BTC/USD": rules()},
                                database_path=self.database, live_trading=True)
        self.exe.reconcile_open_orders()

    def post(self, cid):
        return self.exe.create_order(
            client_order_id=cid, symbol="BTC/USD", side="buy",
            price=Decimal("100"), quantity=Decimal("0.01"),
            free_balances={"USD": Decimal("100000")},
        )

    def put(self, parent_id, cid):
        return self.exe.replace_order(
            parent_id, new_price=Decimal("101"), client_order_id=cid,
            free_balances={"USD": Decimal("100000")},
        )

    def assert_intent(self, cid, status):
        item = self.exe.connection.execute(
            "SELECT status FROM order_lineage WHERE client_order_id=?", (cid,)
        ).fetchone()
        self.assertEqual(item["status"] if item else None, status)

    def test_crash_before_post_network_keeps_persistent_pending_and_cannot_retry(self):
        cid = uid()
        self.exe._assert_dispatch_allowed = lambda *_: (_ for _ in ()).throw(SystemExit("crashed"))
        with self.assertRaises(SystemExit):
            self.post(cid)
        self.assert_intent(cid, "pending_new")
        self.assertEqual(len(self.venue.placed), 0)
        with self.assertRaises(ReconciliationError):
            self.restart()
        with self.assertRaises(ReconciliationError):
            self.post(cid)
        self.assertEqual(len(self.venue.placed), 0)

    def test_crash_after_venue_post_before_ack_recovered_without_second_post(self):
        cid = uid()
        original = self.venue.place_order
        def crash(payload):
            original(payload)
            raise SystemExit("process died after venue accepted the order")
        self.venue.place_order = crash
        with self.assertRaises(SystemExit):
            self.post(cid)
        self.assert_intent(cid, "pending_new")
        self.venue.place_order = original
        self.restart()
        self.assertTrue(self.post(cid)["idempotent"])
        self.assertEqual(len(self.venue.placed), 1)

    def test_crash_after_ack_before_get_recovered_from_venue(self):
        cid = uid()
        original = self.venue.get_order
        def crash(*_):
            raise SystemExit("process died before read-back")
        self.venue.get_order = crash
        with self.assertRaises(SystemExit):
            self.post(cid)
        self.assert_intent(cid, "pending_new")
        self.venue.get_order = original
        self.restart()
        self.assertEqual(len(self.venue.placed), 1)

    def test_crash_after_get_before_local_confirmation(self):
        cid = uid()
        original = self.exe._confirm_mutation
        def crash(*a, **kw):
            original(*a, **kw)
            raise SystemExit("crashed before local acknowledgment commit")
        self.exe._confirm_mutation = crash
        with self.assertRaises(SystemExit):
            self.post(cid)
        self.assert_intent(cid, "pending_new")
        self.restart()
        self.assertEqual(len(self.venue.placed), 1)

    def test_restart_after_committed_ack_only_returns_cached_id(self):
        cid = uid()
        self.post(cid)
        self.restart()
        self.assertTrue(self.post(cid)["idempotent"])
        self.assertEqual(len(self.venue.placed), 1)

    def test_connection_drop_and_timeout_after_post_are_never_replayed(self):
        for exc_type in (requests.Timeout, requests.ConnectionError):
            with self.subTest(exc_type=exc_type):
                cid = uid()
                old = self.venue.place_order
                def lost(payload):
                    old(payload)
                    raise exc_type("network response lost")
                self.venue.place_order = lost
                try:
                    with self.assertRaises(exc_type):
                        self.post(cid)
                    self.assert_intent(cid, "pending_new")
                finally:
                    self.venue.place_order = old
                self.restart()
                self.assertEqual(len([x for x in self.venue.placed if x["client_order_id"] == cid]), 1)

    def test_bad_ack_client_id_never_confirms_and_recovers_get(self):
        cid = uid()
        orig = self.venue.place_order
        def forged(payload):
            result = orig(payload)
            return dict(result, client_order_id=uid())
        self.venue.place_order = forged
        with self.assertRaises(ReconciliationError):
            self.post(cid)
        self.assert_intent(cid, "pending_new")
        self.venue.place_order = orig
        self.restart()
        self.assertTrue(self.post(cid)["idempotent"])

    def test_corrupt_get_fields_fail_closed_without_confirming_intent(self):
        variations = {
            "id": lambda x: x.update(id=uid()),
            "client_order_id": lambda x: x.update(client_order_id=uid()),
            "symbol": lambda x: x.update(symbol="ETH/USD"),
            "side": lambda x: x.update(side="sell"),
            "price": lambda x: x.update(price="101"),
            "quantity": lambda x: x.update(quantity="0.02", leaves_quantity="0.02"),
            "post_only": lambda x: x.update(execution_instructions=["allow_taker"]),
            "state": lambda x: x.update(status="unexpected"),
            "leaves": lambda x: x.update(leaves_quantity="9"),
        }
        for label, corrupt in variations.items():
            with self.subTest(label=label):
                cid = uid()
                original = self.venue.get_order
                def tamper(vid):
                    item = original(vid)
                    corrupt(item)
                    return item
                self.venue.get_order = tamper
                try:
                    with self.assertRaises((ReconciliationError, PrecisionError)):
                        self.post(cid)
                    self.assert_intent(cid, "pending_new")
                finally:
                    self.venue.get_order = original
                self.restart()
                self.assertTrue(self.post(cid)["idempotent"])

    def test_kill_switch_set_between_intent_commit_and_dispatch_blocks_post(self):
        cid = uid()
        original = self.exe._assert_dispatch_allowed
        def trip(*args):
            self.exe.connection.execute(
                "INSERT INTO bot_metadata(key,value) VALUES('kill_switch_triggered','true') "
                "ON CONFLICT(key) DO UPDATE SET value='true'"
            )
            return original(*args)
        self.exe._assert_dispatch_allowed = trip
        with self.assertRaises(ReconciliationError):
            self.post(cid)
        self.assert_intent(cid, "pending_new")
        self.assertEqual(len(self.venue.placed), 0)

    def test_crash_after_venue_put_before_ack_adopts_unique_child(self):
        parent_id = self.post(uid())["venue_order_id"]
        child = uid()
        original = self.venue.replace_order
        def crash(vid, payload):
            original(vid, payload)
            raise SystemExit("crashed after replace")
        self.venue.replace_order = crash
        with self.assertRaises(SystemExit):
            self.put(parent_id, child)
        self.assert_intent(child, "pending_replace")
        self.venue.replace_order = original
        self.restart()
        self.assert_intent(child, "new")
        self.assertEqual(len(self.venue.replaced), 1)

    def test_timeout_after_put_with_missing_undocumented_predecessor_recovers(self):
        parent_id = self.post(uid())["venue_order_id"]
        child = uid()
        original = self.venue.replace_order
        def lost(vid, payload):
            original(vid, payload)
            self.venue.active[0].pop("previous_order_id", None)
            raise requests.Timeout("no PUT response")
        self.venue.replace_order = lost
        with self.assertRaises(requests.Timeout):
            self.put(parent_id, child)
        self.venue.replace_order = original
        self.restart()
        self.assert_intent(child, "new")
        self.assertEqual(len(self.venue.replaced), 1)

    def test_crash_after_put_ack_before_get_recovers(self):
        parent_id = self.post(uid())["venue_order_id"]
        child = uid()
        original = self.venue.get_order
        def crash(vid):
            if vid != parent_id:
                raise SystemExit("crash reading child")
            return original(vid)
        self.venue.get_order = crash
        with self.assertRaises(SystemExit):
            self.put(parent_id, child)
        self.venue.get_order = original
        self.restart()
        self.assertEqual(len(self.venue.replaced), 1)

    def test_put_ack_fails_if_original_venue_order_is_not_replaced(self):
        parent_id = self.post(uid())["venue_order_id"]
        child = uid()
        original = self.venue.get_order
        def wrong_parent(vid):
            data = original(vid)
            if vid == parent_id:
                data["status"] = "new"
            return data
        self.venue.get_order = wrong_parent
        with self.assertRaises(ReconciliationError):
            self.put(parent_id, child)
        self.assert_intent(child, "pending_replace")
        self.venue.get_order = original
        self.restart()
        self.assert_intent(child, "new")

    def test_put_ack_wrong_client_id_leaves_pending_recoverable(self):
        parent_id = self.post(uid())["venue_order_id"]
        child = uid()
        original = self.venue.replace_order
        def wrong(vid, payload):
            result = original(vid, payload)
            return dict(result, client_order_id=uid())
        self.venue.replace_order = wrong
        with self.assertRaises(ReconciliationError):
            self.put(parent_id, child)
        self.assert_intent(child, "pending_replace")
        self.venue.replace_order = original
        self.restart()
        self.assert_intent(child, "new")

    def test_kill_switch_before_put_dispatch_blocks_write(self):
        parent_id = self.post(uid())["venue_order_id"]
        child = uid()
        original = self.exe._assert_dispatch_allowed
        def trip(*args):
            self.exe.connection.execute(
                "INSERT INTO bot_metadata(key,value) VALUES('kill_switch_triggered','true') "
                "ON CONFLICT(key) DO UPDATE SET value='true'"
            )
            return original(*args)
        self.exe._assert_dispatch_allowed = trip
        with self.assertRaises(ReconciliationError):
            self.put(parent_id, child)
        self.assert_intent(child, "pending_replace")
        self.assertEqual(len(self.venue.replaced), 0)


class WireContracts(unittest.TestCase):
    def test_official_put_data_object_and_singleton_array(self):
        cid, vid = uid(), uid()
        for rows in ({"venue_order_id": vid, "client_order_id": cid, "state": "new"},
                     [{"venue_order_id": vid, "client_order_id": cid, "state": "new"}]):
            session = Mock()
            response = Mock(status_code=200, content=b"x")
            response.json.return_value = {"data": rows}
            session.request.return_value = response
            client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
            self.assertEqual(client.replace_order(uid(), {"client_order_id": cid})["venue_order_id"], vid)

    def test_ambiguous_put_multi_data_or_wrong_uuid_is_not_accepted(self):
        cid = uid()
        for data in ([], [dict(client_order_id=cid, venue_order_id=uid(), state="new")] * 2,
                     dict(client_order_id=cid, venue_order_id="invalid", state="new"),
                     dict(client_order_id=cid, venue_order_id=uid(), state="pending_wrong"),
                     dict(client_order_id=uid(), venue_order_id=uid(), state="new")):
            with self.subTest(data=data):
                session = Mock()
                response = Mock(status_code=200, content=b"x")
                response.json.return_value = {"data": data}
                session.request.return_value = response
                client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
                with self.assertRaises(UncertainMutationError):
                    client.replace_order(uid(), {"client_order_id": cid})
                self.assertEqual(session.request.call_count, 1)

    def test_post_put_timeout_5xx_429_and_disconnect_send_at_most_once(self):
        cid = uid()
        errors = [requests.Timeout("lost"), requests.ConnectionError("reset"),
                  requests.exceptions.ChunkedEncodingError("truncated"), Mock(status_code=500, content=b"err"),
                  Mock(status_code=429, content=b"err")]
        for method in ("place_order", "replace_order"):
            for outcome in errors:
                with self.subTest(method=method, outcome=repr(outcome)):
                    session = Mock()
                    session.request.side_effect = [outcome] if isinstance(outcome, BaseException) else None
                    if not isinstance(outcome, BaseException):
                        session.request.return_value = outcome
                    client = RevolutXClient("key", Ed25519PrivateKey.generate(), session=session)
                    with self.assertRaises(UncertainMutationError):
                        if method == "place_order":
                            client.place_order({"client_order_id": cid})
                        else:
                            client.replace_order(uid(), {"client_order_id": cid})
                    self.assertEqual(session.request.call_count, 1)



class FileVenue(FakeClient):
    """Disk-backed fake venue survives real os._exit() in a child process."""

    def __init__(self, state_path, crash=None):
        super().__init__()
        self.state_path = Path(state_path)
        self.crash = crash
        if self.state_path.exists():
            import json
            state = json.loads(self.state_path.read_text())
            self.active, self.history = state["active"], state["history"]

    def _save(self):
        import json, os
        with self.state_path.open("w", encoding="utf8") as out:
            json.dump({"active": self.active, "history": self.history}, out)
            out.flush()
            os.fsync(out.fileno())

    def place_order(self, payload):
        import os
        result = super().place_order(payload)
        self._save()
        if self.crash == "POST":
            os._exit(71)
        return result

    def replace_order(self, venue_order_id, payload):
        import os
        result = super().replace_order(venue_order_id, payload)
        self._save()
        if self.crash == "PUT":
            os._exit(72)
        return result

    def get_order(self, venue_order_id):
        import os
        result = super().get_order(venue_order_id)
        if self.crash in ("GET_POST", "GET_PUT"):
            os._exit(73)
        return result


def crash_worker(db, state_path, operation, cid, parent_id=None):
    import os
    venue = FileVenue(state_path, crash=operation)
    executor = GridExecutor(venue, {"BTC/USD": rules()}, database_path=db, live_trading=True)
    executor.reconcile_open_orders()
    if operation == "BEFORE_POST":
        executor._assert_dispatch_allowed = lambda *args: os._exit(74)
    if operation == "BEFORE_PUT":
        executor._assert_dispatch_allowed = lambda *args: os._exit(75)
    if parent_id is None:
        executor.create_order(
            client_order_id=cid, symbol="BTC/USD", side="buy",
            price=Decimal("100"), quantity=Decimal("0.01"),
            free_balances={"USD": Decimal("100000")},
        )
    else:
        executor.replace_order(
            parent_id, new_price=Decimal("101"),
            client_order_id=cid, free_balances={"USD": Decimal("100000")},
        )


class HardProcessCrashTests(unittest.TestCase):
    """Real process termination, not a caught Python exception."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "state.sqlite")
        self.snapshot = str(Path(self.tmp.name) / "venue.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _crash(self, operation, cid, parent_id=None):
        import multiprocessing
        context = multiprocessing.get_context("spawn")
        proc = context.Process(
            target=crash_worker,
            args=(self.db, self.snapshot, operation, cid, parent_id),
        )
        proc.start()
        proc.join(timeout=12)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
            self.fail("child process did not terminate")
        self.assertIn(proc.exitcode, (71, 72, 73, 74, 75))
        return FileVenue(self.snapshot)

    def _reconcile(self, venue):
        executor = GridExecutor(venue, {"BTC/USD": rules()},
                                database_path=self.db, live_trading=True)
        try:
            executor.reconcile_open_orders()
        except BaseException:
            executor.close()
            raise
        return executor

    def _parent(self):
        venue = FileVenue(self.snapshot)
        executor = self._reconcile(venue)
        result = executor.create_order(
            client_order_id=uid(), symbol="BTC/USD", side="buy",
            price=Decimal("100"), quantity=Decimal("0.01"),
            free_balances={"USD": Decimal("100000")},
        )
        executor.close()
        return result["venue_order_id"]

    def test_actual_process_death_pre_post_never_replays_unsafe_intent(self):
        cid = uid()
        venue = self._crash("BEFORE_POST", cid)
        self.assertEqual(len(venue.active), 0)
        with self.assertRaises(ReconciliationError):
            self._reconcile(venue)

    def test_actual_process_death_after_venue_accepts_post_reconciles(self):
        for mode in ("POST", "GET_POST"):
            with self.subTest(mode=mode):
                # Isolate subprocess tests from one another.
                with tempfile.TemporaryDirectory() as tmp:
                    db = str(Path(tmp) / "db.sqlite")
                    state = str(Path(tmp) / "venue.json")
                    cid = uid()
                    import multiprocessing
                    ctx = multiprocessing.get_context("spawn")
                    p = ctx.Process(target=crash_worker, args=(db, state, mode, cid))
                    p.start()
                    p.join(12)
                    self.assertEqual(p.exitcode, 71 if mode == "POST" else 73)
                    venue = FileVenue(state)
                    exe = GridExecutor(venue, {"BTC/USD": rules()},
                                       database_path=db, live_trading=True)
                    try:
                        exe.reconcile_open_orders()
                        row = exe.connection.execute(
                            "SELECT status FROM order_lineage WHERE client_order_id=?", (cid,)
                        ).fetchone()
                        self.assertEqual(row["status"], "new")
                        self.assertEqual(len(venue.active), 1)
                    finally:
                        exe.close()

    def test_actual_process_death_pre_put_blocks_and_preserves_parent(self):
        parent = self._parent()
        child = uid()
        venue = self._crash("BEFORE_PUT", child, parent)
        self.assertEqual(len(venue.active), 1)
        with self.assertRaises(ReconciliationError):
            self._reconcile(venue)

    def test_actual_process_death_after_put_is_reconciled_without_reissue(self):
        for mode in ("PUT", "GET_PUT"):
            with self.subTest(mode=mode):
                # Each scenario gets a fresh persistent database.
                with tempfile.TemporaryDirectory() as tmp:
                    db = str(Path(tmp) / "db.sqlite")
                    state = str(Path(tmp) / "venue.json")
                    venue = FileVenue(state)
                    exe = GridExecutor(venue, {"BTC/USD": rules()},
                                       database_path=db, live_trading=True)
                    exe.reconcile_open_orders()
                    parent = exe.create_order(
                        client_order_id=uid(), symbol="BTC/USD", side="buy",
                        price=Decimal("100"), quantity=Decimal("0.01"),
                        free_balances={"USD": Decimal("100000")},
                    )["venue_order_id"]
                    exe.close()
                    import multiprocessing
                    ctx = multiprocessing.get_context("spawn")
                    cid = uid()
                    p = ctx.Process(target=crash_worker,
                                    args=(db, state, mode, cid, parent))
                    p.start()
                    p.join(12)
                    self.assertEqual(p.exitcode, 72 if mode == "PUT" else 73)
                    recovered = FileVenue(state)
                    exe = GridExecutor(recovered, {"BTC/USD": rules()},
                                       database_path=db, live_trading=True)
                    try:
                        exe.reconcile_open_orders()
                        child_row = exe.connection.execute(
                            "SELECT status FROM order_lineage WHERE client_order_id=?", (cid,)
                        ).fetchone()
                        self.assertEqual(child_row["status"], "new")
                        self.assertEqual(len(recovered.active), 1)
                        self.assertEqual(len(recovered.history), 1)
                    finally:
                        exe.close()

if __name__ == "__main__":
    unittest.main()
