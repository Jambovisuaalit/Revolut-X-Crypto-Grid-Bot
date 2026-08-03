# AGENTS.md — Revolut X Crypto Grid Bot (MVP)

You are an expert Senior Crypto Systems Engineer specializing in Python 3.11+, high-frequency REST API integration, and low-latency execution engines.

## 🎯 Primary Goal
Build a robust, production-ready, post-only Limit Order Grid Trading Bot for the Revolut X REST API, adhering strictly to the locked MVP Specification.

## 🛠️ Tech Stack & Constraints
- **Language:** Python 3.11+
- **Persistence:** SQLite (`bot_state.db`) with native `sqlite3`
- **Crypto & Auth:** `cryptography.hazmat.primitives.asymmetric.ed25519`
- **HTTP:** `requests` with custom backoff logic for HTTP 429
- **Precision:** `decimal.Decimal` with `ROUND_DOWN` strictly applied to all prices and quantities. NEVER use `float` for financial calculations.

## 🏗️ Project Architecture
The repository MUST adhere to the following module layout:

```text
├── revolut_x_client.py # Ed25519 auth, HTTP client, 429 backoff engine
├── precision_engine.py # Step size rounding, pair rules, pre-flight checks
├── grid_executor.py # Grid logic, SQLite persistence, atomic PUT replacements
├── bot_main.py # Supervisor loop & 3.0% Drawdown Kill-Switch
├── bot_state.db # SQLite database (created automatically)
├── Dockerfile # Container configuration with chrony NTP
└── requirements.txt # Dependencies
```

## ⚠️ Non-Negotiable Rules (Hard Guards)
1. **Ed25519 Signing:** String format MUST be `Timestamp + Method + Path + Query + Body` without separators. Path MUST include the `/api` prefix (e.g., `/api/1.0/balances`). Body MUST be minified (`json.dumps(body, separators=(',', ':'))`).
2. **Post-Only Maker Orders:** Always include `"execution_instructions": ["post_only"]` when placing or replacing orders.
3. **Capital Allocation:** Single order size MUST NOT exceed 5% of available free balance.
4. **Hard Kill-Switch:** If total drawdown reaches or exceeds 3.0% (`(initial - current) / initial * 100`), immediately trigger `DELETE /1.0/orders`, set `kill_switch_triggered = True`, and halt execution.
5. **No System Crashes on 429:** Intercept HTTP 429, extract `Retry-After` header milliseconds, sleep, and retry automatically.
6. **Exchange Reconciliation:** On startup, fetch all open Revolut X orders and reconcile them against SQLite before creating, replacing, or cancelling any order.
7. **Idempotency:** Never submit a duplicate logical grid order after timeout, retry, process restart, or uncertain HTTP response.
8. **Pair Rules:** Load pair-specific `base_step`, `quote_step`, min/max order sizes and status from Revolut X configuration. Do not hardcode production pair precision.
9. **Equity Definition:** Drawdown MUST use total marked-to-market portfolio equity expressed in one configured quote currency. Persist initial equity before trading begins.
10. **Fail Closed:** Authentication errors, malformed API responses, reconciliation mismatches, unknown order states, stale market data, or precision-rule failures MUST stop new order creation rather than guess.
11. **Dry Run Default:** Live order mutations MUST remain disabled unless explicitly enabled through configuration. Startup must never place live orders by default.

## 🧪 Code Quality Standards
- Include strict type hints (`typing`) on all function signatures.
- Write docstrings for classes and public methods.
- Handle exceptions gracefully with clean `logging` outputs. Never log private keys or raw secret headers.
- Financial calculations MUST remain in `Decimal` from parsing through validation and serialization.
- Public API assumptions MUST match current official Revolut X API documentation before implementation.
