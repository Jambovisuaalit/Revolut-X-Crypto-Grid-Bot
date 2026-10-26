"""Offline-only invariants for explicit 180-second public GET probe."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.railway_3min_probe import (
    APPROVAL_PHRASE, DURATION_SECONDS, MAX_BOOK_POLLS,
    PROBE_NAME, begin, execute, summarize,
)

class ProbeSafetyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_no_approval_no_dir(self):
        with self.assertRaisesRegex(RuntimeError, "EXPLICIT_3MIN_APPROVAL_REQUIRED"):
            begin(self.root, "no", now=100_000)
        self.assertFalse((self.root / PROBE_NAME).exists())

    def test_explicit_once_only_and_persistent_deadline(self):
        p, state = begin(self.root, APPROVAL_PHRASE, now=100_000)
        self.assertEqual(state["absolute_deadline_ms"], 100_000+DURATION_SECONDS*1000)
        self.assertEqual(state["max_book_polls"], MAX_BOOK_POLLS)
        self.assertEqual(json.loads((p / "state.json").read_text())["status"], "STARTED_NO_RETRY")
        with self.assertRaises(FileExistsError):
            begin(self.root, APPROVAL_PHRASE, now=101_000)

    def test_fail_closed_live_flag(self):
        with patch.dict(os.environ, {"LIVE_TRADING": "true"}):
            with self.assertRaisesRegex(RuntimeError, "LIVE_TRADING_MUST_BE_DISABLED"):
                begin(self.root, APPROVAL_PHRASE, now=100_000)
        self.assertFalse((self.root / PROBE_NAME).exists())

    def test_missing_root_fails_before_io(self):
        with self.assertRaisesRegex(RuntimeError, "PERSISTENT_VOLUME_MISSING"):
            begin(self.root / "not-mounted", APPROVAL_PHRASE, now=100_000)

    def test_no_db_final_report_blocks(self):
        folder, state = begin(self.root, APPROVAL_PHRASE, now=100_000)
        out = summarize(folder, state, polls=0, end_ms=102_000, cause="STOPPED_TimeoutError",
                        free_before=1_000_000_000)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["book_poll_attempts"], 0)
        self.assertEqual(out["exchange_mutations"], 0)
        self.assertFalse(out["backup_capacity_estimate_pass"] is None)
        self.assertTrue((folder / "final-report.json").exists())

    def test_missing_data_never_calls_network_collector(self):
        class NoCollector:
            def __init__(self, *args, **kwargs):
                raise AssertionError("NETWORK_COLLECTOR_MUST_NOT_BE_REACHED")
        with self.assertRaisesRegex(RuntimeError,"EXPLICIT_3MIN_APPROVAL_REQUIRED"):
            execute(self.root, "wrong", collector_factory=NoCollector, use_alarm=False)

if __name__ == "__main__":
    unittest.main()
