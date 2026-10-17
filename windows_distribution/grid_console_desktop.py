"""Windows Grid Console development desktop launcher.

Keeps the existing read-only Finnish Grid Console web UI; wraps it with an
ordinary graphical Windows window and a lifecycle-managed loopback server.

No API keys, no external network, no trading engine, no collector and no
privileged account operations are invoked by this GUI. PyInstaller builds
an UNSIGNED DEV-only artifact; never claim it is a signed customer release.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import http.client
import json
import os
import re
from pathlib import Path
import sys
from threading import Thread
from typing import Any
import webbrowser

from product_shell.server import local_server
from product_shell.local_runtime_readonly import inspect_local_runtime

SCHEMA = "grid-console-windows-react-offline-v2"
OFFLINE_REQUIRED = {
    "mode": "SHADOW_ONLY",
    "live_orders": "DISABLED",
    "account_connection": "NOT_CONNECTED",
}


def assert_safe_env() -> None:
    """Never launch even an offline GUI in an explicitly live configuration."""
    if os.environ.get("LIVE_TRADING", "false").strip().lower() != "false":
        raise RuntimeError("LIVE_ENV_FORBIDDEN")


def check_status(payload: dict[str, Any]) -> None:
    """Fail if the bundled dashboard falsely claims to be trading-ready."""
    if not isinstance(payload, dict):
        raise RuntimeError("INVALID_DASHBOARD_STATUS")
    for key, value in OFFLINE_REQUIRED.items():
        if payload.get(key) != value:
            raise RuntimeError("DASHBOARD_SAFETY_CONTRACT_FAILED")
    if payload.get("equity_eur") is not None or payload.get("realized_pnl_eur") is not None:
        raise RuntimeError("REAL_ACCOUNT_DATA_FORBIDDEN")
    if payload.get("strategy", {}).get("state") != "NOT_STARTED":
        raise RuntimeError("STRATEGY_MUST_REMAIN_STOPPED")


def status_through_loopback(supervisor_db: Path | None = None) -> dict[str, Any]:
    """Exercise existing HTTP route, including forbidden HTTP mutations."""
    assert_safe_env()
    with local_server(None, 0, supervisor_db=supervisor_db, ui="react") as server:
        thread = Thread(target=server.serve_forever, daemon=True, name="grid-console-test-http")
        thread.start()
        try:
            port = server.server_port

            def get(path: str, method: str = "GET") -> tuple[int, bytes, dict[str, str]]:
                with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as conn:
                    conn.request(method, path, headers={"Host": f"127.0.0.1:{port}"})
                    response = conn.getresponse()
                    return response.status, response.read(), dict(response.getheaders())

            code, body, headers = get("/api/status")
            if code != 200 or headers.get("Cache-Control") != "no-store":
                raise RuntimeError("LOCAL_STATUS_HTTP_FAILED")
            state = json.loads(body)
            check_status(state)
            runtime_code, runtime_body, _ = get("/api/runtime")
            if runtime_code != 200:
                raise RuntimeError("LOCAL_RUNTIME_ENDPOINT_UNSAFE")
            runtime = json.loads(runtime_body)
            if runtime.get("schema") != "grid-console-local-runtime-v1" or (
                runtime.get("provenance") != "LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED" or
                runtime.get("live_orders") != "DISABLED" or
                runtime.get("account_connection") != "NOT_CONNECTED" or
                runtime.get("reconciled_for_trading") is not False
            ):
                raise RuntimeError("LOCAL_RUNTIME_CONTRACT_UNSAFE")
            if supervisor_db is None and runtime.get("status") != "NOT_CONFIGURED":
                raise RuntimeError("UNEXPECTED_SUPERVISOR_STATE")
            if supervisor_db is not None and runtime.get("status") in ("NOT_CONFIGURED", None):
                raise RuntimeError("SUPERVISOR_DATABASE_NOT_READ")
            index_code, html, html_headers = get("/")
            if index_code != 200 or "text/html" not in html_headers.get("Content-Type", "") or b'id="root"' not in html:
                raise RuntimeError("REACT_INDEX_UNAVAILABLE")
            # Only hashed compiled JS/CSS is accepted. No legacy app.js fallback.
            scripts = re.findall(rb'src="\./assets/([A-Za-z0-9_.-]+\.js)"', html)
            styles = re.findall(rb'href="\./assets/([A-Za-z0-9_.-]+\.css)"', html)
            if not scripts or not styles:
                raise RuntimeError("REACT_COMPILED_ASSETS_MISSING")
            for name, expected_type in [(x, "javascript") for x in scripts] + [(x, "text/css") for x in styles]:
                route = "/assets/" + name.decode("ascii")
                asset_status, content, meta = get(route)
                if asset_status != 200 or not content or expected_type not in meta.get("Content-Type", ""):
                    raise RuntimeError("REACT_COMPILED_ASSET_UNAVAILABLE")
            if get("/app.js")[0] != 404 or get("/style.css")[0] != 404:
                raise RuntimeError("LEGACY_ASSET_FALLBACK_FORBIDDEN")
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                for path in ("/api/status", "/api/runtime", "/"):
                    code, _, _ = get(path, method)
                    if code != 405:
                        raise RuntimeError("UNSAFE_HTTP_MUTATION_ENABLED")
        finally:
            server.shutdown()
            thread.join(timeout=5)

    return {
        "schema": SCHEMA,
        "status": "DEV_DESKTOP_PASS",
        "ui": "GRID_CONSOLE_REACT_LOCAL_GUI",
        "runtime_status": runtime["status"],
        "risk_lock": runtime["risk_lock"],
        "uncertain_order_count": runtime["uncertain_order_count"],
        "local_event_count": len(runtime["events"]),
        "react_assets": len(scripts) + len(styles),
        "http_bind": "127.0.0.1",
        "local_assets": "VERIFIED",
        "account_connection": "NOT_CONNECTED",
        "live_orders": "DISABLED",
        "market_collector": "NOT_STARTED",
        "authenticated_account_get": "NOT_RUN",
        "exchange_mutations": 0,
        "signed_installer": False,
        "sellable_product": "NO_GO",
    }


def run_gui(*, open_browser: bool = True) -> int:
    """Tkinter desktop control window; existing dashboard opens in browser."""
    assert_safe_env()
    import tkinter as tk
    from tkinter import filedialog, messagebox

    server = local_server(None, 0, ui="react")
    url = f"http://127.0.0.1:{server.server_port}/"
    worker = Thread(target=server.serve_forever, daemon=True, name="grid-console-http")
    worker.start()
    window = None
    closed = False

    def shutdown() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        if window is not None:
            window.destroy()

    try:
        # Tk exists only in graphical mode, not in CI smoke/--self-test.
        window = tk.Tk()
        window.title("Grid Console — Turvallinen esikatselu")
        window.geometry("600x430")
        window.minsize(540, 405)
        window.configure(bg="#0e1626")
        window.protocol("WM_DELETE_WINDOW", shutdown)

        frame = tk.Frame(window, bg="#0e1626", padx=28, pady=22)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="GRID CONSOLE", font=("Segoe UI", 22, "bold"),
                 bg="#0e1626", fg="#f4f6fb", anchor="w").pack(fill="x")
        tk.Label(frame, text="WINDOWS-SOVELLUS · OFFLINE-ESIKATSELU",
                 font=("Segoe UI", 9, "bold"), bg="#0e1626",
                 fg="#7bc7bd", anchor="w").pack(fill="x", pady=(0, 24))
        info = (
            "Paikallinen hallintanäkymä on valmiina.\n"
            "Revolut X -tiliyhteys: EI YHDISTETTY\n"
            "Oikea kaupankäynti: ESTETTY\n"
            "Markkinadatan keräin: EI KÄYNNISSÄ"
        )
        tk.Label(frame, text=info, bg="#182438", fg="#e4edf5",
                 justify="left", anchor="w", padx=18, pady=18,
                 font=("Segoe UI", 11), width=54).pack(fill="x")

        note = tk.Label(
            frame, text="Tämä on allekirjoittamaton kehitysesikatselu, ei asiakasjulkaisu. "
                        "Selaimen sulkeminen ei pysäytä paikallista palvelinta; "
                        "sulje tämä ikkuna lopettaaksesi.",
            font=("Segoe UI", 9), bg="#0e1626", fg="#b2bfd3",
            justify="left", wraplength=530, anchor="w",
        )
        note.pack(fill="x", pady=(14, 14))
        actions = tk.Frame(frame, bg="#0e1626")
        actions.pack(fill="x")

        def open_dashboard() -> None:
            if closed:
                return
            try:
                if not webbrowser.open(url, new=1):
                    raise RuntimeError("BROWSER_OPEN_FAILED")
            except Exception:
                messagebox.showerror("Grid Console", "Selaimen avaaminen epäonnistui. "
                                     "Sovelluksen paikallinen palvelin pysyy käynnissä.")

        db_status = tk.Label(frame, text="Valvojan tietokanta: ei valittu",
                             bg="#0e1626", fg="#a9b9cb", font=("Segoe UI", 9),
                             anchor="w")
        db_status.pack(fill="x", pady=(0, 8))

        def select_local_supervisor_db() -> None:
            nonlocal server, worker, url
            if closed:
                return
            selected = filedialog.askopenfilename(
                title="Valitse paikallinen OFFLINE-valvojan SQLite-tietokanta",
                filetypes=[("SQLite-tietokanta", "*.sqlite *.db"), ("Kaikki tiedostot", "*.*")],
            )
            if not selected:
                return
            candidate = Path(selected)
            inspection = inspect_local_runtime(candidate)
            if inspection.get("reason") in (
                "DATABASE_UNAVAILABLE", "UNRECOGNIZED_DATABASE_SCHEMA",
                "LOCAL_DATABASE_READ_FAILED",
            ):
                messagebox.showwarning(
                    "Grid Console", "Tiedostoa ei voida tunnistaa turvalliseksi "
                    "paikalliseksi valvojan tietokannaksi. Yhteyttä ei avattu."
                )
                return
            try:
                next_server = local_server(None, 0, supervisor_db=candidate, ui="react")
                next_worker = Thread(target=next_server.serve_forever, daemon=True,
                                     name="grid-console-local-db-http")
                next_worker.start()
            except Exception:
                messagebox.showerror("Grid Console",
                                     "Paikallisen tietokantanäkymän avaaminen epäonnistui.")
                return
            previous_server, previous_worker = server, worker
            server, worker = next_server, next_worker
            url = f"http://127.0.0.1:{server.server_port}/"
            previous_server.shutdown()
            previous_server.server_close()
            previous_worker.join(timeout=5)
            db_status.configure(text="Valvojan tietokanta: paikallinen lukutila")
            open_dashboard()

        tk.Button(frame, text="Valitse valvojan paikallinen tietokanta (vain luku)",
                  command=select_local_supervisor_db,
                  bg="#1d3642", fg="#e7f0f4", font=("Segoe UI", 9),
                  relief="flat", padx=12, pady=7).pack(fill="x", pady=(0, 8))

        tk.Button(actions, text="Avaa Grid Console", command=open_dashboard,
                  bg="#53ccb7", fg="#102030", font=("Segoe UI", 11, "bold"),
                  relief="flat", padx=16, pady=10).pack(side="left")
        tk.Button(actions, text="Sulje sovellus", command=shutdown,
                  bg="#25344b", fg="#e7edf5", font=("Segoe UI", 10),
                  relief="flat", padx=16, pady=10).pack(side="right")
        if open_browser:
            window.after(350, open_dashboard)
        window.mainloop()
        return 0
    finally:
        shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grid Console Windows offline-only GUI")
    parser.add_argument("--self-test", action="store_true",
                        help="Verify local HTML dashboard and GET-only routes; no GUI")
    parser.add_argument("--report", type=Path, default=None,
                        help="Write redacted self-test JSON to file")
    parser.add_argument("--supervisor-db", type=Path, default=None,
                        help="Offline SQLite fixture for --self-test only; never writes")
    parser.add_argument("--no-auto-browser", action="store_true",
                        help="Open browser only by clicking dashboard button")
    args = parser.parse_args(argv)
    if args.report is not None and not args.self_test:
        parser.error("--report is restricted to --self-test")
    if args.supervisor_db is not None and not args.self_test:
        parser.error("--supervisor-db is restricted to --self-test")
    try:
        if args.self_test:
            result = status_through_loopback(args.supervisor_db)
            if args.report is not None:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n",
                                       encoding="utf-8")
            if sys.stdout is not None:
                print(json.dumps(result, sort_keys=True))
            return 0
        return run_gui(open_browser=not args.no_auto_browser)
    except Exception:
        # PyInstaller --windowed has no console; fail closed without leaking paths.
        if args.self_test and args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({
                "schema": SCHEMA, "status": "DEV_DESKTOP_FAIL", "live_orders": "DISABLED",
                "sellable_product": "NO_GO",
            }, sort_keys=True) + "\n", encoding="utf-8")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
