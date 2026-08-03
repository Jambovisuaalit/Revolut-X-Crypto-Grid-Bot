"""Read-only Revolut X production smoke test. Never mutates orders."""

from __future__ import annotations

import logging
import os
import sys
from decimal import Decimal

from bot_main import BotConfig, _pair_key, portfolio_equity
from precision_engine import PairRules
from revolut_x_client import RevolutXClient, RevolutXError

LOGGER = logging.getLogger(__name__)


def run() -> int:
    """Validate authentication and production response shapes without trading."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = BotConfig.from_env()
        client = RevolutXClient(config.api_key, config.private_key_path)

        pair_key = _pair_key(config.symbol)
        account_pairs = client.get_account_pair_configuration()
        if pair_key not in account_pairs:
            raise RevolutXError(
                f"account pair configuration missing for {pair_key}"
            )
        rules = PairRules.from_api(pair_key, account_pairs[pair_key])

        if config.region:
            public_pairs = client.get_pair_configuration(config.region)
            if pair_key not in public_pairs:
                raise RevolutXError(
                    f"public pair configuration missing for {pair_key} in {config.region}"
                )

        balances = client.get_balances()
        tickers, exchange_ts = client.get_tickers()
        active_orders = client.get_active_orders([config.symbol])
        equity = portfolio_equity(
            balances=balances,
            tickers=tickers,
            quote_currency=config.quote_currency,
        )

        if rules.status != "active":
            raise RevolutXError(f"pair {pair_key} is not active")
        if exchange_ts <= 0 or equity <= Decimal("0"):
            raise RevolutXError("invalid ticker timestamp or equity")

        LOGGER.info(
            "SMOKE TEST PASS pair=%s balances=%d tickers=%d active_orders=%d equity_quote=%s",
            pair_key,
            len(balances),
            len(tickers),
            len(active_orders),
            config.quote_currency,
        )
        return 0
    except (KeyError, TypeError, ValueError, ArithmeticError, RevolutXError) as exc:
        LOGGER.error("SMOKE TEST FAIL: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
