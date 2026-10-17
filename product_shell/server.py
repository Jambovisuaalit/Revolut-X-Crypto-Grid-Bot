"""Secure localhost-only read-only preview for a prospective software customer.

This module does not import any exchange client, start a collector, read API
credentials, place orders, or expose raw exchange data through a remote server.
"""
from __future__ import annotations

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from eth_shadow_v1.collector_quality import evaluate_quality
from eth_shadow_v1.config import Config

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
CONFIG = ROOT.parent / "configs" / "eth_eur_grid_shadow_v1.toml"
VERSION = "0.1.0-preview"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def snapshot(db_path: Path | None, now_ms: int) -> dict:
    """Produce only a public-safe, conservative customer dashboard snapshot."""
    cfg = Config.load(CONFIG)
    health = {
        "status": "NOT_CONFIGURED", "accepted_count": 0, "sample_count": 0,
        "alerts": ["KERÄINTÄ EI OLE KYTKETTY"],
    }
    if db_path is not None:
        try:
            if Path(db_path).exists():
                raw = evaluate_quality(Path(db_path), now_ms)
                health = {
                    "status": raw.get("status", "BLOCKED"),
                    "accepted_count": raw.get("accepted_count", 0),
                    "sample_count": raw.get("sample_count", 0),
                    "accepted_ratio": raw.get("accepted_ratio"),
                    "source_age_p95_ms": raw.get("source_age_p95_ms"),
                    "last_book_ms": raw.get("last_book_ms"),
                    "alerts": raw.get("alerts", ["STATUS_TUNTEMATON"])[:8],
                }
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            # A corrupt or missing DB cannot be misreported as "healthy".
            health = {"status": "BLOCKED", "accepted_count": 0,
                      "sample_count": 0, "alerts": ["TIETOKANNAN LUKU EPÄONNISTUI"]}
    return {
        "app": "Grid Console", "version": VERSION,
        "mode": "SHADOW_ONLY", "live_orders": "DISABLED",
        "account_connection": "NOT_CONNECTED",
        "strategy": {"market": "ETH/EUR", "type": "GRID", "state": "NOT_STARTED",
                     "virtual_start_eur": str(cfg["initial_cash_eur"]),
                     "max_virtual_exposure_eur": str(cfg["max_cost_basis_eur"]),
                     "hard_loss_limit_eur": str(cfg["hard_drawdown_eur"])},
        "collector": health,
        "equity_eur": None, "realized_pnl_eur": None, "trade_count": None,
        "data_note": "Ei oikean tilin saldoa tai kaupankäyntituloksia.",
        "next_step": "Tarkista laitteen soveltuvuus ja API-yhteyden turvallinen käyttöönotto.",
    }


def make_handler(db_path: Path | None, clock) -> type[BaseHTTPRequestHandler]:
    """Inject read-only dependencies without global mutation."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "GridConsole"
        sys_version = ""

        def _serve(self, body: bytes, content_type: str, status: int = 200,
                   *, head: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _allowed_origin(self) -> bool:
            host = self.headers.get("Host", "")
            port = self.server.server_port
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return False
            fetch_site = self.headers.get("Sec-Fetch-Site", "")
            if fetch_site and fetch_site not in ("same-origin", "none"):
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in (
                f"http://127.0.0.1:{port}", f"http://localhost:{port}"
            ):
                return False
            return True

        def _handle(self, *, head: bool) -> None:
            if not self._allowed_origin():
                self._serve(b"Access denied", "text/plain; charset=utf-8", 403, head=head)
                return
            path = urlsplit(self.path)
            if path.query or path.fragment:
                self._serve(b"Invalid path", "text/plain; charset=utf-8", 400, head=head)
                return
            if path.path == "/api/status":
                data = json.dumps(snapshot(db_path, clock()),
                                  sort_keys=True, ensure_ascii=False).encode("utf-8")
                self._serve(data, "application/json; charset=utf-8", head=head)
                return
            if path.path in FILES:
                name, content_type = FILES[path.path]
                self._serve((ASSETS / name).read_bytes(), content_type, head=head)
                return
            self._serve(b"Not found", "text/plain; charset=utf-8", 404, head=head)

        def do_GET(self) -> None:
            self._handle(head=False)

        def do_HEAD(self) -> None:
            self._handle(head=True)

        def do_POST(self) -> None:
            self._serve(b"Read-only: forbidden", "text/plain; charset=utf-8", 405)

        do_PUT = do_DELETE = do_PATCH = do_POST

        def log_message(self, format: str, *args: object) -> None:
            # No access log with query strings, credentials or sensitive data.
            return
    return Handler


def local_server(db_path: Path | None = None, port: int = 0, *,
                 clock=None) -> ThreadingHTTPServer:
    """Bind *only* to loopback. Never allow LAN/internet hosting."""
    import time
    handler = make_handler(db_path, clock or (lambda: time.time_ns() // 1_000_000))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server
