"""Customer portal safety and read-only contract tests, without external HTTP."""
from __future__ import annotations

from contextlib import closing
import http.client
from pathlib import Path
from threading import Thread
import tempfile
import unittest

from product_shell.server import local_server, snapshot, CONFIG


class GridConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = local_server(None, 0, clock=lambda: 1792214928623)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)

    def request(self, method: str, path: str, headers: dict | None = None):
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)) as con:
            con.request(method, path, headers=headers or {})
            response = con.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def test_console_01_binds_loopback_only_and_disables_live(self):
        import json
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["live_orders"], "DISABLED")
        self.assertEqual(data["account_connection"], "NOT_CONNECTED")
        self.assertEqual(data["mode"], "SHADOW_ONLY")
        self.assertEqual(data["strategy"]["state"], "NOT_STARTED")
        self.assertIsNone(data["equity_eur"])
        self.assertIsNone(data["realized_pnl_eur"])
        self.assertEqual(data["collector"]["status"], "NOT_CONFIGURED")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_console_02_all_mutating_requests_denied(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                code, _, _ = self.request(method, "/api/status")
                self.assertEqual(code, 405)

    def test_console_03_cross_origin_and_host_header_denied(self):
        port=self.server.server_port
        invalid=[
            {"Host":"attacker.example.com"},
            {"Origin":"http://external.example.com"},
            {"Sec-Fetch-Site":"cross-site"},
            {"Host":f"127.0.0.1:{port}","Origin":"null"},
        ]
        for headers in invalid:
            with self.subTest(headers=headers):
                code, _, _=self.request("GET","/api/status",headers=headers)
                self.assertEqual(code,403)

    def test_console_04_only_known_paths_no_traversal(self):
        for path in ("/api/orders", "/api/keys", "/secret.txt", "/metrics", "/foo"):
            code, _, _=self.request("GET",path)
            self.assertEqual(code,404)
        code,_,_=self.request("GET","/api/status?account=foo")
        self.assertEqual(code,400)

    def test_console_05_static_content_accessible_without_remote_scripts(self):
        status, headers, body = self.request("GET","/")
        self.assertEqual(status,200)
        self.assertIn(b"GRID",body)
        self.assertIn(b"api/status", self.request("GET", "/app.js")[2])
        self.assertIn("text/html", headers["Content-Type"])
        css=self.request("GET","/style.css")
        self.assertEqual(css[0],200)
        self.assertIn(b"--panel",css[2])

    def test_console_06_head_has_no_body(self):
        code, headers, body = self.request("HEAD","/api/status")
        self.assertEqual(code,200)
        self.assertEqual(body,b"")
        self.assertEqual(headers["Content-Type"],"application/json; charset=utf-8")

    def test_console_07_corrupt_local_db_is_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/"bad.sqlite"
            db.write_text("not a database")
            data=snapshot(db,1792214928623)
            self.assertEqual(data["collector"]["status"],"BLOCKED")
            self.assertEqual(data["live_orders"],"DISABLED")
            self.assertNotIn(str(db),str(data))

    def test_console_08_no_live_modules_imported(self):
        import product_shell.server as portal
        code=Path(portal.__file__).read_text("utf-8")
        for token in ("revolut_x_client", "grid_executor", "place_order(",
                      "cancel_all_orders(", "from_pem("):
            self.assertNotIn(token,code)
        self.assertEqual(CONFIG.name,"eth_eur_grid_shadow_v1.toml")


if __name__ == "__main__":
    unittest.main()
