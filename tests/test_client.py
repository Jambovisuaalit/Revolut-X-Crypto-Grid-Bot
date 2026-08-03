import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from revolut_x_client import RevolutXClient


class FakeResponse:
    def __init__(self, status_code: int, *, headers=None, payload=None, text="") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        self.text = text
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload


class FakeSession(requests.Session):
    def __init__(self, responses) -> None:
        super().__init__()
        self.responses = list(responses)
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


class ClientTests(unittest.TestCase):
    def _key_file(self) -> tempfile.NamedTemporaryFile:
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(pem)
        handle.flush()
        return handle

    def test_signature_message_exact_format(self) -> None:
        message = RevolutXClient._signature_message(
            "123", "post", "/api/1.0/orders", "limit=10", '{"x":1}'
        )
        self.assertEqual(message, b'123POST/api/1.0/orderslimit=10{"x":1}')

    def test_429_retry_after_is_milliseconds(self) -> None:
        handle = self._key_file()
        session = FakeSession(
            [
                FakeResponse(429, headers={"Retry-After": "25"}),
                FakeResponse(200, payload=[]),
            ]
        )
        try:
            client = RevolutXClient("x" * 64, handle.name, session=session)
            with patch("revolut_x_client.time.sleep") as sleep:
                self.assertEqual(client.get_balances(), [])
                sleep.assert_called_once_with(0.025)
            self.assertEqual(session.calls, 2)
        finally:
            Path(handle.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
