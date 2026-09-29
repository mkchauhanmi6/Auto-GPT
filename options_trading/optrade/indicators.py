"""Technical indicators on plain lists of floats (no numpy/pandas needed)."""

from __future__ import annotations

import math
from typing import Sequence

from .models import Candle


def ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[float]:
    """Wilder's RSI. First ``period`` values are filled with 50."""
    if len(values) <= period:
        return [50.0] * len(values)
    gains, losses = [], []
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_g, avg_l = sum(gains) / period, sum(losses) / period
    out = [50.0] * (period + 1)
    out[-1] = _rsi_from(avg_g, avg_l)
    for i in range(period + 1, len(values)):
        ch = values[i] - values[i - 1]
        avg_g = (avg_g * (period - 1) + max(ch, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-ch, 0.0)) / period
        out.append(_rsi_from(avg_g, avg_l))
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles: Sequence[Candle], period: int = 14) -> list[float]:
    if not candles:
        return []
    trs = [candles[0].high - candles[0].low]
    for prev, c in zip(candles, candles[1:]):
        trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    out = [trs[0]]
    for tr in trs[1:]:
        out.append((out[-1] * (period - 1) + tr) / period)
    return out


def vwap(candles: Sequence[Candle]) -> float | None:
    """Session VWAP over the candles given (None if there is no volume data)."""
    vol = sum(c.volume for c in candles)
    if vol <= 0:
        return None
    return sum((c.high + c.low + c.close) / 3 * c.volume for c in candles) / vol


def realized_vol(closes: Sequence[float], periods_per_year: float) -> float:
    """Annualised close-to-close volatility."""
    if len(closes) < 3:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * periods_per_year)
