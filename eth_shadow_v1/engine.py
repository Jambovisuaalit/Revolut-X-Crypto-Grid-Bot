"""Deterministic ETH/EUR shadow state transition. Zero I/O and zero live imports."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .config import Config
from .grid import PairRules, buy_math, levels, quant, sell_math
from .market import Book, entry_blocks, indicators, parse_book

D = Decimal


def fresh_state(cfg: Config) -> dict[str, Any]:
    """Initial virtual 1000 EUR and zero ETH, with sticky risk state."""
    return {
        "config_hash": cfg.sha256, "mode": "shadow", "status": "WAITING_FOR_DATA",
        "cash": str(cfg["initial_cash_eur"]), "eth": "0", "fees": "0",
        "realized_pnl": "0", "anchor": None, "anchor_ms": None,
        "lots": {}, "peak": str(cfg["initial_cash_eur"]), "max_dd": "0",
        "last_source_ms": None, "last_observed_ms": None,
        "invalid_count": 0, "daily_date": None,
        "day_start_equity": str(cfg["initial_cash_eur"]),
        "entry_pause_day": None, "soft_since_ms": None,
        "soft_good_since_ms": None, "last_soft_good_ms": None,
        "closed_cycles": 0, "synthetic_fills": 0, "rejected_signals": 0,
        "data_gaps": 0, "reanchors": 0, "last_equity": str(cfg["initial_cash_eur"]),
        "last_bid": None,
    }


def portfolio_equity(state: dict, bid: Decimal, cfg: Config) -> Decimal:
    """Conservative virtual liquidation value of all owned ETH at the observed bid."""
    return D(state["cash"]) + D(state["eth"]) * bid * (
        1 - cfg["slippage_rate"]) * (1 - cfg["fee_rate"])


def _fill(state: dict, lot: dict, *, index: int, side: str, qty: Decimal,
          price: Decimal, fee: Decimal, cash_change: Decimal,
          event_id: str, source_ms: int) -> dict:
    """Record a local synthetic fill; no exchange payload is ever constructed."""
    fill_id = f"{event_id}:L{index}:{side}:C{lot['cycle']}"
    state["cash"] = str(D(state["cash"]) + cash_change)
    state["eth"] = str(D(state["eth"]) + (qty if side == "BUY" else -qty))
    state["fees"] = str(D(state["fees"]) + fee)
    state["synthetic_fills"] += 1
    return {
        "fill_id": fill_id, "event_id": event_id, "side": side,
        "lot_id": f"L{index}", "cycle_id": f"L{index}:C{lot['cycle']}",
        "source_ts_utc_ms": source_ms, "quantity": str(qty),
        "fill_price": str(price), "fee_eur": str(fee),
        "cash_change_eur": str(cash_change), "synthetic": True,
    }


def _liquidate(state: dict, book: Book, cfg: Config, event_id: str) -> list[dict]:
    """Hard stop: sell only already-owned virtual lots on the current valid bid."""
    fills = []
    for index in sorted(state["lots"], key=int):
        lot = state["lots"][index]
        if lot["status"] != "OPEN_LOT":
            continue
        qty = D(lot["qty"])
        price, proceeds, fee = sell_math(qty, book.bid, cfg)
        fills.append(_fill(state, lot, index=int(index), side="SELL_HARD_STOP",
                           qty=qty, price=price, fee=fee,
                           cash_change=proceeds, event_id=event_id,
                           source_ms=book.source_ms))
        state["realized_pnl"] = str(D(state["realized_pnl"]) + proceeds - D(lot["cost"]))
        lot.update(status="CLOSED", qty="0", cost="0",
                   sell_trigger_ms=None, buy_trigger_ms=None,
                   closed_on_ms=book.source_ms)
    return fills


def _assert_invariants(state: dict, cfg: Config) -> None:
    cash, eth = D(state["cash"]), D(state["eth"])
    open_lots = [lot for lot in state["lots"].values() if lot["status"] == "OPEN_LOT"]
    owned = sum((D(lot["qty"]) for lot in open_lots), D(0))
    cost = sum((D(lot["cost"]) for lot in open_lots), D(0))
    if cash < 0 or eth < 0 or owned != eth or cost > cfg["max_cost_basis_eur"]:
        raise AssertionError("SHADOW_INVARIANT_BROKEN")
    if len(open_lots) > cfg["grid_levels"] or state["mode"] != "shadow":
        raise AssertionError("SHADOW_MODE_OR_LOT_INVARIANT_BROKEN")


def transition(current: dict, event: dict, cfg: Config,
               rules: PairRules) -> tuple[dict, dict]:
    """A single deterministic state transition, independent of wall clock and storage."""
    if current["config_hash"] != cfg.sha256 or current["mode"] != "shadow":
        raise ValueError("state/config mismatch: fail closed")
    state = deepcopy(current)
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id or len(event_id) > 100:
        raise ValueError("event_id required")
    reason: list[str] = []
    fills: list[dict] = []
    action = "HOLD"
    book = None
    try:
        book = parse_book(event, cfg)
        if state["last_source_ms"] is not None and book.source_ms <= state["last_source_ms"]:
            raise ValueError("DATA_NONMONOTONIC")
        if state["last_observed_ms"] is not None and book.observed_ms <= state["last_observed_ms"]:
            raise ValueError("DATA_OBSERVATION_NONMONOTONIC")
    except (KeyError, TypeError, ValueError, ArithmeticError, IndexError) as exc:
        reason.append(str(exc) if isinstance(exc, ValueError) else "DATA_MALFORMED")
        state["invalid_count"] += 1
        observed = event.get("observed_ms")
        stalled = type(observed) is int and state["last_observed_ms"] is not None and (
            observed - state["last_observed_ms"] >= 15000)
        if state["status"] != "STOPPED" and (state["invalid_count"] >= 3 or stalled):
            if state["status"] != "DATA_HALT":
                state["data_gaps"] += 1
            state["status"] = "DATA_HALT"
        for lot in state["lots"].values():
            lot["buy_trigger_ms"] = None
            lot["sell_trigger_ms"] = None
        state["soft_good_since_ms"] = None
        state["last_soft_good_ms"] = None
        action = "DATA_REJECTED"
    else:
        previous_observed_ms = state["last_observed_ms"]
        gap = previous_observed_ms is not None and book.observed_ms - previous_observed_ms > 15000
        if gap:
            state["data_gaps"] += 1
            for lot in state["lots"].values():
                lot["buy_trigger_ms"] = None
                lot["sell_trigger_ms"] = None
            state["soft_good_since_ms"] = None
        state["invalid_count"] = 0
        state["last_source_ms"] = book.source_ms
        state["last_observed_ms"] = book.observed_ms
        state["last_bid"] = str(book.bid)
        nav = portfolio_equity(state, book.bid, cfg)
        state["peak"] = str(max(D(state["peak"]), nav))
        dd = D(state["peak"]) - nav
        state["max_dd"] = str(max(D(state["max_dd"]), dd))
        state["last_equity"] = str(nav)
        day = datetime.fromtimestamp(book.source_ms / 1000, timezone.utc).date().isoformat()
        if day != state["daily_date"]:
            state["daily_date"], state["day_start_equity"] = day, str(nav)
            state["entry_pause_day"] = None
        try:
            metrics = indicators(event, book)
            market_reasons = entry_blocks(book, metrics, cfg, rules)
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            market_reasons = [str(exc)]
        if state["status"] == "STOPPED":
            reason.append("PERSISTENT_HARD_STOP")
        elif nav <= cfg["absolute_equity_floor_eur"] or dd >= cfg["hard_drawdown_eur"]:
            state["status"] = "STOPPED"
            fills += _liquidate(state, book, cfg, event_id)
            state["last_equity"] = str(D(state["cash"]))
            state["max_dd"] = str(max(D(state["max_dd"]),
                                    D(state["peak"]) - D(state["cash"])))
            state["soft_since_ms"] = None
            reason.append("HARD_STOP")
            action = "HARD_STOP"
        else:
            if (D(state["day_start_equity"]) - nav >= cfg["daily_loss_eur"]
                    and state["entry_pause_day"] is None):
                state["entry_pause_day"] = day
            if dd >= cfg["soft_drawdown_eur"] and state["soft_since_ms"] is None:
                state["soft_since_ms"] = book.observed_ms
                state["soft_good_since_ms"] = None
            if state["soft_since_ms"] is not None:
                if market_reasons or dd > cfg["soft_recovery_drawdown_eur"] or gap:
                    state["soft_good_since_ms"] = None
                else:
                    if state["soft_good_since_ms"] is None:
                        state["soft_good_since_ms"] = book.observed_ms
                    if (book.observed_ms - state["soft_good_since_ms"] >=
                            cfg["soft_recovery_seconds"] * 1000):
                        state["soft_since_ms"] = None
                        state["soft_good_since_ms"] = None
            if state["soft_since_ms"] is not None:
                state["status"] = "PAUSED_ENTRIES"
                reason.append("SOFT_DRAWDOWN_PAUSE")
            elif state["entry_pause_day"] == day:
                state["status"] = "PAUSED_ENTRIES"
                reason.append("DAILY_LOSS_PAUSE")
            elif not market_reasons:
                state["status"] = "RUNNING"
            else:
                state["status"] = "READY" if state["anchor"] is None else "RUNNING"
            if market_reasons:
                reason += market_reasons
            # Anchor may be initialized only during an entry-eligible observation.
            entries_allowed = (state["status"] == "RUNNING" and
                               state["soft_since_ms"] is None and
                               state["entry_pause_day"] != day and
                               not market_reasons and not gap)
            if state["anchor"] is None and entries_allowed:
                state["anchor"] = str(quant(book.mid, rules.quote_step))
                state["anchor_ms"] = book.source_ms
                state["lots"] = {
                    str(i): {
                        "status": "ARMED_BUY", "cycle": 0, "qty": "0", "cost": "0",
                        "buy_trigger_ms": None, "sell_trigger_ms": None,
                        "closed_on_ms": None,
                    }
                    for i in range(1, cfg["grid_levels"] + 1)
                }
                action = "ANCHOR_SET"
            # An anchor is not moved while a single lot remains open.
            if (entries_allowed and state["anchor"] is not None
                    and state["anchor_ms"] is not None
                    and book.source_ms - state["anchor_ms"] >= cfg["reanchor_seconds"] * 1000
                    and all(lot["status"] != "OPEN_LOT" for lot in state["lots"].values())
                    and abs(book.mid / D(state["anchor"]) - 1) >= cfg["reanchor_move"]):
                state["anchor"] = str(quant(book.mid, rules.quote_step))
                state["anchor_ms"] = book.source_ms
                state["reanchors"] += 1
                for lot in state["lots"].values():
                    lot.update(status="ARMED_BUY", buy_trigger_ms=None,
                               sell_trigger_ms=None, closed_on_ms=None)
                action = "REANCHOR"
            if state["anchor"] is not None and state["status"] != "STOPPED":
                grid = levels(D(state["anchor"]), cfg, rules)
                # SELL owned lots first; filters block entries but not exit of owned stock.
                for i, (_, target) in enumerate(grid, 1):
                    lot = state["lots"][str(i)]
                    if lot["status"] != "OPEN_LOT":
                        continue
                    eligible = rules.active() and book.bid >= target * (1 + cfg["slippage_rate"])
                    if eligible and not gap:
                        prior = lot["sell_trigger_ms"]
                        if prior is None:
                            lot["sell_trigger_ms"] = book.source_ms
                        elif book.source_ms - prior >= cfg["poll_seconds"] * 1000:
                            qty = D(lot["qty"])
                            price, proceeds, fee = sell_math(qty, target, cfg)
                            fills.append(_fill(state, lot, index=i, side="SELL", qty=qty,
                                               price=price, fee=fee, cash_change=proceeds,
                                               event_id=event_id, source_ms=book.source_ms))
                            state["realized_pnl"] = str(D(state["realized_pnl"]) + proceeds - D(lot["cost"]))
                            state["closed_cycles"] += 1
                            lot.update(status="CLOSED", qty="0", cost="0",
                                       sell_trigger_ms=None, buy_trigger_ms=None,
                                       closed_on_ms=book.source_ms)
                    else:
                        lot["sell_trigger_ms"] = None
                # Update NAV/risk before allowing any fresh buy after a sale.
                nav = portfolio_equity(state, book.bid, cfg)
                if nav <= cfg["absolute_equity_floor_eur"] or D(state["peak"]) - nav >= cfg["hard_drawdown_eur"]:
                    state["status"] = "STOPPED"
                    fills += _liquidate(state, book, cfg, event_id)
                    reason.append("HARD_STOP_POST_EXIT")
                    action = "HARD_STOP"
                elif entries_allowed:
                    for i, (buy, _) in enumerate(grid, 1):
                        lot = state["lots"][str(i)]
                        if lot["status"] == "CLOSED":
                            if lot["closed_on_ms"] != book.source_ms:
                                lot.update(status="ARMED_BUY", cycle=lot["cycle"] + 1,
                                           buy_trigger_ms=None, closed_on_ms=None)
                            else:
                                continue
                        if lot["status"] != "ARMED_BUY":
                            continue
                        eligible = book.ask <= buy * (1 - cfg["slippage_rate"])
                        if not eligible:
                            lot["buy_trigger_ms"] = None
                            continue
                        prior = lot["buy_trigger_ms"]
                        if prior is None:
                            lot["buy_trigger_ms"] = book.source_ms
                            continue
                        if book.source_ms - prior < cfg["poll_seconds"] * 1000:
                            continue
                        qty, total, fee = buy_math(buy, cfg, rules)
                        cost_basis = sum((D(x["cost"]) for x in state["lots"].values()
                                         if x["status"] == "OPEN_LOT"), D(0))
                        if (qty <= 0 or (rules.min_base_qty > 0 and qty < rules.min_base_qty)
                                or (rules.max_base_qty > 0 and qty > rules.max_base_qty)
                                or total < rules.min_notional
                                or total > rules.max_notional or total > D(state["cash"])
                                or cost_basis + total > cfg["max_cost_basis_eur"]):
                            state["rejected_signals"] += 1
                            reason.append(f"LOT_{i}_CAP_OR_PAIR_RULE")
                            lot["buy_trigger_ms"] = None
                            continue
                        fills.append(_fill(state, lot, index=i, side="BUY", qty=qty,
                                           price=buy * (1 + cfg["slippage_rate"]),
                                           fee=fee, cash_change=-total,
                                           event_id=event_id, source_ms=book.source_ms))
                        lot.update(status="OPEN_LOT", qty=str(qty), cost=str(total),
                                   buy_trigger_ms=None, sell_trigger_ms=None)
                        # Risk guard executes again after virtual fee/slippage.
                        post_nav = portfolio_equity(state, book.bid, cfg)
                        if (post_nav <= cfg["absolute_equity_floor_eur"]
                                or D(state["peak"]) - post_nav >= cfg["hard_drawdown_eur"]):
                            state["status"] = "STOPPED"
                            fills += _liquidate(state, book, cfg, event_id)
                            reason.append("HARD_STOP_POST_FILL")
                            action = "HARD_STOP"
                            break
                else:
                    for lot in state["lots"].values():
                        lot["buy_trigger_ms"] = None
                if fills and action != "HARD_STOP":
                    action = "SYNTHETIC_FILL"
            state["last_equity"] = str(portfolio_equity(state, book.bid, cfg))
            state["max_dd"] = str(max(D(state["max_dd"]),
                                    D(state["peak"]) - D(state["last_equity"])))
    _assert_invariants(state, cfg)
    decision = {
        "event_id": event_id, "source_ts_utc_ms": None if book is None else book.source_ms,
        "observed_at_utc_ms": event.get("observed_ms"),
        "action": action, "reasons": reason, "fills": fills,
        "status": state["status"], "bid": None if book is None else str(book.bid),
        "ask": None if book is None else str(book.ask),
        "cash_eur": state["cash"], "eth_balance": state["eth"],
        "equity_eur": state["last_equity"],
        "config_hash": cfg.sha256, "mode": "shadow",
    }
    return state, decision
