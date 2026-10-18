"""No-network tests of Railway 24h pilot. Does not call run() or the public API."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sqlite3
import unittest
from unittest.mock import patch

from scripts.railway_24h_pilot import DURATION_MS, arm, begin_once, finish, read_state, sqlite_backup, status_probe

NOW = 1800000000000
FLAGS = {"LIVE_TRADING": "false", "EXPLICIT_PUBLIC_GET_APPROVAL": "I_APPROVE_PUBLIC_GET_24H"}


class BoundedPilotTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "STOPPED_BY_DEFAULT").write_text("disabled\n")
        self.flag_patch = patch.dict(os.environ, FLAGS)
        self.flag_patch.start()
        self.addCleanup(self.flag_patch.stop)

    def test_local_volume_probe_writes_and_removes_only_probe_file(self):
        result = status_probe(self.root)
        self.assertEqual(result["schema"], "railway-v06-volume-probe-v1")
        self.assertTrue(result["writable"])
        self.assertGreater(result["total_bytes"], 0)
        self.assertGreaterEqual(result["free_bytes"], 0)
        self.assertEqual(result["reserve_pass"], result["free_bytes"] >= 256 * 1024 * 1024)
        self.assertFalse(result["backup_capacity_verified"])
        self.assertEqual([p for p in self.root.iterdir() if p.name.startswith(".volume-probe-")], [])
        self.assertFalse((self.root / "collector.sqlite").exists())

    def test_missing_volume_is_blocked_no_directory_created(self):
        missing = self.root / "no-mount"
        probe = status_probe(missing)
        self.assertEqual(probe["status"], "BLOCKED")
        self.assertEqual(probe["reason"], "MOUNT_MISSING_OR_SYMLINK")
        self.assertFalse(missing.exists())

    def test_volume_reserve_never_claims_backup_capacity(self):
        from unittest.mock import Mock
        from scripts import railway_24h_pilot as pilot
        with patch.object(pilot.shutil, "disk_usage", return_value=Mock(total=500_000_000, used=300_000_000, free=200_000_000)):
            probe = status_probe(self.root)
        self.assertEqual(probe["status"], "BLOCKED")
        self.assertEqual(probe["reason"], "INSUFFICIENT_FREE_SPACE")
        self.assertFalse(probe["reserve_pass"])
        self.assertFalse(probe["backup_capacity_verified"])

    def test_approval_required_and_no_implicit_run(self):
        with patch.dict(os.environ, {"EXPLICIT_PUBLIC_GET_APPROVAL": ""}):
            with self.assertRaisesRegex(RuntimeError, "PUBLIC_GET_APPROVAL_REQUIRED"):
                arm(self.root, NOW)
        self.assertIsNone(read_state(self.root))
        with self.assertRaisesRegex(RuntimeError, "PILOT_NOT_ARMED"):
            begin_once(self.root, NOW)
        self.assertFalse((self.root / "collector.sqlite").exists())

    def test_24h_deadline_persists_and_restart_denied(self):
        arm(self.root, NOW)
        started = begin_once(self.root, NOW + 1000)
        self.assertEqual(started["deadline_at_ms"], NOW + 1000 + DURATION_MS)
        self.assertEqual(read_state(self.root)["deadline_at_ms"], started["deadline_at_ms"])
        with self.assertRaisesRegex(RuntimeError, "PILOT_NOT_ARMED"):
            begin_once(self.root, NOW + 2000)
        with self.assertRaisesRegex(RuntimeError, "PILOT_ALREADY_ARMED"):
            arm(self.root, NOW + 2000)

    def test_late_approval_refused_and_live_flag_refused(self):
        arm(self.root, NOW)
        with self.assertRaisesRegex(RuntimeError, "PILOT_APPROVAL_EXPIRED"):
            begin_once(self.root, NOW + 16 * 60 * 1000)
        with patch.dict(os.environ, {"LIVE_TRADING": "true"}):
            with self.assertRaisesRegex(RuntimeError, "LIVE_TRADING"):
                begin_once(self.root, NOW + 1000)

    def test_backup_is_consistent_and_readonly(self):
        db = self.root / "collector.sqlite"
        with closing(sqlite3.connect(db)) as con:
            con.execute("CREATE TABLE ticks (ts INTEGER PRIMARY KEY)")
            con.executemany("INSERT INTO ticks VALUES (?)", [(1,), (2,)])
            con.commit()
        before = db.read_bytes()
        target = self.root / "reports" / "collector-backup.sqlite"
        evidence = sqlite_backup(db, target)
        self.assertEqual(len(evidence["sha256"]), 64)
        self.assertEqual(before, db.read_bytes())
        with closing(sqlite3.connect(target)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM ticks").fetchone()[0], 2)
        with self.assertRaisesRegex(RuntimeError, "INVALID_BACKUP"):
            sqlite_backup(db, target)

    def test_finalization_blocked_with_no_db_and_never_rearms(self):
        arm(self.root, NOW)
        state = begin_once(self.root, NOW + 1000)
        evidence = finish(self.root, state, reason="DEADLINE", now=NOW + 1000 + DURATION_MS)
        self.assertEqual(evidence["status"], "BLOCKED")
        self.assertEqual(evidence["exchange_mutations"], 0)
        self.assertEqual(evidence["economic_approval"], "NO_GO")
        self.assertEqual(evidence["forward_30d"], "NOT_STARTED")
        self.assertEqual(read_state(self.root)["status"], "BLOCKED")
        self.assertTrue((self.root / "reports" / "final-report.json").is_file())
        with self.assertRaisesRegex(RuntimeError, "PILOT_NOT_ARMED"):
            begin_once(self.root, NOW + DURATION_MS + 2000)


if __name__ == "__main__":
    unittest.main()
