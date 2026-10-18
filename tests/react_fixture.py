"""Synthetic compiled-asset fixture for Python tests; never used in Windows release."""
from pathlib import Path


def make_react_fixture(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    (root / "index.html").write_text(
        '<!doctype html><html lang="fi"><head>'
        '<link rel="stylesheet" href="./assets/index-test123.css">'
        '</head><body><div id="root"></div>'
        '<script type="module" src="./assets/index-test123.js"></script>'
        '</body></html>',
        encoding="utf-8",
    )
    (assets / "index-test123.js").write_text(
        'console.log("LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED");', encoding="utf-8"
    )
    (assets / "index-test123.css").write_text(
        "body { background: #0e1626; }", encoding="utf-8"
    )
    return root
