"""Windows-only per-user DPAPI credential vault for the GET-only reader.

No exchange requests, no plaintext key files, no machine-scope DPAPI,
no API key CLI arguments, no remote reporting. The user's public Ed25519
key is the *only* material intended for display/export.

Security boundary: protects encrypted data at rest for the current Windows
user. Processes already running as that user (or a compromised system/admin)
can still use the key. Python cannot guarantee zeroisation of memory.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Iterator

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from account_readonly import AuthenticatedAccountReader, IDENTITY

_MAGIC = b"GCVAULT1\x00"
_VERSION = 1
_MAX_BLOB_BYTES = 16_384
_CRYPTO_UI_FORBIDDEN = 0x1
_VAULT_NAME = "account.vault"
_LOCK_NAME = "account.lock"
_APP_FOLDER = "GridConsole"
_VAULT_FOLDER = "vault"


class VaultError(RuntimeError):
    """Sanitized failure; never includes a key, file content or decrypted data."""


class WindowsUserVault:
    """DPAPI CurrentUser encrypted credentials with protected NTFS ACLs.

    root is injectable only for tests; normal consumers use LOCALAPPDATA.
    The vault never loads any plaintext PEM from disk.
    """

    def __init__(self, *, root: Path | None = None) -> None:
        if sys.platform != "win32":
            raise VaultError("WINDOWS_ONLY")
        if root is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise VaultError("LOCALAPPDATA_MISSING")
            root = Path(local) / _APP_FOLDER / _VAULT_FOLDER
        self.root = Path(root).absolute()
        if str(self.root).startswith("\\\\"):
            raise VaultError("NETWORK_SHARE_FORBIDDEN")
        self.vault_file = self.root / _VAULT_NAME
        self.lock_file = self.root / _LOCK_NAME

    @staticmethod
    def _native() -> tuple[Any, ...]:
        """Delay Windows-only imports so Linux cannot accidentally use the vault."""
        import win32api
        import win32con
        import win32crypt
        import win32security
        import msvcrt
        return win32api, win32con, win32crypt, win32security, msvcrt

    @staticmethod
    def _assert_not_reparse(path: Path) -> None:
        win32api, win32con, *_ = WindowsUserVault._native()
        if not path.exists() and not path.is_symlink():
            return
        attrs = win32api.GetFileAttributes(str(path))
        if attrs & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
            raise VaultError("REPARSE_POINT_FORBIDDEN")

    def _secure(self, path: Path) -> None:
        """Apply and verify a protected DACL limited to current user + SYSTEM."""
        win32api, win32con, _, sec, _ = self._native()
        self._assert_not_reparse(path)
        with sec.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY) as token:
            user_sid = sec.GetTokenInformation(token, sec.TokenUser)[0]
        sys_sid = sec.ConvertStringSidToSid("S-1-5-18")
        acl = sec.ACL()
        acl.AddAccessAllowedAce(sec.ACL_REVISION, win32con.GENERIC_ALL, user_sid)
        acl.AddAccessAllowedAce(sec.ACL_REVISION, win32con.GENERIC_ALL, sys_sid)
        sec.SetNamedSecurityInfo(
            str(path), sec.SE_FILE_OBJECT,
            sec.DACL_SECURITY_INFORMATION | sec.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, acl, None,
        )
        sd = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT,
                                      sec.DACL_SECURITY_INFORMATION)
        actual = sd.GetSecurityDescriptorDacl()
        if actual is None or actual.GetAceCount() != 2:
            raise VaultError("VAULT_ACL_INVALID")
        allowed = {
            sec.ConvertSidToStringSid(user_sid),
            sec.ConvertSidToStringSid(sys_sid),
        }
        for i in range(actual.GetAceCount()):
            ace = actual.GetAce(i)
            if (ace[0][0] != sec.ACCESS_ALLOWED_ACE_TYPE or
                sec.ConvertSidToStringSid(ace[2]) not in allowed):
                raise VaultError("VAULT_ACL_UNEXPECTED_ENTRY")
        control, _ = sd.GetSecurityDescriptorControl()
        if not control & sec.SE_DACL_PROTECTED:
            raise VaultError("VAULT_ACL_INHERITANCE_NOT_DISABLED")

    def _directory(self) -> None:
        # Check ancestors under the app-controlled directory, including
        # a Windows junction that Path.is_symlink() may not identify.
        for path in (self.root.parent, self.root):
            self._assert_not_reparse(path)
        self.root.mkdir(parents=True, exist_ok=True)
        self._secure(self.root)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Non-blocking interprocess lock; automatically freed on process exit."""
        _, _, _, _, msvcrt = self._native()
        self._directory()
        fd = os.open(str(self.lock_file), os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o600)
        try:
            self._secure(self.lock_file)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise VaultError("VAULT_BUSY") from None
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)

    def _encode(self, state: dict[str, Any]) -> bytes:
        _, _, crypto, _, _ = self._native()
        plain = json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(plain) > 4096:
            raise VaultError("INVALID_VAULT_RECORD")
        try:
            encrypted = crypto.CryptProtectData(
                plain, "GridConsole CurrentUser GET-only credentials",
                None, None, None, _CRYPTO_UI_FORBIDDEN,
            )
        except Exception:
            raise VaultError("DPAPI_PROTECT_FAILED") from None
        if not isinstance(encrypted, bytes):
            raise VaultError("DPAPI_INVALID_RESULT")
        output = _MAGIC + encrypted
        if len(output) > _MAX_BLOB_BYTES:
            raise VaultError("VAULT_BLOB_TOO_LARGE")
        return output

    def _decode(self) -> dict[str, Any]:
        _, _, crypto, _, _ = self._native()
        if not self.vault_file.is_file():
            raise VaultError("VAULT_NOT_INITIALIZED")
        self._secure(self.vault_file)
        if self.vault_file.stat().st_size > _MAX_BLOB_BYTES:
            raise VaultError("VAULT_BLOB_TOO_LARGE")
        with self.vault_file.open("rb") as f:
            blob = f.read(_MAX_BLOB_BYTES + 1)
        if len(blob) > _MAX_BLOB_BYTES or not blob.startswith(_MAGIC):
            raise VaultError("VAULT_INVALID_HEADER")
        try:
            _, plain = crypto.CryptUnprotectData(
                blob[len(_MAGIC):], None, None, None, _CRYPTO_UI_FORBIDDEN,
            )
            if not isinstance(plain, bytes) or len(plain) > 4096:
                raise ValueError("invalid DPAPI plain data")
            state = json.loads(plain)
            if not isinstance(state, dict) or state.get("version") != _VERSION:
                raise ValueError("unknown vault schema")
            raw = base64.b64decode(state["signing_key_raw"], validate=True)
            if len(raw) != 32:
                raise ValueError("wrong Ed25519 key size")
            Ed25519PrivateKey.from_private_bytes(raw)
            token = state.get("api_key")
            if token is not None and (
                not isinstance(token, str) or not IDENTITY.fullmatch(token)
            ):
                raise ValueError("invalid API key")
            return state
        except Exception:
            raise VaultError("DPAPI_UNPROTECT_OR_SCHEMA_FAILED") from None

    def _atomic_write(self, state: dict[str, Any], *, new: bool = False) -> None:
        """Write ciphertext ONLY, sync, then atomically replace old ciphertext."""
        if new and self.vault_file.exists():
            raise VaultError("VAULT_ALREADY_INITIALIZED")
        encrypted = self._encode(state)
        tmp = self.root / ("vault." + secrets.token_hex(16) + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
        try:
            self._secure(tmp)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(fd)
            os.close(fd)
            fd = -1
            self._assert_not_reparse(self.vault_file)
            os.replace(str(tmp), str(self.vault_file))
            self._secure(self.vault_file)
        finally:
            if fd != -1:
                os.close(fd)
            if tmp.exists():
                tmp.unlink()

    def initialize(self) -> str:
        """Generate a signing key locally; return PUBLIC PEM only.

        Private bytes exist transiently in process memory and DPAPI ciphertext.
        """
        with self._locked():
            if self.vault_file.exists():
                raise VaultError("VAULT_ALREADY_INITIALIZED")
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            state = {
                "version": _VERSION,
                "signing_key_raw": base64.b64encode(raw).decode("ascii"),
                "api_key": None,
            }
            self._atomic_write(state, new=True)
            return key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii")

    def public_key_pem(self) -> str:
        """Read only public key for Revolut X registration (safe to display)."""
        with self._locked():
            state = self._decode()
            raw = base64.b64decode(state["signing_key_raw"])
            key = Ed25519PrivateKey.from_private_bytes(raw)
            return key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii")

    def bind_api_key(self, api_key: str) -> None:
        """Store the user's key entered locally. No network or order action."""
        if not isinstance(api_key, str) or not IDENTITY.fullmatch(api_key):
            raise VaultError("INVALID_API_KEY_FORMAT")
        with self._locked():
            state = self._decode()
            if state["api_key"] is not None:
                raise VaultError("API_KEY_ALREADY_BOUND_USE_EXPLICIT_REPLACEMENT_FLOW")
            state["api_key"] = api_key
            self._atomic_write(state)

    def status(self) -> dict[str, Any]:
        """Only non-sensitive status: no API key, private key, balance or paths."""
        with self._locked():
            if not self.vault_file.exists():
                return {"state": "NOT_INITIALIZED", "api_key_linked": False}
            state = self._decode()
            key = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(state["signing_key_raw"])
            )
            public_raw = key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw,
            )
            return {
                "state": "LOCAL_ONLY",
                "api_key_linked": state["api_key"] is not None,
                "public_key_fingerprint": hashlib.sha256(public_raw).hexdigest()[:16],
                "live_trading": "DISABLED",
                "real_account_test": "NOT_RUN",
            }

    def make_get_only_reader(self, *, transport: Any) -> AuthenticatedAccountReader:
        """Only explicit injected transport (mock until approved real test).

        Deliberately no default network transport in this vault's interface.
        """
        if transport is None or not hasattr(transport, "get"):
            raise VaultError("EXPLICIT_GET_TRANSPORT_REQUIRED")
        with self._locked():
            state = self._decode()
            if state["api_key"] is None:
                raise VaultError("API_KEY_NOT_BOUND")
            key = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(state["signing_key_raw"])
            )
            return AuthenticatedAccountReader(
                state["api_key"], key, transport=transport
            )

    def remove_local(self, *, confirmed: bool = False) -> None:
        """Remove local ciphertext, never pretend to revoke the venue API key."""
        if confirmed is not True:
            raise VaultError("LOCAL_REMOVAL_REQUIRES_EXPLICIT_CONFIRMATION")
        with self._locked():
            if not self.vault_file.exists():
                raise VaultError("VAULT_NOT_INITIALIZED")
            self._secure(self.vault_file)
            self.vault_file.unlink()
