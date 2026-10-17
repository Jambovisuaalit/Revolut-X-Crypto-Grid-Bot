"""Atomic durable shadow decision/state journal; never writes to an exchange."""
import json
import sqlite3
from pathlib import Path


class ShadowJournal:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS entries (seq INTEGER PRIMARY KEY, decision TEXT NOT NULL, state TEXT NOT NULL)")
        self.db.commit()

    def last_state(self):
        row = self.db.execute("SELECT state FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def append(self, decision, state):
        if state["previous_hash"] != decision["hash"]:
            raise ValueError("decision and state hash mismatch")
        with self.db:
            self.db.execute(
                "INSERT INTO entries(decision,state) VALUES (?,?)",
                (json.dumps(decision, sort_keys=True), json.dumps(state, sort_keys=True)),
            )

    def export(self, path):
        with open(path, "w", encoding="utf-8") as stream:
            for (value,) in self.db.execute("SELECT decision FROM entries ORDER BY seq"):
                stream.write(value + "\n")

    def close(self):
        self.db.close()
