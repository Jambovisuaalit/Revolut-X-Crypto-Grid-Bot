"""Verify the actual Vite dist, not the synthetic Python-only fixture."""
from __future__ import annotations

from contextlib import closing
import http.client
from pathlib import Path
from threading import Thread
import unittest

from product_shell.react_assets import load_react_assets
from product_shell.server import local_server

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@unittest.skipUnless((DIST / "index.html").is_file(), "Vite dist not built; run npm run build")
class ReactBuiltBundleTests(unittest.TestCase):
    def test_01_real_vite_bundle_is_local_and_has_js_css(self):
        assets = load_react_assets(DIST)
        self.assertIn("/", assets)
        self.assertIn(b'id="root"', assets["/"][0])
        self.assertTrue(any(name.endswith(".js") for name in assets))
        self.assertTrue(any(name.endswith(".css") for name in assets))
        self.assertTrue(any(b"LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED" in body
                            for name, (body, _) in assets.items() if name.endswith(".js")))

    def test_02_real_bundle_http_routes_and_offline_contract(self):
        server = local_server(None, 0, ui="react", react_root=DIST)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with closing(http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=4
            )) as conn:
                conn.request("GET", "/")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b'id="root"', response.read())
            with closing(http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=4
            )) as conn:
                conn.request("GET", "/api/runtime")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b"NOT_CONFIGURED", response.read())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
