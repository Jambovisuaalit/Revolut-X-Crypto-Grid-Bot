"""Windows native DPAPI/ACL integration + Linux fail-closed tests.

All credentials are generated synthetically inside runner temp directories.
No real user key, authenticated API call or network access.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization

from windows_key_vault import WindowsUserVault, VaultError

TEST_API = "B" * 64


class PlatformGuard(unittest.TestCase):
    def test_01_non_windows_rejected(self):
        if sys.platform == "win32":
            self.skipTest("Windows native tests below")
        with self.assertRaisesRegex(VaultError, "WINDOWS_ONLY"):
            WindowsUserVault()


@unittest.skipUnless(sys.platform == "win32", "Windows DPAPI native-only")
class WindowsDPAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "app" / "vault"
        self.vault = WindowsUserVault(root=self.root)

    def test_02_create_public_only_private_never_written_as_plaintext(self):
        public = self.vault.initialize()
        self.assertIn("BEGIN PUBLIC KEY", public)
        self.assertNotIn("PRIVATE KEY", public)
        self.assertEqual(self.vault.public_key_pem(), public)
        data = self.vault.vault_file.read_bytes()
        self.assertTrue(data.startswith(b"GCVAULT1"))
        self.assertNotIn(b"PRIVATE KEY", data)
        self.assertNotIn(b"signing_key_raw", data)
        self.assertNotIn(TEST_API.encode(), data)
        key = serialization.load_pem_public_key(public.encode())
        signature = self._reader_after_link()._private_key.sign(b"test")
        key.verify(signature, b"test")

    def _reader_after_link(self):
        self.vault.bind_api_key(TEST_API)
        return self.vault.make_get_only_reader(transport=FakeTransport())

    def test_03_owner_scoped_dpapi_round_trip_and_snapshot_safe(self):
        self.vault.initialize()
        self.vault.bind_api_key(TEST_API)
        self.assertEqual(self.vault.status()["state"], "LOCAL_ONLY")
        self.assertTrue(self.vault.status()["api_key_linked"])
        self.assertEqual(len(self.vault.status()["public_key_fingerprint"]), 16)
        reader = self.vault.make_get_only_reader(transport=FakeTransport())
        self.assertEqual(reader.balances(), ())
        self.assertEqual(reader.snapshot().live_trading_approval, "NO_GO")
        self.assertFalse(reader.snapshot().reconciled_for_trading)
        self.assertNotIn(TEST_API, repr(reader))
        self.assertNotIn(TEST_API.encode(), self.vault.vault_file.read_bytes())

    def test_04_create_twice_and_implicit_rebind_forbidden(self):
        self.vault.initialize()
        with self.assertRaisesRegex(VaultError, "ALREADY_INITIALIZED"):
            self.vault.initialize()
        self.vault.bind_api_key(TEST_API)
        with self.assertRaisesRegex(VaultError, "ALREADY_BOUND"):
            self.vault.bind_api_key("A"*64)
        self.assertNotIn("api_key", self.vault.status())

    def test_05_mutation_of_ciphertext_fail_closed(self):
        self.vault.initialize()
        data = bytearray(self.vault.vault_file.read_bytes())
        data[-1] ^= 0x01
        self.vault.vault_file.write_bytes(data)
        with self.assertRaisesRegex(VaultError, "DPAPI_UNPROTECT_OR_SCHEMA_FAILED"):
            self.vault.status()

    def test_06_acl_limited_to_user_and_system_and_protected(self):
        import win32security
        self.vault.initialize()
        for path in (self.root, self.vault.vault_file, self.vault.lock_file):
            sd=win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                                   win32security.DACL_SECURITY_INFORMATION)
            dacl=sd.GetSecurityDescriptorDacl()
            self.assertEqual(dacl.GetAceCount(), 2)
            self.assertTrue(sd.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED)
            ids={win32security.ConvertSidToStringSid(dacl.GetAce(i)[2]) for i in range(2)}
            self.assertIn("S-1-5-18", ids)

    def test_07_local_removal_requires_confirmation_and_not_revocation(self):
        self.vault.initialize()
        with self.assertRaisesRegex(VaultError, "REQUIRES_EXPLICIT_CONFIRMATION"):
            self.vault.remove_local()
        self.assertTrue(self.vault.vault_file.exists())
        self.vault.remove_local(confirmed=True)
        self.assertEqual(self.vault.status()["state"], "NOT_INITIALIZED")
        with self.assertRaisesRegex(VaultError, "NOT_INITIALIZED"):
            self.vault.public_key_pem()

    def test_08_lock_rejects_simultaneous_modification(self):
        self.vault.initialize()
        second=WindowsUserVault(root=self.root)
        with self.vault._locked():
            with self.assertRaisesRegex(VaultError, "VAULT_BUSY"):
                second.bind_api_key(TEST_API)
        second.bind_api_key(TEST_API)
        self.assertTrue(second.status()["api_key_linked"])

    def test_09_fail_closed_unexpected_key_and_symlink(self):
        self.vault.initialize()
        for raw in ("short", "a"*63, "é"*64):
            with self.subTest(raw=raw),self.assertRaisesRegex(VaultError,"INVALID_API_KEY_FORMAT"):
                self.vault.bind_api_key(raw)
        self.assertFalse(self.vault.status()["api_key_linked"])

    def test_10_existing_foreign_file_cannot_be_adopted(self):
        self.root.mkdir(parents=True,exist_ok=True)
        self.vault.vault_file.write_text("not a DPAPI secret")
        with self.assertRaises(VaultError):
            self.vault.initialize()
        with self.assertRaises(VaultError):
            self.vault.status()

    def test_11_explicit_transport_is_required_to_get_reader(self):
        self.vault.initialize()
        self.vault.bind_api_key(TEST_API)
        with self.assertRaisesRegex(VaultError,"EXPLICIT_GET_TRANSPORT_REQUIRED"):
            self.vault.make_get_only_reader(transport=None)
        with self.assertRaisesRegex(VaultError,"EXPLICIT_GET_TRANSPORT_REQUIRED"):
            self.vault.make_get_only_reader(transport=object())


class FakeResponse:
    status_code=200
    headers={}
    def __init__(self, data):
        import json
        self.content=json.dumps(data).encode("utf8")
    def iter_content(self, chunk_size=65536):
        yield self.content
    def close(self):
        pass


class FakeTransport:
    def get(self, url, *, headers, timeout, stream, allow_redirects, params):
        if url.endswith("/balances"):
            return FakeResponse([])
        if "/orders/" in url:
            return FakeResponse({"data":[], "metadata":{"timestamp":1}})
        raise AssertionError("unexpected endpoint")


if __name__=="__main__":
    unittest.main()
