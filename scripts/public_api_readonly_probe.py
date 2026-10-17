"""One unauthenticated public Revolut X GET. No credentials; no order endpoints.

This is a REAL MARKET DATA smoke check, not a sandbox trading contract test.
Only this hardcoded public URL is permitted; no URL from user input.
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

URL = "https://revx.revolut.com/api/2.0/public/order-book/BTC-USD"


def main() -> None:
    request = Request(URL, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=8) as response:
        if response.status != 200:
            raise RuntimeError("public order book returned non-200 response")
        body = response.read(1024 * 1024)
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("unexpected public order-book envelope")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("unexpected public order-book data")
    # Public v2 may enrich or vary its market data structure; do not
    # interpret success as validation of authenticated POST/PUT schemas.
    print("PUBLIC_GET_ONLY_OK; authenticated_order_contract=NOT_TESTED")


if __name__ == "__main__":
    main()
