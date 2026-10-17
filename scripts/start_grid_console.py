"""Start local-only Grid Console preview; no trading or market API calls.

Future production installers must add signed releases, local credential vault
and authenticated read-only account link before any live-capable release.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

# Support `python scripts/start_grid_console.py` without PYTHONPATH.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from product_shell.server import local_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid Console local preview; never trades")
    parser.add_argument("--db", type=Path, default=None,
                        help="Optional existing local read-only collector SQLite DB")
    parser.add_argument("--port", type=int, default=0,
                        help="Optional localhost port. 0 chooses a free local port")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port out of range")
    with local_server(args.db, args.port) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        print("Grid Console · turvallinen esikatselu")
        print("Oikea kaupankäynti ESTETTY. Ei tiliavaimia tai ulkoista yhteyttä.")
        print("Avaa selaimessa:", url)
        print("Lopeta sulkemalla tämä ikkuna tai painamalla Ctrl+C.")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.3)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
