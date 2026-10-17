"""Real subprocess crash/restart integration test. Offline; no exchange credentials."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

from grid_executor import GridExecutor, ReconciliationError
from precision_engine import PairRules

ROOT = Path(__file__).resolve().parents[1]
CID = "00000000-0000-4000-8000-000000000001"
VID = "00000000-0000-4000-8000-000000000002"


def atomic_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


class Exchange:
    """Persist venue acceptance independently from bot SQLite."""
    def __init__(self, path, *, invisible=False):
        self.path = Path(path)
        self.invisible = invisible

    def _state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def get_open_orders(self):
        return [] if self.invisible else self._state()["orders"]

    def get_historical_orders(self):
        return []

    def get_order(self, venue_order_id):
        return next(x for x in self.get_open_orders() if x["id"] == venue_order_id)

    def place_order(self, payload):
        state = self._state()
        state["post_count"] += 1
        order = {
            "id": VID, "client_order_id": payload["client_order_id"],
            "symbol": "BTC/USD", "side": "buy", "type": "limit",
            "price": "100.00", "quantity": "0.01000000",
            "filled_quantity": "0", "leaves_quantity": "0.01000000",
            "status": "new", "execution_instructions": ["post_only"],
        }
        state["orders"].append(order)
        atomic_json(self.path, state)
        # Simulate OS-level death AFTER exchange acceptance, BEFORE ACK.
        os._exit(73)

    def replace_order(self, *args, **kwargs):
        raise AssertionError("unexpected PUT")


def executor(db, exchange):
    rules = PairRules("BTC/USD", Decimal("0.00000001"), Decimal("0.01"),
                      Decimal("0.0001"), Decimal("10"), "active")
    return GridExecutor(exchange, {"BTC/USD": rules}, database_path=str(db), live_trading=True)


def order(ex):
    return ex.create_order(client_order_id=CID, symbol="BTC/USD", side="buy",
                           price=Decimal("100"), quantity=Decimal("0.01"),
                           free_balances={"USD": Decimal("100000"), "BTC": Decimal("10")})


def worker(db, venue):
    ex = executor(db, Exchange(venue))
    try:
        ex.reconcile_open_orders()
        order(ex)
        raise AssertionError("worker unexpectedly survived venue acceptance")
    finally:
        ex.close()


def run_case(directory, invisible):
    directory.mkdir(parents=True)
    db, venue = directory / "bot.sqlite", directory / "venue.json"
    atomic_json(venue, {"post_count": 0, "orders": []})
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(db), str(venue)],
        cwd=str(ROOT), timeout=30, capture_output=True, text=True,
        env={**os.environ, "LIVE_TRADING": "false"},
    )
    assert result.returncode == 73, f"expected abrupt exit 73; got {result.returncode}: {result.stderr}"
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status FROM order_lineage WHERE client_order_id=?", (CID,)).fetchone()
    assert row and row[0] == "pending_new", f"write-ahead intent missing: {row}"
    ex = executor(db, Exchange(venue, invisible=invisible))
    try:
        if invisible:
            try:
                ex.reconcile_open_orders()
            except ReconciliationError:
                pass
            else:
                raise AssertionError("unknown venue outcome was incorrectly accepted")
            assert not ex.reconciled
            try:
                order(ex)
            except ReconciliationError:
                pass
            else:
                raise AssertionError("unknown venue outcome permitted duplicate POST")
        else:
            ex.reconcile_open_orders()
            assert ex.reconciled
            assert order(ex)["idempotent"] is True
            row = ex.connection.execute(
                "SELECT status,venue_order_id FROM order_lineage WHERE client_order_id=?", (CID,)
            ).fetchone()
            assert row["status"] == "new" and row["venue_order_id"] == VID
    finally:
        ex.close()
    state = json.loads(venue.read_text(encoding="utf-8"))
    assert state["post_count"] == 1, f"duplicate POST: {state['post_count']}"
    assert len(state["orders"]) == 1
    return {"scenario": "unknown_fail_closed" if invisible else "accepted_ack_lost",
            "process_exit_code": result.returncode, "post_count": state["post_count"],
            "put_count": 0, "duplicate_orders": 0, "status": "PASS"}


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]), Path(sys.argv[3]))
        return
    output = Path(os.environ.get("RECOVERY_REPORT_DIR", "recovery-artifacts"))
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    cleanup_error = None
    temporary = tempfile.TemporaryDirectory()
    try:
        root = Path(temporary.name)
        for name, invisible in (("accepted", False), ("unknown", True)):
            try:
                reports.append(run_case(root / name, invisible))
            except Exception as exc:
                reports.append({"scenario": name, "status": "FAIL", "error": repr(exc)})
    finally:
        try:
            temporary.cleanup()
        except Exception as exc:
            cleanup_error = repr(exc)
    report = {"platform": sys.platform, "scenarios": reports,
              "cleanup_error": cleanup_error,
              "status": "PASS" if len(reports) == 2 and
              all(x["status"] == "PASS" for x in reports) and cleanup_error is None else "FAIL"}
    atomic_json(output / ("process-recovery-" + sys.platform + ".json"), report)
    print(json.dumps(report, indent=2))
    if report["status"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
