# ETH/EUR persistent public collector — deployment gate

## State

Feature only, no infrastructure deployed by this PR. All existing trading
settings remain unchanged. The collector does **not** start a forward-shadow
strategy and has no authenticated trading API method.

* Source: https://developer.revolut.com/docs/api/revolut-x-crypto-exchange
* Public order book: GET /api/2.0/public/order-book/ETH-EUR?limit=50&region=EEA
* Public EEA pair configuration: GET /api/1.0/public/configuration/pairs?region=EEA
* 15m OHLCV: GET /api/1.0/public/candles/ETH-EUR?interval=15&since=...&until=...&region=EEA
* Calls are rate-spaced by >=1.1 seconds between *all* GETs (public limit 1/sec).
* Book poll every five seconds, candles every 15 min, pair rules every 6h.
* SQLite WAL FULL durability, one owner lease, raw 50-level order books,
  retained closed candles, pair rule versions, error logs, sha256 provenance.
* No automatic row deletion: keep >=45 days, plan disk accordingly.
* `health` is independently read-only and checks 24h coverage >=99%,
  valid ratio >=99%, source age p95 <=1.5 s, current book <=15 s, gaps <=15 s,
  pair active/no drift, 200+ candles and free disk >=256MB.

## Local bounded smoke (never starts a daemon)

```bash
python -m pip install -r requirements.txt
python scripts/eth_eur_persistent_collector.py run \
  --db ./collector-data/books.sqlite --max-polls 3 \
  --report ./collector-data/health.json
python scripts/eth_eur_persistent_collector.py health \
  --db ./collector-data/books.sqlite --report ./collector-data/health.json
```

The second command returns exit 2 during the initial 24h WARMUP — expected.
No cron/daemon is automatically activated by committing this branch.

## Optional manual systemd install on a dedicated Linux VM

Requires the repo checked out at `/opt/revx-bot`, Python 3.11 venv at
`/opt/revx-bot/.venv`, a separate unprivileged `revxcollector` user/group,
write access only to `/var/lib/revx-eth-eur`, outbound TLS to revx.revolut.com
and enough disk capacity for potentially hundreds of MB/month.

```bash
sudo install -m 0644 deploy/eth-eur-collector.service /etc/systemd/system/
sudo install -m 0644 deploy/eth-eur-collector-health.service /etc/systemd/system/
sudo install -m 0644 deploy/eth-eur-collector-health.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eth-eur-collector.service
sudo systemctl enable --now eth-eur-collector-health.timer
sudo systemctl status eth-eur-collector.service
sudo cat /var/lib/revx-eth-eur/health.json
```

**Not executed in this PR.** Operator must first decide hosting, network budget,
monitoring, backup/retention plan and server operating costs. Unit file
does not contain exchange keys. Service is isolated from trading components.

## Operational failure behavior

* API errors, book staleness, bad region, stale candles, and pair drift
  create explicit rejected observations and/or durable faults.
* Stale lease can be taken over after 60s; no second writer while lease valid.
* If disk free falls below 256 MB, process fails closed. Capture does not
  erase history silently or fabricate missed ticks after restart.
* Source timestamps remain separate from reception timestamps.
* Full raw books and rolling candles allow **future** as-of offline replay.
  A successful three-sample smoke has no bearing on strategy profitability.

## Release gates for data capture

1. Linux and Windows offline tests PASS, bounded real GET smoke PASS.
2. Raw book is persisted (not just BBO), can verify checksum and replay.
3. After deploying on an independently authorized host, collect >=24h
   continuous books with source delay and data completeness report PASS.
4. Only then consider a distinct decision to begin 30-90d forward shadow.
   Live trading still forbidden until an entirely separate approval.
