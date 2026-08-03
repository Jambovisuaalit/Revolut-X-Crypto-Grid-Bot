# Revolut X Crypto Grid Bot

Production-oriented MVP for a post-only limit-order grid strategy on the Revolut X REST API.

## Safety defaults

- `Decimal` only for financial arithmetic; no `float` financial calculations.
- All place/replace operations carry `execution_instructions: ["post_only"]`.
- Single-order allocation is hard-capped at 5% of relevant available balance.
- 3.0% marked-to-market portfolio drawdown triggers `DELETE /1.0/orders`, persists the kill switch, and halts.
- HTTP 429 uses Revolut X `Retry-After` milliseconds and retries without crashing.
- Startup reconciles persisted bot order state against active exchange orders.
- `DRY_RUN=true` by default. Live mutations require both `DRY_RUN=false` and `LIVE_TRADING_ENABLED=true`.
- API keys and private keys are never committed or logged.

## Official API assumptions

- Base URL: `https://revx.revolut.com/api/`
- Signed message: `Timestamp + Method + /api/path + Query + minified JSON Body`, no separators.
- Pair precision/rules are loaded from `/1.0/public/configuration/pairs`.
- Active orders: `/1.0/orders/active`.
- Place: `POST /1.0/orders`.
- Atomic replace: `PUT /1.0/orders/{venue_order_id}`.
- Kill-switch cancellation: `DELETE /1.0/orders`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Generate/register the Revolut X Ed25519 key pair in Revolut X, then provide the API key and private-key path through environment variables or your secret manager. Do not store the private key in Git.

Load environment variables with your preferred secret tooling and start in dry-run mode:

```bash
python bot_main.py
```

## Live-mode gate

Live order mutations require both:

```text
DRY_RUN=false
LIVE_TRADING_ENABLED=true
```

Before live activation, verify pair configuration, balances, ticker freshness, SQLite reconciliation, and the persisted `initial_equity`. The bot deliberately fails closed on uncertain order state or malformed exchange data.

## Docker

```bash
docker build -t revolut-x-grid-bot .
```

Mount the Ed25519 private key as a runtime secret. The image includes `chrony`; production deployments must still guarantee accurate host time and grant clock capabilities only when operationally justified.

## Tests

```bash
python -m unittest discover -s tests -v
```

## OpenAI

The trading runtime has no OpenAI API dependency. OpenAI/Codex can be used for repository development and review without introducing an `OPENAI_API_KEY` into the bot runtime.
