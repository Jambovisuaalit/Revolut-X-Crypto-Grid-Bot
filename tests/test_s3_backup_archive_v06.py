"""Pure fake-S3 tests; never call actual cloud, exchange, or account endpoints."""
from __future__ import annotations
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.s3_backup_archive import upload_and_restore, PREFIX, s3_settings, synthetic_restore_smoke


class FakeS3:
    def __init__(self, corrupt=False):
        self.data={}
        self.corrupt=corrupt
        self.uploads=0
        self.downloads=0
    def upload_fileobj(self, reader, bucket, key, ExtraArgs):
        self.uploads+=1
        self.data[(bucket,key)]=reader.read()
    def download_fileobj(self, bucket, key, out):
        self.downloads+=1
        data=self.data[(bucket,key)]
        out.write(data[:-1]+b'x' if self.corrupt else data)


class S3BackupTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.db=self.root/"source.sqlite"
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
            db.execute("INSERT INTO probe VALUES(1,'synthetic')")
            db.commit()
        self.settings={"endpoint":"https://t3.storageapi.dev",
                       "bucket":"revx-eth-eur-shadow-backups-fake",
                       "region":"auto","key_id":"local","secret":"local"}

    def test_private_roundtrip_recovers_hash_and_schema(self):
        fake=FakeS3()
        with TemporaryDirectory() as scratch:
            res=upload_and_restore(
                self.db,key=PREFIX+"/synthetic/fake.sqlite.gz",scratch=Path(scratch),
                s3=fake,configured=self.settings,enforce_separate=False)
            self.assertEqual(sorted(Path(scratch).iterdir()),[])
        self.assertEqual(res["status"],"REMOTE_RESTORE_VERIFIED")
        self.assertEqual(res["sqlite_integrity"],"PASS")
        self.assertEqual(fake.uploads,1)
        self.assertEqual(fake.downloads,1)
        self.assertEqual(res["exchange_mutations"],0)

    def test_remote_corruption_fail_closed(self):
        fake=FakeS3(corrupt=True)
        with TemporaryDirectory() as scratch:
            with self.assertRaisesRegex(RuntimeError,"REMOTE_ARCHIVE_SHA256_MISMATCH"):
                upload_and_restore(
                    self.db,key=PREFIX+"/synthetic/fake.sqlite.gz",scratch=Path(scratch),
                    s3=fake,configured=self.settings,enforce_separate=False)
            self.assertEqual(list(Path(scratch).iterdir()),[])

    def test_no_credentials_fails_before_connection(self):
        with patch.dict("os.environ", {"REVX_S3_ENDPOINT":"",
                                       "REVX_S3_ACCESS_KEY_ID":"",
                                       "REVX_S3_SECRET_ACCESS_KEY":""}):
            with self.assertRaisesRegex(RuntimeError,"PRIVATE_S3_BACKUP_NOT_CONFIGURED"):
                s3_settings()

    def test_reject_out_of_scope_object_key(self):
        with self.assertRaisesRegex(ValueError,"BACKUP_OBJECT_KEY_OUTSIDE_ALLOWED_PREFIX"):
            upload_and_restore(self.db,key="../unsafe",s3=FakeS3(),
                               configured=self.settings,enforce_separate=False)

    def test_no_smoke_without_specific_approval(self):
        with patch.dict("os.environ", {"REVX_ARCHIVE_SMOKE_APPROVAL":""}):
            with self.assertRaisesRegex(RuntimeError,"SYNTHETIC_BACKUP_APPROVAL_REQUIRED"):
                synthetic_restore_smoke()

if __name__=="__main__":
    unittest.main()
