"""Private S3 off-volume SQLite archive and remote restore verification.

Only called explicitly; no exchange/network reads except configured object bucket.
Credentials are injected by Railway variable references; never logged.
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

from scripts.offsite_backup_transport import (
    BACKUP_PROTOCOL, check_scratch_separate, digest_file, snapshot_to_scratch,
)

DEFAULT_ENDPOINT = "https://t3.storageapi.dev"
PREFIX = "revx-eth-eur-shadow/v06"


def s3_settings() -> dict:
    vals = {
        "endpoint": os.getenv("REVX_S3_ENDPOINT", ""),
        "bucket": os.getenv("REVX_S3_BUCKET", ""),
        "region": os.getenv("REVX_S3_REGION", "auto"),
        "key_id": os.getenv("REVX_S3_ACCESS_KEY_ID", ""),
        "secret": os.getenv("REVX_S3_SECRET_ACCESS_KEY", ""),
    }
    if (vals["endpoint"] != DEFAULT_ENDPOINT
            or not vals["bucket"].startswith("revx-eth-eur-shadow-backups-")
            or not vals["key_id"] or not vals["secret"]):
        raise RuntimeError("PRIVATE_S3_BACKUP_NOT_CONFIGURED")
    return vals


def client_from_settings(settings: dict):
    # Local import keeps trade/shadow core free of third-party network clients.
    import boto3
    from botocore.config import Config as S3Config
    return boto3.client(
        "s3", endpoint_url=settings["endpoint"],
        region_name=settings["region"],
        aws_access_key_id=settings["key_id"],
        aws_secret_access_key=settings["secret"],
        config=S3Config(signature_version="s3v4", s3={"addressing_style": "virtual"},
                        retries={"max_attempts": 1, "mode": "standard"}),
    )


def upload_and_restore(source: Path, *, key: str, scratch: Path = Path("/tmp"),
                       s3=None, configured: dict | None = None,
                       enforce_separate: bool = True) -> dict:
    """Live round trip: remote upload, fresh GET, gzip extraction, hash & sqlite QA.

    Never stores a full copy on the original /data volume. Return status only
    after remote GET returns the exact same bytes and SQLite is usable.
    """
    if not key.startswith(PREFIX + "/") or ".." in key or "//" in key or len(key)>180:
        raise ValueError("BACKUP_OBJECT_KEY_OUTSIDE_ALLOWED_PREFIX")
    settings = configured or s3_settings()
    if enforce_separate:
        check_scratch_separate(source, scratch)
    service = s3 or client_from_settings(settings)
    with tempfile.TemporaryDirectory(prefix="revx-s3-", dir=scratch) as tmp:
        folder = Path(tmp)
        raw, archive, meta = snapshot_to_scratch(source, folder)
        with archive.open("rb") as stream:
            service.upload_fileobj(
                stream, settings["bucket"], key,
                ExtraArgs={"ContentType":"application/gzip",
                           "Metadata":{"sha256":meta["archive_sha256"],
                                       "source-sha256":meta["source_sha256"]}})
        # IMPORTANT: download a fresh copy; upload success alone is not proof.
        restored_gzip = folder / "restored.sqlite.gz"
        with restored_gzip.open("wb") as file:
            service.download_fileobj(settings["bucket"], key, file)
            file.flush()
            os.fsync(file.fileno())
        if digest_file(restored_gzip) != meta["archive_sha256"]:
            raise RuntimeError("REMOTE_ARCHIVE_SHA256_MISMATCH")
        restored = folder / "restored.sqlite"
        with gzip.open(restored_gzip, "rb") as packed, restored.open("wb") as dest:
            for chunk in iter(lambda: packed.read(1024*1024), b""):
                dest.write(chunk)
            dest.flush()
            os.fsync(dest.fileno())
        if digest_file(restored) != meta["source_sha256"]:
            raise RuntimeError("RESTORED_SQLITE_SHA256_MISMATCH")
        with closing(sqlite3.connect(restored.as_uri()+"?mode=ro",uri=True)) as db:
            integrity=db.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity!="ok":
                raise RuntimeError("RESTORED_SQLITE_INTEGRITY_FAILED")
        return {
            "status": "REMOTE_RESTORE_VERIFIED", "protocol": BACKUP_PROTOCOL,
            "archive_sha256":meta["archive_sha256"],
            "source_sha256":meta["source_sha256"],
            "archive_size_bytes":meta["archive_size_bytes"],
            "source_size_bytes":meta["source_size_bytes"],
            "remote_object_key":key,
            "remote_download_sha256_match":True,
            "sqlite_integrity":"PASS", "exchange_mutations":0,
            "live_trading":"DISABLED",
        }


def synthetic_restore_smoke() -> dict:
    """Explicit no-exchange small sample; preserve bucket artifact for audit."""
    if os.getenv("LIVE_TRADING","false").lower()!="false":
        raise RuntimeError("LIVE_TRADING_MUST_BE_DISABLED")
    if os.getenv("REVX_ARCHIVE_SMOKE_APPROVAL","")!="ONLY_SYNTHETIC_SQLITE_RESTORE":
        raise RuntimeError("SYNTHETIC_BACKUP_APPROVAL_REQUIRED")
    settings=s3_settings()
    from datetime import datetime,timezone
    from uuid import uuid4
    with tempfile.TemporaryDirectory(prefix="revx-test-",dir="/tmp") as tmp:
        source=Path(tmp)/"synthetic.sqlite"
        with closing(sqlite3.connect(source)) as db:
            db.execute("CREATE TABLE evidence(label TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO evidence VALUES (?,?)",("kind","synthetic_offsite_restore_test"))
            db.commit()
        key=PREFIX+"/synthetic/"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid4().hex+".sqlite.gz"
        return upload_and_restore(source,key=key,scratch=Path("/tmp"),
                                  configured=settings,enforce_separate=False)


def main() -> int:
    # Status-only mode is never enabled here; this script is a one-shot smoke.
    try:
        result=synthetic_restore_smoke()
    except Exception as exc:
        result={"status":"BLOCKED","error_code":type(exc).__name__,
                "exchange_mutations":0,"live_trading":"DISABLED"}
    print("RAILWAY_S3_RESTORE_SMOKE "+json.dumps(result,sort_keys=True),flush=True)
    return 0 if result["status"]=="REMOTE_RESTORE_VERIFIED" else 2


if __name__=="__main__":
    raise SystemExit(main())
