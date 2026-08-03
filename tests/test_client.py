import base64
import tempfile
import unittest
from decimal import Decimal
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
        self.last_kwargs = None

    def request(self, *args, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self.responses.pop(0)


class ClientTests(unittest.TestCase):
    def _key_file(self):
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(pem)
        handle.flush()
        return handle, key

    def test_signature_message_exact_format(self) -> None:
        message = RevolutXClient._signature_message(
            "123", "post", "/api/1.0/orders", "limit=10", '{"x":1}'
        )
        self.assertEqual(message, b'123POST/api/1.0/orderslimit=10{"x":1}')

    def test_signed_header_verifies_exact_message(self) -> None:
        handle, key = self._key_file()
        try:
            client = RevolutXClient("x" * 64, handle.name)
            with patch("revolut_x_client.time.time_ns", return_value=1_765_360_896_219_000_000):
                headers = client._signed_headers(
                    "post",
                    "/1.0/orders",
                    "",
                    '{"client_order_id":"abc"}',
                )
            self.assertEqual(headers["X-Revx-Timestamp"], "1765360896219")
            message = b'1765360896219POST/api/1.0/orders{"client_order_id":"abc"}'
            signature = base64.b64decode(headers["X-Revx-Signature"])
            key.public_key().verify(signature, message)
        finally:
            Path(handle.name).unlink(missing_ok=True)

    def test_place_order_serializes_decimal_without_float(self) -> None:
        handle, _ = self._key_file()
        session = FakeSession([FakeResponse(200, payload={"data": {"venue_order_id": "v1"}})])
        try:
            client = RevolutXClient("x" * 64, handle.name, session=session)
            client.place_limit_order(
                client_order_id="c1",
                symbol="BTC-USD",
                side="buy",
                base_size=Decimal("0.1000"),
                price=Decimal("120000.50"),
            )
            self.assertEqual(
                session.last_kwargs["data"],
                '{"client_order_id":"c1","symbol":"BTC-USD","side":"buy","order_configuration":{"limit":{"base_size":"0.1000","price":"120000.50","execution_instructions":["post_only"]}}}',
            )
            with self.assertRaises(TypeError):
                client.place_limit_order(
                    client_order_id="c2",
                    symbol="BTC-USD",
                    side="buy",
                    base_size=0.1,
                    price=Decimal("120000.50"),
                )
        finally:
            Path(handle.name).unlink(missing_ok=True)

    def test_429_retry_after_is_milliseconds(self) -> None:
        handle, _ = self._key_file()
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
