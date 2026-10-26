"""No-network negative and positive checks for off-volume SQLite backup and capacity."""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.offsite_backup_transport import (
    check_scratch_separate, ensure_destination, offsite_backup, snapshot_to_scratch,
)
from eth_shadow_v1.capacity_preflight import assess_24h_capacity

PROBE = {
    "sqlite_book_rows": 36, "accepted_rows": 36,
    "sqlite_all_files_bytes": 299008,
    "accepted_payload_total_bytes": 208710,
    "cause": "TIMEBOX_COMPLETED", "status": "REVIEW_REQUIRED",
    "full_chain_audit": {"status": "PASS"},
}

class BackupCapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "collector.sqlite"
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE items(k INTEGER PRIMARY KEY, v TEXT)")
            db.execute("INSERT INTO items VALUES (1,'verified')")
            db.commit()

    def test_remote_unconfigured_has_no_side_effect(self):
        with patch.dict(os.environ, {"REVX_BACKUP_UPLOAD_URL": "", "REVX_BACKUP_UPLOAD_TOKEN": ""}):
            with self.assertRaisesRegex(RuntimeError, "OFFSITE_BACKUP_DESTINATION_NOT_CONFIGURED"):
                offsite_backup(self.db)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["collector.sqlite"])

    def test_url_rejects_non_https_and_credentials(self):
        for url in ("http://example.com/a", "https://u:p@example.com/x",
                    "https://localhost/x", "https://example.com:8080/x",
                    "https://example.com/x#f"):
            with self.assertRaises(ValueError):
                ensure_destination(url)
        ensure_destination("https://backup.example.org/store")

    def test_scratch_same_filesystem_refused(self):
        with self.assertRaisesRegex(ValueError, "SCRATCH"):
            check_scratch_separate(self.db, self.root)

    def test_snapshot_compress_reopen_and_cleanup(self):
        with tempfile.TemporaryDirectory() as other:
            raw, gz, evidence = snapshot_to_scratch(self.db, Path(other))
            self.assertTrue(gz.is_file())
            self.assertEqual(len(evidence["archive_sha256"]), 64)
            with closing(sqlite3.connect(raw)) as db:
                self.assertEqual(db.execute("SELECT v FROM items").fetchone()[0], "verified")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["collector.sqlite"])

    def test_verified_remote_receipt_uploads_only_compressed_snapshot(self):
        import hashlib
        class Result:
            status_code = 200
            content = b'{\"ok\":true}'
            def __init__(self, digest):
                self.digest = digest
            def json(self):
                return {"sha256": self.digest, "durable": True}
        class FakeSession:
            def __init__(self):
                self.calls = 0
            def put(self, url, *, data, allow_redirects, timeout, headers):
                self.calls += 1
                self.payload = data.read()
                self.assertion = (url, allow_redirects, headers["Content-Type"])
                self.digest = hashlib.sha256(self.payload).hexdigest()
                return Result(self.digest)
        fake = FakeSession()
        with patch("scripts.offsite_backup_transport.check_scratch_separate"):
            result = offsite_backup(
                self.db, upload_url="https://archive.example.com/backup",
                token="a_secret_test_token_123456", scratch_root=self.root,
                session=fake,
            )
        self.assertEqual(result["archive_sha256"], fake.digest)
        self.assertEqual(fake.assertion,
                         ("https://archive.example.com/backup", False, "application/gzip"))
        self.assertEqual(result["status"], "REMOTE_RECEIPT_VERIFIED")
        self.assertEqual(fake.calls, 1)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["collector.sqlite"])

    def test_mismatched_remote_receipt_fails_closed_and_cleans(self):
        class Result:
            status_code = 200
            content = b'{}'
            def json(self):
                return {"sha256": "0" * 64, "durable": True}
        class FakeSession:
            def put(self, *args, **kwargs):
                return Result()
        with patch("scripts.offsite_backup_transport.check_scratch_separate"):
            with self.assertRaisesRegex(RuntimeError, "BACKUP_REMOTE_RECEIPT_MISMATCH"):
                offsite_backup(self.db, upload_url="https://archive.example.com/store",
                               token="a_secret_test_token_123456", scratch_root=self.root,
                               session=FakeSession())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["collector.sqlite"])

    def test_model_blocks_inadequate_headroom(self):
        result = assess_24h_capacity(PROBE, free_bytes=443_768_832)
        self.assertEqual(result["projected_db_bytes"], 143_523_840)
        self.assertEqual(result["projected_db_with_25pct_margin_bytes"], 179_404_800)
        self.assertIn("25PCT_MARGIN_EXCEEDS_DISK_HEADROOM", result["reasons"])
        self.assertEqual(result["required_free_volume_bytes"], 447_840_256)
        self.assertEqual(result["additional_free_bytes_needed"], 4_071_424)
        self.assertFalse(result["capacity_margin_pass"])
        self.assertIn("OFFSITE_DESTINATION_NOT_CONFIGURED", result["reasons"])
        self.assertEqual(result["status"], "NO_GO")

    def test_model_does_not_claim_go_even_with_large_disk(self):
        result = assess_24h_capacity(PROBE, free_bytes=1_000_000_000,
                    offsite_export_configured=True, remote_restore_verified=True)
        self.assertNotIn("25PCT_MARGIN_EXCEEDS_DISK_HEADROOM", result["reasons"])
        self.assertEqual(result["additional_free_bytes_needed"], 0)
        self.assertTrue(result["capacity_margin_pass"])
        self.assertIn("24H_CONTINUITY_UNTESTED", result["reasons"])
        self.assertEqual(result["status"], "NO_GO")

    def test_partial_or_inconsistent_probe_never_calibrates(self):
        for accepted in (0, 30, 35, 37, None):
            with self.subTest(accepted_rows=accepted):
                result = assess_24h_capacity(
                    {**PROBE, "accepted_rows": accepted}, free_bytes=1_000_000_000)
                self.assertIn("INSUFFICIENT_CALIBRATION", result["reasons"])
                self.assertIsNone(result["required_free_volume_bytes"])
                self.assertFalse(result["capacity_margin_pass"])

    def test_unverified_or_malformed_audit_never_calibrates(self):
        for audit in (None, "PASS", {}, {"status": "FAIL"}):
            with self.subTest(audit=audit):
                result = assess_24h_capacity(
                    {**PROBE, "full_chain_audit": audit}, free_bytes=1_000_000_000)
                self.assertIn("UNVERIFIED_PROBE", result["reasons"])
        result = assess_24h_capacity(
            {**PROBE, "status": "BLOCKED"}, free_bytes=1_000_000_000)
        self.assertIn("UNVERIFIED_PROBE", result["reasons"])

    def test_unverified_probe_never_passes(self):
        bad = dict(PROBE, full_chain_audit={"status": "FAIL"})
        result = assess_24h_capacity(bad, free_bytes=1_000_000_000)
        self.assertIsNone(result["projected_db_bytes"])
        self.assertIn("UNVERIFIED_PROBE", result["reasons"])

if __name__ == "__main__":
    unittest.main()
