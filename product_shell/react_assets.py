"""Strict static-only Vite bundle loader for localhost Grid Console.

No remote URLs, dynamic file traversal, source-map exposure or development
server fallback. Missing assets abort React startup instead of serving a
misleading blank dashboard. Existing legacy assets remain separately available.
"""
from __future__ import annotations

from pathlib import Path
import re

CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
SAFE_ASSET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.(?:js|css|svg|png|woff2?)\Z")
REFERENCES = re.compile(rb'(?:src|href)="(\./assets/[A-Za-z0-9_.-]+)"')
MAX_ASSET_BYTES = 10 * 1024 * 1024
MAX_BUNDLE_BYTES = 30 * 1024 * 1024


def load_react_assets(root: Path) -> dict[str, tuple[bytes, str]]:
    """Load only safe compiled assets into an immutable per-server allowlist."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("REACT_BUNDLE_MISSING")
    index = root / "index.html"
    asset_dir = root / "assets"
    if index.is_symlink() or not index.is_file() or asset_dir.is_symlink() or not asset_dir.is_dir():
        raise RuntimeError("REACT_BUNDLE_INCOMPLETE")
    if index.stat().st_size > 128_000:
        raise RuntimeError("REACT_INDEX_TOO_LARGE")
    html = index.read_bytes()
    # Reject Vite development entrypoints and remote HTML resources.
    if b"/src/" in html or b"localhost:" in html or b"http://" in html or b"https://" in html:
        raise RuntimeError("REACT_UNBUILT_OR_REMOTE_HTML")
    references = REFERENCES.findall(html)
    if not references or not any(x.endswith(b".js") for x in references) or not any(x.endswith(b".css") for x in references):
        raise RuntimeError("REACT_ENTRYPOINT_MISSING")
    # Do not silently accept remote, absolute, or unrecognized script/style refs.
    for attr in re.findall(rb'(?:src|href)="([^"]+)"', html):
        if not attr.startswith(b"./assets/") or attr not in references:
            raise RuntimeError("REACT_UNEXPECTED_ASSET_REFERENCE")
    result: dict[str, tuple[bytes, str]] = {
        "/": (html, "text/html; charset=utf-8"),
    }
    total = len(html)
    for file in asset_dir.iterdir():
        if file.is_symlink() or not file.is_file() or not SAFE_ASSET.fullmatch(file.name):
            raise RuntimeError("REACT_UNSAFE_BUNDLE_ASSET")
        if file.stat().st_size > MAX_ASSET_BYTES:
            raise RuntimeError("REACT_BUNDLE_ASSET_TOO_LARGE")
        total += file.stat().st_size
        if total > MAX_BUNDLE_BYTES:
            raise RuntimeError("REACT_BUNDLE_TOO_LARGE")
        result["/assets/" + file.name] = (file.read_bytes(), CONTENT_TYPES[file.suffix])
    for reference in references:
        key = "/" + reference.decode("ascii")[2:]
        if key not in result:
            raise RuntimeError("REACT_BUNDLE_REFERENCE_MISSING")
    return result
