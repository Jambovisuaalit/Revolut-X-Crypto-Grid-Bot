# Railway 24h ETH/EUR collector pilot — pre-deployment approval record

**Status: BLOCKED_BILLING / NOT_DEPLOYED / NOT_STARTED**
**Scope:** public unauthenticated GET only, ETH/EUR EEA, no wallet, account, API secrets, orders or shadow strategy.
**Branch:** feat/grid-console-v06-forward-validation (Draft PR #20, based on v0.5 Draft PR #19).

## Railway account finding (2026-10-18)

Attempted to create `revolut-x-shadow-validation` in the user's Railway workspace. Railway rejected creation: `Your trial has expired. Please select a plan to continue using Railway.` **No project was created**; no Railway workloads, volumes, or public API requests were started.

Railway docs list Free $0/month, Hobby $5/month (includes $5 resource usage), and volume storage $0.15/GB-month, memory $10/GB-month, CPU $20/vCPU-month, egress $0.05/GB. Workspace compute hard limit minimum $10; setting it impacts **all existing workspace workloads** and can stop/restart them. Do not change shared limits without separate approval.

Source: https://docs.railway.com/pricing/plans ; https://docs.railway.com/pricing/cost-control

## Proposed isolation — not yet provisioned

- Dedicated private project: `revolut-x-shadow-validation`, environment `pilot`, service `eth-eur-public-collector`.
- Source exactly pinned approved v0.6 commit, not floating main or auto-deploy on push; no PR preview deployments.
- Persistent Railway Volume mounted at `/data`, SQLite at `/data/eth-eur-public-books.sqlite`, reports at `/data/reports/`.
- Single worker (no HTTP domain, no externally exposed ingress), 1 replica, no public credentials.
- `LIVE_TRADING=false`, `DRY_RUN=true`; collector only uses the allowlisted public GET calls.
- Resource ceiling proposal: 0.5 GB RAM, 0.5 vCPU; 512 MB volume initially only if plan allows. Reserve at least 256 MB free for fail-closed collector (so 512 MB may be too small depending on book growth). Check projected DB size and adjust volume before approval.
- Runtime dependency install from pinned source and dependency versions. Verify GitHub private-repo OAuth access, resolved commit, source terms and region.
- Restart policy `NEVER`; serverless sleep disabled, no cron schedule. Any crash => BLOCKED pending operator review.
- **Do not use indefinite `collector.run(max_polls=0)` as a 24h pilot.** Implement and CI-test an absolute wall-clock deadline persisted on the volume, stopping new GETs at 24h even after Railway restart/redeploy; ensure start requires a one-time explicit operator approval gate. Count gaps, never catch up or invent samples.
- Use separate read-only quality job/report, verify SQLite hash-chain, include hourly metrics and final full audit. Avoid concurrent WAL reads without proper snapshot semantics.
- Stop & preserve data before disabling service; archive SQLite via SQLite backup API or clean stop/WAL checkpoint, report SHA256 and retained evidence; Railway volume is persistent but not itself an off-platform backup.
- A process reaching 24h must stop and not restart. The wall-clock timer is not proof of 24h accepted data.

## Validation gates before activation

1. Railway plan selected by owner and workspace cost limit implications accepted.
2. Bounded 24h runner, persisted deadline, safety tests and Windows/Ubuntu CI green.
3. Public API conditions and endpoint availability checked; max GET frequency >=1.1 s between requests.
4. Dedicated project/service/volume configuration staged and reviewed, budget estimated, operator explicitly approves deployment + API calls.
5. Begin at a UTC timestamp and monitor; verify heartbeats, no gap >15s, >=99% scheduled slot coverage and >=99% accepted coverage.
6. Only after 24h: `python -m scripts.check_forward_preflight --collector-db ... --report ...`, verify full-chain `PASS`, pair active unchanged, 200 candles, source p95 <=1.5s.
7. Output `DATA_READY_FOR_REVIEW` or `BLOCKED`; forward 30d remains `NOT_STARTED`, economic approval stays `NO_GO`.

## Cost planning, not a quote

Monthly base Free $0 if user can select it; Hobby $5/month billed regardless of 24h use. Indicative 24h resource budget from published unit prices (for example 0.25GB avg RAM and 0.05 avg vCPU, 0.3GB stored): approximately $0.12 for RAM + CPU + disk before egress, rounded; actual measured usage, disk and egress can differ, applicable tax additional. If the 24h usage fits monthly included Hobby usage, subscription total is $5 plus any prior workspace usage/extra overage. Existing workspace resources and their accrued usage have not been assessed; no exact invoice forecast claimed.

## Authorization

Current state: **NO_APPROVAL_FOR_CHARGED_PLAN / NO_APPROVAL_FOR_PUBLIC_CAPTURE**. Require explicit confirmation before any Railway deploy, plan change, persistent volume creation, background collector, or HTTP GET traffic.

Current live and commercial statuses stay NO_GO.
