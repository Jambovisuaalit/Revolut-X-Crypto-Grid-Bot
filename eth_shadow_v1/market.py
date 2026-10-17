"""Validated BBO depth and closed-candle volatility/trend filters; no lookahead."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone

from .config import Config, number
from .grid import PairRules

D = Decimal
FIFTEEN_MINUTES_MS = 900000


@dataclass(frozen=True)
class Book:
    source_ms: int
    observed_ms: int
    bid: Decimal
    ask: Decimal
    spread: Decimal
    bid_depth_eur: Decimal
    ask_depth_eur: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


def parse_book(event: dict, cfg: Config) -> Book:
    """Validate UNIX-ms UTC source, region, symbol, entire BBO and depth."""
    meta = event["metadata"]
    src, observed = meta["timestamp"], event["observed_ms"]
    if type(src) is not int or type(observed) is not int or src < 1000000000000:
        raise ValueError("DATA_TIMESTAMP_NOT_UNIX_MS")
    if observed - src < -1000 or observed - src > cfg["max_book_age_seconds"] * 1000:
        raise ValueError("DATA_STALE_OR_FUTURE")
    if meta.get("region") != "EEA" or event.get("symbol") != "ETH-EUR":
        raise ValueError("DATA_WRONG_REGION_OR_SYMBOL")
    sides = []
    for name in ("bids", "asks"):
        rows = event[name]
        if not isinstance(rows, list) or not rows:
            raise ValueError("DATA_EMPTY_BOOK")
        parsed = [(number(price, "price"), number(qty, "qty")) for price, qty in rows]
        if any(price <= 0 or qty <= 0 for price, qty in parsed):
            raise ValueError("DATA_BAD_LEVEL")
        if any(parsed[i][0] < parsed[i + 1][0] for i in range(len(parsed)-1)) if name == "bids" else any(parsed[i][0] > parsed[i + 1][0] for i in range(len(parsed)-1)):
            raise ValueError("DATA_UNSORTED_BOOK")
        sides.append(parsed)
    bid, ask = sides[0][0][0], sides[1][0][0]
    if bid >= ask:
        raise ValueError("DATA_CROSSED_BOOK")
    mid = (bid + ask) / 2
    lower = mid * (1 - cfg["depth_band_rate"])
    upper = mid * (1 + cfg["depth_band_rate"])
    bid_depth = sum((p * q for p,q in sides[0] if p >= lower), D("0"))
    ask_depth = sum((p * q for p,q in sides[1] if p <= upper), D("0"))
    return Book(src, observed, bid, ask, (ask - bid) / mid, bid_depth, ask_depth)


@dataclass(frozen=True)
class Indicators:
    adx14: Decimal
    ema_divergence: Decimal
    atr_rate: Decimal
    change_24h: Decimal
    zero_volume_share: Decimal


def _ema(prices: list[Decimal], span: int) -> Decimal:
    multiplier = D(2) / D(span + 1)
    value = prices[0]
    for price in prices[1:]:
        value = price * multiplier + value * (1 - multiplier)
    return value


def indicators(event: dict, book: Book) -> Indicators:
    """Indicators built ONLY from 200+ consecutive closed 15m candles."""
    candles = event["candles"]
    if not isinstance(candles, list) or len(candles) < 200:
        raise ValueError("CANDLES_INSUFFICIENT")
    candles = candles[-200:]
    closes, highs, lows, volumes = [], [], [], []
    previous = None
    for c in candles:
        stamp = c["close_ts_ms"]
        if type(stamp) is not int or c.get("closed") is not True:
            raise ValueError("CANDLE_UNCLOSED")
        if previous is not None and stamp - previous != FIFTEEN_MINUTES_MS:
            raise ValueError("CANDLE_NOT_CONTIGUOUS")
        if stamp > book.source_ms or book.source_ms - stamp > 200 * FIFTEEN_MINUTES_MS:
            raise ValueError("CANDLE_INVALID_TIME")
        previous = stamp
        high, low, close, volume = (number(c[k], k) for k in ("high", "low", "close", "volume"))
        if close <= 0 or low <= 0 or low > close or high < close or high < low or volume < 0:
            raise ValueError("CANDLE_INVALID_OHLCV")
        highs.append(high); lows.append(low); closes.append(close); volumes.append(volume)
    if book.source_ms - previous > 2 * FIFTEEN_MINUTES_MS:
        raise ValueError("CANDLE_TOO_OLD")
    tr, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else D(0))
        minus_dm.append(down if down > up and down > 0 else D(0))
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    period = 14
    atr = sum(tr[:period], D(0)) / period
    plus_smoothed = sum(plus_dm[:period], D(0)) / period
    minus_smoothed = sum(minus_dm[:period], D(0)) / period
    dx_series = []
    for i in range(period-1, len(tr)):
        if i >= period:
            atr = (atr * (period-1) + tr[i]) / period
            plus_smoothed = (plus_smoothed * (period-1) + plus_dm[i]) / period
            minus_smoothed = (minus_smoothed * (period-1) + minus_dm[i]) / period
        denominator = plus_smoothed + minus_smoothed
        dx_series.append(D(0) if denominator == 0 else abs(plus_smoothed - minus_smoothed) / denominator * 100)
    adx = sum(dx_series[:period], D(0)) / period
    for dx in dx_series[period:]:
        adx = (adx * (period-1) + dx) / period
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    return Indicators(
        adx14=adx,
        ema_divergence=abs(ema20 / ema50 - 1),
        atr_rate=atr / closes[-1],
        change_24h=abs(closes[-1] / closes[-97] - 1),
        zero_volume_share=D(sum(1 for v in volumes[-50:] if v == 0)) / D(50),
    )


def entry_blocks(book: Book, metrics: Indicators, cfg: Config, rules: PairRules) -> list[str]:
    """Every failed market filter is recorded and prevents a new synthetic buy."""
    failures = []
    if not rules.active(): failures.append("CONFIG_BLOCKED")
    if book.spread > cfg["max_spread_rate"]: failures.append("SPREAD_LIMIT")
    if min(book.bid_depth_eur, book.ask_depth_eur) < cfg["min_depth_eur_per_side"]:
        failures.append("DEPTH_LIMIT")
    if metrics.adx14 >= cfg["max_adx"]: failures.append("ADX_LIMIT")
    if metrics.ema_divergence > cfg["max_ema_divergence"]: failures.append("EMA_LIMIT")
    if not cfg["min_atr_rate"] <= metrics.atr_rate <= cfg["max_atr_rate"]:
        failures.append("ATR_LIMIT")
    if metrics.change_24h > cfg["max_24h_change"]: failures.append("CHANGE_24H_LIMIT")
    if metrics.zero_volume_share > D("0.25"): failures.append("ZERO_VOLUME_LIMIT")
    return failures
