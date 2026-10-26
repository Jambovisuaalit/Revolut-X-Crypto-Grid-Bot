# Railway Free: v0.6 24h SQLite growth and off-volume backup gate

Status **NO_GO / no 24h GET authorization / no trading**. This brief supersedes the older assumption that a full SQLite backup fits on the 500 MB Railway Volume.

## Measured three-minute GET-only pilot (2026-10-18)

The run finished in 175059 ms with 36/36 accepted ETH/EUR books, full SQLite chain PASS. Persisted DB/WAL footprint: 299008 bytes. Accepted book JSON total: 208710 bytes (mean 5797.5 bytes/book). Actual Volume free after the run and inert status redeploy: 443768832 bytes. Collector needs 268435456 bytes free.

Target 24h interval: 86400 / 5 = 17280 book observations. **Naive full-footprint scaling**: 299008 * (17280 / 36) = **143523840 bytes**. This deliberately scales fixed SQLite schema and initial candle history as if they repeated, which tends to overstate size. However, it does NOT prove that API payload size, WAL behavior, candle revisions, retained data, or fragmentation remain stable for 24h.

25% buffer: 179404800 bytes vs **175333376 bytes** budget above 256 MiB free reserve. Shortfall: **4071424 bytes** (~3.9 MiB). Buffer gate: **FAIL**. An off-volume backup removes the *extra local copy*, but does not alone cure this projected 25% headroom failure. Do not reduce the reserve, reduce sampling or discard raw observations merely to make a GO.

## Backup implementation (not active)

- `eth_shadow_v1/offsite_backup.py`: sqlite online backup into a temporary directory on a *different filesystem*, integrity check, deterministic gzip, SHA-256 for raw DB and gzip.
- HTTPS upload only, explicit destination `REVX_BACKUP_UPLOAD_URL` and token `REVX_BACKUP_UPLOAD_TOKEN`, no redirects, no retries, 200 JSON receipt with exact archive digest and durable=true. No endpoint or credentials are configured now.
- Temporary snapshot is removed upon exit. The Railway `/data` Volume stores original SQLite, state and final metadata, **not** the full duplicate.
- Receipt is NOT an independent restore proof. Before approving 24h, provision a real controlled offsite receiver, verify independent download and bytewise hash, retention policy, failure handling, archive size, and cost. User authorization is required before configuring endpoints or additional external network actions.
- If upload destination or scratch isolation is absent, final report remains BLOCKED. There is no fallback to saving a second SQLite DB on /data.
- `eth_shadow_v1/capacity_preflight.py` computes 24h estimate and reserve budget from measured short pilot. It never authorizes the 24h pilot.

## Unresolved hard gates

1. 25%-margin sizing fails by 4,071,424 bytes on current measurement; extended bounded, authorized non-trading capture or validated compression strategy is needed for better model. No additional GET calls were approved or started for this work.
2. No configured external durable upload receiver, independent restore QA or retention confirmation.
3. Railway Free serverless is required; outbound GETs generally keep worker awake, but 24h uninterrupted availability is not proven.
4. Actual disk reserve guard must hold throughout, 24h slot completeness and full audit must pass.
5. Separate explicit user authorization is required before 24h network collection, no live account access and no trading.

The active Railway service remains pinned to its previous safe status-only commit, unless separately audited and published; creating code on the branch does not change that running source.

## 26.10.2026 — Measured capacity admission gate

The v0.6 `arm` and `run` admission now refuse a missing, malformed, partial or unverified persisted `/data/three-minute-probe/final-report.json`. Both recompute the 25% 24-hour disk margin using *current free bytes*, before recording ARMED or RUNNING. Backup settings must also be present; the check does not contact the backup endpoint.

Measured requirement: **447,840,256 current free bytes** = 268,435,456 reserve + 179,404,800 projected with 25% growth buffer. Previous reported free bytes 443,768,832 are **4,071,424 short**. A larger approved persistent volume is one option. Never lower reserves or discard raw books to bypass this gate.

This code guard does **not** approve costs, verify remote restore, authorize public GET traffic or trading, or prove 24-hour availability. The pilot stays BLOCKED until all separate owner approvals and external evidence pass. No merge to `main`.
