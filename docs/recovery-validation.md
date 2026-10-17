# Grid Bot P0: POST/PUT recovery and kill-switch validation

Date: 2026-10-17
Target: fix/revx-contract-idempotent-recovery (PR #3, draft)
Production activation: NOT AUTHORIZED

## 1. Authoritative sources

Official Revolut X API documentation:
https://developer.revolut.com/docs/api/revolut-x-crypto-exchange

Official Revolut open-source API companion and security guidelines:
https://github.com/revolut-engineering/revolut-x-api

API contracts checked:

- POST /api/1.0/orders: accepts client UUID, pair, side and post-only limit
  configuration; response contains data with venue order ID, client ID and state.
- PUT /api/1.0/orders/{venue_order_id}: flat replacement body with new client
  UUID, price/base_size and execution_instructions=[post_only].
- GET /api/1.0/orders/{venue_order_id}: authenticated authoritative order detail.
- GET /api/1.0/orders/active and /historical: paginated reads. Historical
  defaults have finite retention; absence from history does not prove a write
  was rejected.
- Documentation describes PUT response data as object but illustrates a list.
  Client accepts an object or EXACTLY ONE replacement item and rejects
  empty/multiple, invalid UUIDs or mismatched client ID. This discrepancy
  is not considered proof of a real authenticated contract test.
- Official published order schema does not promise previous_order_id. Recovery
  must not assume it exists. When present, it must match persisted parent ID.
  When absent, a successful child adoption also requires the distinct
  parent venue order to be confirmed REPLACED via authenticated GET, the
  unique persisted child chain, exact child symbol/side/limit/price/qty
  and post_only and an unambiguous matching client UUID.

## 2. Offline crash / fault matrix

Tests: tests/test_bot.py, tests/test_atomic_intents.py,
tests/test_failure_matrix.py, tests/test_risk_boundary.py.

Crash points exercised:
- After SQLite intent commit but before HTTP POST or PUT (hard os._exit):
  a pending reservation persists, there is no safe proof of rejection, so
  startup reconciliation blocks rather than resending.
- Venue mutation accepted, acknowledgement lost (SystemExit and hard
  os._exit; timeout, connection reset): restart adopts only an independently
  verifiable venue order. No second POST/PUT is issued.
- Ack received then process dies before GET: pending intent is recovered via
  GET and paginated active/historical snapshots.
- GET succeeded then process dies before SQLite acknowledgement:
  pending intent survives and is reconciled on restart.
- SQLite acknowledgement committed before restart: same client UUID returns
  stored order, no duplicate network submission.
- Malformed/mismatching client UUID, venue UUID, side, pair, order type,
  price, quantity, post_only, remaining size, state, or linked parent:
  no local acceptance; execution stays blocked until verified.
- PUT parent must have persisted correct version/root chain and be confirmed
  status=replaced on the venue. Parent and successor must not both be active.
- Two SQLite connections share BEGIN IMMEDIATE intent and reservation locks;
  duplicate logical requests or simultaneous child replacements do not
  produce two venue writes.

Risk tests:
- >=3.0% initial-equity drawdown triggers persistent kill flag BEFORE
  attempting account-level cancel-all. Cancellation failure keeps flag set.
- Exact 5% order/free-balance cap and aggregate SQLite reservation cap.
- Cross-instance pending intent blocks all further mutations.
- Re-check persistent halt, current intent and competing unknown orders
  just before network dispatch. This closes a known pre-dispatch race.

## 3. Authenticated test status

OFFLINE CONTRACTS: tested against official documented response examples and
a synthetic venue mock.
PUBLIC ONLINE SMOKE: GitHub CI safely calls only GET
https://revx.revolut.com/api/2.0/public/order-book/BTC-USD
without credentials, without mutations, and validates the response envelope.

AUTHENTICATED READ-ONLY API: NOT TESTED; no user-provided API credentials were
used or requested in CI.
REVOLUT X TRADING SANDBOX: NOT VERIFIED; official docs link a separate
Business sandbox but do not establish an authenticated Revolut X test-trading
environment. No real POST/PUT or DELETE was used.
The public GET passing is NOT evidence that authenticated order placement,
replacement or cancellation functions end-to-end.

## 4. Residual risks – release blockers

1. SQLite intent + external HTTP is not a distributed atomic transaction.
   In-flight POST/PUT may race with another process's kill switch AFTER the
   last local dispatch check. Exchange-side limits and isolated sub-account
   are necessary; no code-only guarantee is claimed.
2. A crash before HTTP dispatch is observationally indistinguishable from
   a delivered request with lost result if no order is returned by the venue.
   This case intentionally requires operator/manual incident resolution.
   Never auto-retry or delete pending intent based only on absence of
   recent history.
3. Read-only GET immediately after a successful mutation may be eventually
   consistent. A stale, mismatched or unavailable GET MUST leave pending
   lineage intact until a later explicit reconciliation.
4. Account balances passed by caller are snapshots. SQLite can serialize
   local reservations, but external spending, free-balance changes, quote
   freshness, API key privileges and sub-account ownership remain separately
   unproven.
5. Account-level DELETE /orders cancels every order in that authenticated
   account, not only this bot's orders. Isolate the bot in its own sub-account.
6. There is no authenticated venue sandbox proof or independent real order
   trace. No live activation until approved in a separate decision.
7. Keep PR #3 DRAFT and the production default LIVE_TRADING=false.

## 5. Operator runbook on uncertainty

1. Stop all new bot writes. Do not rotate/reuse client_order_id.
2. Preserve SQLite WAL, current database, timestamps and incident identifiers.
3. Using a read-only account-scoped client under the owner's control, inspect
   active and historical orders across all relevant pages and details via
   GET /orders/{venue_order_id}; cross-check fills.
4. Match exact client UUID, venue UUID, symbol, side, order type, post_only,
   quantity, price and state. For PUT, check parent order terminal/replaced
   and only one known child in persisted lineage. Never assume undocumented
   previous_order_id is always available.
5. If proof is incomplete, keep halted and escalate to venue support/owner.
   No 'not found -> retry' transition is implemented.
6. Resumption, modifying/deleting pending lineage, or resetting kill switch
   requires a separate explicit owner-approved operational procedure and
   read-only reconciliation; this branch does not implement an unsafe reset.

## 6. Decision

API payload shapes, deterministic fault recovery, persistent kill switch and
SQLite-local idempotency may be verified by CI. The required user acceptance
of an authenticated exchange integration and live-trading safety is NOT met.
No market orders, limit orders, or cancellations were sent in this work.
