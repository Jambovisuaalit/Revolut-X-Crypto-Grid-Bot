"""Atomic SQLite audit chain, idempotent synthetic fills and sticky risk recovery."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from .config import Config
from .engine import fresh_state, transition
from .grid import PairRules


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


class Journal:
    """One atomic transaction includes virtual lots, risk state, decisions and fills."""

    def __init__(self, file: str | Path, run_id: str, cfg: Config, rules: PairRules):
        if not isinstance(run_id, str) or not run_id or len(run_id) > 100:
            raise ValueError("run_id required")
        self.cfg, self.rules, self.run_id = cfg, rules, run_id
        Path(file).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(file), timeout=10, isolation_level=None)
        try:
            self.db.execute("PRAGMA busy_timeout=10000")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    state_json TEXT NOT NULL
                )
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS synthetic_fills (
                    fill_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES decisions(event_id),
                    details_json TEXT NOT NULL
                )
            """)
            self.db.execute("BEGIN IMMEDIATE")
            for key, value in {
                "run_id": run_id, "config_hash": cfg.sha256, "mode": "shadow",
                "pair_source": rules.source,
            }.items():
                previous = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
                if previous is not None and previous[0] != value:
                    raise ValueError(f"journal metadata mismatch: {key}")
                self.db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES (?,?)", (key,value))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            self.db.close()
            raise

    def _last(self) -> tuple[dict, str]:
        last = self.db.execute("SELECT state_json,entry_hash FROM decisions ORDER BY seq DESC LIMIT 1").fetchone()
        return (json.loads(last[0]), last[1]) if last else (fresh_state(self.cfg), "0"*64)

    def process(self, event: dict) -> dict:
        """Idempotently process an observation under BEGIN IMMEDIATE across processes."""
        if not isinstance(event, dict):
            raise ValueError("event must be object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id required")
        payload_hash = digest(event)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT payload_hash,decision_json FROM decisions WHERE event_id=?", (event_id,)
            ).fetchone()
            if existing:
                if existing[0] != payload_hash:
                    raise ValueError("same event_id with different content")
                decision = json.loads(existing[1])
                self.db.execute("COMMIT")
                return decision
            previous_state, previous_hash = self._last()
            next_state, decision = transition(previous_state, event, self.cfg, self.rules)
            decision["run_id"] = self.run_id
            decision["previous_hash"] = previous_hash
            decision["state_hash"] = digest(next_state)
            decision["pair_source"] = self.rules.source
            decision["entry_hash"] = digest(decision)
            self.db.execute(
                "INSERT INTO decisions(event_id,payload_hash,previous_hash,entry_hash,decision_json,state_json) VALUES (?,?,?,?,?,?)",
                (event_id, payload_hash, previous_hash, decision["entry_hash"], canonical(decision), canonical(next_state))
            )
            for fill in decision["fills"]:
                self.db.execute(
                    "INSERT INTO synthetic_fills(fill_id,event_id,details_json) VALUES (?,?,?)",
                    (fill["fill_id"], event_id, canonical(fill))
                )
            self.db.execute("COMMIT")
            return decision
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def state(self) -> dict:
        return self._last()[0]

    def decisions(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT decision_json FROM decisions ORDER BY seq")]

    def verify(self) -> dict:
        """Validate hash linkage, state snapshots and synthetic fill uniqueness."""
        prev = "0"*64
        rows = self.db.execute(
            "SELECT event_id,previous_hash,entry_hash,decision_json,state_json FROM decisions ORDER BY seq"
        ).fetchall()
        for event_id, linked, expected, dec_json, state_json in rows:
            decision = json.loads(dec_json)
            state = json.loads(state_json)
            signed = dict(decision)
            signed.pop("entry_hash")
            if (decision["event_id"] != event_id or linked != prev or
                    decision["previous_hash"] != prev or decision["entry_hash"] != expected or
                    digest(signed) != expected or digest(state) != decision["state_hash"]):
                raise ValueError("audit chain corrupted")
            prev = expected
        fills = self.db.execute("SELECT COUNT(*) FROM synthetic_fills").fetchone()[0]
        fill_rows = sum(len(json.loads(r[3])["fills"]) for r in rows)
        if fills != fill_rows:
            raise ValueError("synthetic fill index does not match audit")
        return {"audit_integrity": "PASS", "observations": len(rows),
                "synthetic_fills": fills, "last_hash": prev, "exchange_orders": 0}

    def report(self) -> dict:
        state = self.state()
        integrity = self.verify()
        return {
            "mode": "SHADOW_ONLY", "run_id": self.run_id, "config_hash": self.cfg.sha256,
            "pair": "ETH-EUR", "pair_metadata_source": self.rules.source,
            "data_provenance": "SYNTHETIC_FIXTURE_ONLY" if self.rules.source == "synthetic_fixture"
                               else "UNVERIFIED",
            "initial_equity_eur": str(self.cfg["initial_cash_eur"]),
            "final_equity_eur": state["last_equity"],
            "simulated_total_pnl_eur": str(
                __import__("decimal").Decimal(state["last_equity"]) - self.cfg["initial_cash_eur"]),
            "cash_eur": state["cash"], "eth_qty": state["eth"],
            "simulated_fees_eur": state["fees"], "realized_pnl_eur": state["realized_pnl"],
            "peak_equity_eur": state["peak"], "max_drawdown_eur": state["max_dd"],
            "closed_cycles": state["closed_cycles"], "rejected_signals": state["rejected_signals"],
            "data_gaps": state["data_gaps"], "reanchors": state["reanchors"],
            "risk_state": state["status"], "hard_stop_triggered": state["status"] == "STOPPED",
            "historical_30d_replay": "BLOCKED_NO_AUTHENTICATED_ORDERBOOK_HISTORY",
            "forward_shadow_30d": "NOT_RUN", "benchmark_40_60": "NOT_MEASURED",
            "acceptance": "NO_GO_SYNTHETIC_ONLY", "real_mutation_requests": 0,
            **integrity,
        }

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
