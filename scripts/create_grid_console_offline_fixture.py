"""Generate a disposable synthetic offline supervisor SQLite fixture.

Used only by Windows CI/packaging smoke tests. No account data or network I/O.
The fixture deliberately contains secret-looking IDs to test redaction.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


def create_fixture(path: Path) -> None:
    """Create a test-only risk-locked journal; never overwrite a real DB."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("FIXTURE_PATH_ALREADY_EXISTS")
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE bot_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE order_lineage(
                client_order_id TEXT,venue_order_id TEXT,side TEXT NOT NULL,
                status TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE fills_wire(fill_id TEXT,venue_order_id TEXT,
                created_at TEXT NOT NULL);
            INSERT INTO bot_metadata VALUES('supervisor_state','STOPPED');
            INSERT INTO bot_metadata VALUES('supervisor_reason','HARD_DRAWDOWN_3PCT');
            INSERT INTO bot_metadata VALUES('kill_switch_triggered','true');
            INSERT INTO order_lineage VALUES(
                'SECRET-ORDER-DO-NOT-LOG','SECRET-VENUE-DO-NOT-LOG',
                'buy','pending_new','2026-10-17 08:00:00');
            INSERT INTO fills_wire VALUES(
                'SECRET-FILL-DO-NOT-LOG','SECRET-VENUE-DO-NOT-LOG',
                '2026-10-17 08:01:00');
        """)
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a synthetic offline SQLite risk fixture")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    create_fixture(args.output)


if __name__ == "__main__":
    main()
