"""Offline-safe SQLite backup export: scratch disk, HTTPS transport, remote receipt.

Backup bytes NEVER occupy the Railway /data volume. A local scratch copy is
not durable and MUST NOT count as a backup without independently verified
remote receipt. No destination is configured by default; fail closed.
"""
from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlparse

import requests

BACKUP_PROTOCOL = "revx-sqlite-offsite-v1"
SCRATCH_ENV = "REVX_BACKUP_SCRATCH_DIR"
DEST_ENV = "REVX_BACKUP_UPLOAD_URL"
TOKEN_ENV = "REVX_BACKUP_UPLOAD_TOKEN"
MAX_RECEIPT_BYTES = 4096


def digest_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def ensure_destination(url: str) -> None:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or parsed.port not in (None, 443)
            or parsed.hostname.lower() in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("BACKUP_HTTPS_DESTINATION_INVALID")


def check_scratch_separate(source: Path, scratch_root: Path) -> None:
    """Require non-volume scratch; don't allow a directory inside /data or same fs."""
    original = source.resolve(strict=True)
    scratch = scratch_root.resolve(strict=True)
    if scratch == original.parent or scratch in original.parents:
        raise ValueError("BACKUP_SCRATCH_ON_SOURCE_VOLUME")
    if original == scratch or scratch in original.parents:
        raise ValueError("BACKUP_SCRATCH_UNSAFE")
    if os.stat(original).st_dev == os.stat(scratch).st_dev:
        raise ValueError("BACKUP_SCRATCH_SAME_FILESYSTEM")


def snapshot_to_scratch(source: Path, work: Path) -> tuple[Path, Path, dict]:
    """SQLite Online Backup API, then gzip; all files only on scratch storage."""
    if not source.is_file() or source.is_symlink():
        raise ValueError("BACKUP_SOURCE_UNAVAILABLE")
    raw = work / "snapshot.sqlite"
    compressed = work / "snapshot.sqlite.gz"
    source_uri = source.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=15)) as reader:
        with closing(sqlite3.connect(raw)) as writer:
            reader.backup(writer)
    with closing(sqlite3.connect(raw.as_uri() + "?mode=ro", uri=True)) as snapshot:
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("BACKUP_INTEGRITY_FAILED")
    with raw.open("rb") as stream, compressed.open("wb") as out:
        with gzip.GzipFile(fileobj=out, mode="wb", filename="", mtime=0) as archive:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                archive.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    metadata = {
        "protocol": BACKUP_PROTOCOL,
        "source_sha256": digest_file(raw),
        "source_size_bytes": raw.stat().st_size,
        "archive_sha256": digest_file(compressed),
        "archive_size_bytes": compressed.stat().st_size,
    }
    return raw, compressed, metadata


def offsite_backup(source: Path, *, upload_url: str | None = None,
                   token: str | None = None, scratch_root: Path | None = None,
                   session=None) -> dict:
    """Export compressed verified snapshot. No remote receipt => raises.

    The receiving service must atomically store the uploaded file and return
    HTTP 200 JSON {\"sha256\":sha256, \"durable\":true}. A matching receipt alone
    is a contract assertion; separately exercise remote download/retention
    before accepting this transport for the 24h pilot.
    """
    url = upload_url if upload_url is not None else os.getenv(DEST_ENV, "")
    secret = token if token is not None else os.getenv(TOKEN_ENV, "")
    if not url or not secret or len(secret) < 16:
        raise RuntimeError("OFFSITE_BACKUP_DESTINATION_NOT_CONFIGURED")
    ensure_destination(url)
    root = scratch_root or Path(os.getenv(SCRATCH_ENV, "/tmp"))
    check_scratch_separate(source, root)
    with tempfile.TemporaryDirectory(prefix="revx-backup-", dir=root) as folder:
        _, archive, evidence = snapshot_to_scratch(source, Path(folder))
        client = session or requests.Session()
        if session is None:
            client.trust_env = False
        try:
            with archive.open("rb") as payload:
                response = client.put(
                    url, data=payload, allow_redirects=False,
                    timeout=(10, 180), headers={
                        "Authorization": "Bearer " + secret,
                        "Content-Type": "application/gzip",
                        "X-Content-SHA256": evidence["archive_sha256"],
                        "X-Source-SHA256": evidence["source_sha256"],
                        "X-Backup-Protocol": BACKUP_PROTOCOL,
                    })
            if response.status_code != 200 or len(response.content) > MAX_RECEIPT_BYTES:
                raise RuntimeError("BACKUP_REMOTE_UPLOAD_NOT_CONFIRMED")
            receipt = response.json()
            if (not isinstance(receipt, dict)
                    or receipt.get("sha256") != evidence["archive_sha256"]
                    or receipt.get("durable") is not True):
                raise RuntimeError("BACKUP_REMOTE_RECEIPT_MISMATCH")
            evidence.update(status="REMOTE_RECEIPT_VERIFIED", remote_durable_asserted=True)
            return evidence
        finally:
            if session is None:
                client.close()
