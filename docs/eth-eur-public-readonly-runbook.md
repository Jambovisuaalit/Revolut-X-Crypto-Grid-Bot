# ETH/EUR public market data capture — pre-forward-shadow gate

Status: READ-ONLY / NO LIVE TRADING.

## API sources (official Revolut X REST API 1.0.0)

- `GET https://revx.revolut.com/api/1.0/public/configuration/pairs?region=EEA`
- `GET https://revx.revolut.com/api/2.0/public/order-book/ETH-EUR?limit=50&region=EEA`
- `GET https://revx.revolut.com/api/1.0/public/candles/ETH-EUR?interval=15&since=<unix-ms>&until=<unix-ms>&region=EEA`

Source: https://developer.revolut.com/docs/api/revolut-x-crypto-exchange

Endpoints are public without credentials and limited to 1 request/second.
Transport is exact allowlisted HTTPS GET, no redirects, no cookies, no auth,
no mutation or API keys; <=3 book polls per invocation, five seconds apart.

## Configuration evidence

Response key must be `ETH/EUR` (slash); book URL symbol is `ETH-EUR` (dash).
Parse current `base_step`, `quote_step`, `min_order_size`,
`max_order_size`, `min_order_size_quote`, and `status` as Decimal strings.
Do not assume the *documentation example* fields are current market reality.
The public pair-list request selects EEA, while the public order book response
must independently return `metadata.region = EEA` with a UNIX-ms source timestamp.

## Sampling

```sh
python scripts/capture_eth_eur_public_once.py --samples 3 \
  --capture public-data/capture.json --report public-data/report.json
```

This command performs exactly one finite collection, not a recurring job.
The run writes to local files or CI artifacts. It **does not** invoke the
shadow trading runner; no orders, virtual or real, are submitted.

Data is rejected or marked blocked when missing, stale (>3s), from the wrong
region, crossed, with invalid quantities, non-monotonic timestamps or with
incomplete/unclosed/nonconsecutive 15-minute candle history. Assembling the
200 latest *closed* candles requires excluding the current bar.
Candle volume 0 is retained, not fabricated.

A successful result is `READY_FOR_OFFLINE_INSPECTION`, not GO for a 30-day
forward trial. It does not prove maker fill probability or available depth
outside the returned 50 levels. Offline tests cannot prove the REST service
is reachable. Public data do not verify this user's authenticated account
region or account-specific trading permissions.

## Downstream gates (not enabled by this feature branch)

1. Independently review captured public pair rules and source timestamps.
2. Check and retain data completeness, latency, spread and depth over enough
   real samples. Compare book liquidity with per-lot sizing and tick/lot steps.
3. Enable a separately approved, persistent forward shadow *collector* with
   bounded rate and immutable, replayable records. Do not auto-start it here.
4. Continue 30d replay / 30–90d forward testing, benchmark, drawdown checks.
5. Even then, live trading remains separately blocked.

Protect local captured data from accidental git commits; CI artifacts are
temporary, and public depth has no guarantee of historical reproducibility.
