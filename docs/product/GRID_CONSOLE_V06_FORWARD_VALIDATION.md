# Grid Console v0.6 — Shadow Forward Validation (stage gates)

Status: DEVELOPMENT / LIVE=NO_GO / SELLABLE=NO_GO / FORWARD_30D=NOT_STARTED.

## Baseline and scope

- Base: PR #19, `ae68c8efd8c72b7570041bca1422db6920cff7b0`.
- ETH/EUR, EEA; shadow-only, initial virtual €1,000, 4 grid levels, €400 maximum cost basis and €30 hard loss / equity floor €970. Locked `configs/eth_eur_grid_shadow_v1.toml`.
- Public unauthenticated GET book collection already exists in `eth_shadow_v1/collector.py`, but collecting a book is not the same as running a deterministic shadow transition.
- Collector quality `eth_shadow_v1/collector_quality.py` checks spread, freshness, candles, hash integrity, availability and pair stability; no 24h live data quality run has been evidenced as passing.
- `eth_shadow_v1/forward_preflight.py` is a **read-only audit**, not a scheduler or a collector launcher.

## Gate A — 24h evidence, independent of profitability

Accept only when the full audit chain passes, the pair is active/unchanged, candles and heartbeat are fresh, capture has at least one full 24-hour period, >=99% of all scheduled five-second slots are observed AND >=99% contain accepted observations. Require collector's own `DATA_READY_FOR_REVIEW` and no alerts; the preflight additionally prevents repeated observations in the same 5-second bin from boosting availability. For a full 24-hour period the expected number of five-second slots is **17,280**. The check is intentionally conservative: source timestamps, quality-window integrity and capture availability may be invalid despite visually plausible prices.

Output fields include `data_24h`, `collector_audit`, `observed_unique_slots`, `accepted_unique_slots`, `coverage_ratio`, and `accepted_coverage_ratio`. All `forward_30d=NOT_STARTED` and `economic_approval=NO_GO` regardless of Gate A.

Command once a collector DB has been created in a separately approved public read-only capture run:

```powershell
python -m scripts.check_forward_preflight --collector-db <COLLECTOR.sqlite> --report <REPORT.json>
```

No authenticated API key is needed for the existing public-only collector. Its start, hosting, data retention, bandwidth, and terms of use remain separate approvals; the v0.6 preflight will not start it.

## Gate B — 30–90 calendar-day shadow protocol (not yet implemented)

1. Freeze configuration SHA, pair metadata source, data provenance and a UTC launch timestamp **before** measuring outcomes.
2. Use only time-increasing public orderbook/candle observations with verifiable origins, rejecting missing, stale, duplicated, revised, nonmonotonic and future-dated data.
3. After Gate A and explicit operator permission, create an isolated shadow journal with independent run ID. A replay test of past public data must never be labeled forward time.
4. Persist every eligible/rejected event, synthetic order intent, modeled fill, position, fee, slippage, equity mark and trigger; preserve downtime gaps and permanent hard stop across process restarts.
5. Report daily UTC coverage and cumulative valid calendar days; gaps do not compress calendar duration.
6. Stop/review on lost audit integrity, modified pair rules, invalid price data, stale collector data, hard stop, or journal/portfolio inconsistency.
7. Do **not** confuse software pass with economic approval.

## Gate C — independent economic evidence (not yet implemented)

Publish gross and net P&L, realized and unrealized P&L, estimated fees, slippage, exposure, drawdowns, order frequency, modeled fills, error windows and 40/60 benchmark. Require explicit disclosure of assumptions (especially queue position, maker/post-only probability, depth and adverse-selection bias). Validate order-size constraint: v1 €100 lots do not yet satisfy the separate <=5% of free balance per order commercial risk rule; shadow v2 changes require a new config version and independent evaluation, not silent edits to v1.

Required exit gates: 30 full **calendar** days minimum and 90 days target, documented source integrity, stated risk limits, independent benchmark and pre-registered profitability criteria. Until real observation data and these reports exist, status is `NO_GO`.

## Non-goals / forbidden

- No real balances, authenticated Revolut X API, trading keys, POST/PUT/PATCH/DELETE exchange calls or customer distribution.
- No claim of 30-day return based on five synthetic observations, a replay, an uptime counter, or an unsigned Windows preview.
- Do not merge PR #19 or its successor into main without an explicit approval.
