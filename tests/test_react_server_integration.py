"""React localhost asset routing and SQLite risk-lock safety; no external requests."""
from __future__ import annotations

from contextlib import closing
import http.client
import json
from pathlib import Path
import socket
import sqlite3
from threading import Thread
import tempfile
import unittest

from product_shell.react_assets import load_react_assets
from product_shell.server import local_server
from tests.react_fixture import make_react_fixture

NOW = 1_792_214_000_000


class ReactDesktopServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_react_fixture(Path(self.temp.name) / "react")
        self.db = Path(self.temp.name) / "local-test.sqlite"
        with closing(sqlite3.connect(self.db)) as conn:
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
                    'SECRET-ORDER-ID','SECRET-VENUE-ID','buy','pending_new','2026-10-17 08:00:00');
                INSERT INTO fills_wire VALUES(
                    'SECRET-FILL-ID','SECRET-VENUE-ID','2026-10-17 08:01:00');
            """)
            conn.commit()
        self.server = local_server(None, 0, clock=lambda: NOW,
                                   supervisor_db=self.db, ui="react",
                                   react_root=self.root)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.assertFalse(self.worker.is_alive(), "HTTP_SERVER_THREAD_LEAKED")

    def request(self, method, path, headers=None):
        with closing(http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=4
        )) as conn:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_01_react_assets_serve_with_correct_mime(self):
        for path, mime in (("/", "text/html"), ("/assets/index-test123.js", "javascript"),
                           ("/assets/index-test123.css", "text/css")):
            with self.subTest(path=path):
                code, headers, body = self.request("GET", path)
                self.assertEqual(code, 200)
                self.assertIn(mime, headers["Content-Type"])
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertTrue(body)
        self.assertIn(b'id="root"', self.request("GET", "/")[2])

    def test_02_legacy_and_unlisted_assets_not_served(self):
        for path in ("/app.js", "/style.css", "/src/main.tsx",
                     "/assets/secret.txt", "/assets/missing.js", "/api/orders",
                     "/.env", "/assets/../index.html"):
            with self.subTest(path=path):
                code, _, _ = self.request("GET", path)
                self.assertNotEqual(code, 200)

    def test_03_risk_lock_and_local_journal_visible_without_secrets(self):
        code, headers, body = self.request("GET", "/api/runtime")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        result = json.loads(body)
        self.assertEqual(result["status"], "RISK_LOCKED")
        self.assertEqual(result["risk_lock"], "TRIGGERED")
        self.assertEqual(result["uncertain_order_count"], 1)
        self.assertEqual(len(result["events"]), 2)
        self.assertFalse(result["reconciled_for_trading"])
        self.assertEqual(result["account_connection"], "NOT_CONNECTED")
        for forbidden in ("SECRET-ORDER-ID", "SECRET-VENUE-ID", "SECRET-FILL-ID",
                          str(self.db)):
            self.assertNotIn(forbidden, body.decode("utf-8"))

    def test_04_get_only_and_host_origin_protection(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method, "/api/runtime")[0], 405)
                self.assertEqual(self.request(method, "/")[0], 405)
        for headers in ({"Host": "attacker.example"}, {"Origin": "http://attacker.example"},
                        {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("GET", "/api/runtime", headers)[0], 403)

    def test_05_head_and_invalid_query(self):
        code, _, body = self.request("HEAD", "/assets/index-test123.js")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")
        self.assertEqual(self.request("GET", "/?token=secret")[0], 400)

    def test_06_bundle_missing_is_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "REACT_BUNDLE_MISSING"):
            load_react_assets(Path(self.temp.name) / "absent")
        (self.root / "assets/index-test123.js").unlink()
        with self.assertRaisesRegex(RuntimeError, "REACT_BUNDLE_REFERENCE_MISSING"):
            load_react_assets(self.root)

    def test_07_remote_or_dev_entrypoint_rejected(self):
        index = self.root / "index.html"
        original = index.read_text("utf-8")
        for attack in (
            original.replace("./assets/index-test123.js", "https://example.com/x.js"),
            original.replace("./assets/index-test123.js", "/src/main.tsx"),
            original.replace("./assets/index-test123.js", "../private.js"),
        ):
            index.write_text(attack, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_react_assets(self.root)
        index.write_text(original, encoding="utf-8")

    def test_08_unlisted_files_cannot_be_exposed(self):
        (self.root / "assets" / "secrets.txt").write_text("DO_NOT_LEAK")
        with self.assertRaisesRegex(RuntimeError, "REACT_UNSAFE_BUNDLE_ASSET"):
            load_react_assets(self.root)

    def test_09_database_unchanged_after_read(self):
        before = self.db.read_bytes()
        self.request("GET", "/api/runtime")
        self.assertEqual(self.db.read_bytes(), before)

    def test_10_legacy_mode_unchanged(self):
        with local_server(None, 0) as old:
            worker = Thread(target=old.serve_forever, daemon=True)
            worker.start()
            try:
                with closing(http.client.HTTPConnection(
                    "127.0.0.1", old.server_port, timeout=4
                )) as conn:
                    conn.request("GET", "/app.js")
                    self.assertEqual(conn.getresponse().status, 200)
            finally:
                old.shutdown()
                worker.join(timeout=5)

    def test_11_unknown_ui_mode_fails_before_binding(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_UI_MODE"):
            local_server(None, 0, ui="live")

    def test_12_react_ui_does_not_claim_real_account_data(self):
        code, _, body = self.request("GET", "/api/status")
        self.assertEqual(code, 200)
        result = json.loads(body)
        self.assertEqual(result["live_orders"], "DISABLED")
        self.assertEqual(result["account_connection"], "NOT_CONNECTED")
        self.assertIsNone(result["equity_eur"])


if __name__ == "__main__":
    unittest.main()
