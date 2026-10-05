"""Trend-pullback strategy with a price-action trigger.

Evidence basis:
- Direction comes from time-series momentum / trend-following, the most robustly
  documented FX technical effect (Moskowitz, Ooi & Pedersen 2012; Menkhoff et al. 2012).
- Entries wait for a pullback into the 20 EMA "value zone" and a price-action
  confirmation (close beyond the prior bar's extreme in the trend direction), so the
  stop can sit just beyond the pullback swing instead of far away.
- Exits: fixed R-multiple target, stop beyond the swing. Every rule is mechanical so it
  can be backtested (see backtest.py) and reproduced live.

All signals are computed on CLOSED bars only.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Params:
    sma_fast: int = 50
    sma_slow: int = 200
    momentum_lookback: int = 60
    ema_value: int = 20
    pullback_window: int = 3
    atr_len: int = 14
    stop_buffer_atr: float = 0.25
    min_stop_atr: float = 1.0
    max_stop_atr: float = 3.0
    target_r: float = 2.0
    entry: str = "pullback"   # "pullback" (EMA pullback + price action) or "breakout" (Donchian)
    breakout_len: int = 55
    exit: str = "target"      # "target" (fixed R) or "trail" (chandelier stop, no target)
    trail_atr: float = 3.0


def indicators(df: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    d = df.copy()
    d["sma_fast"] = d["close"].rolling(p.sma_fast).mean()
    d["sma_slow"] = d["close"].rolling(p.sma_slow).mean()
    d["ema"] = d["close"].ewm(span=p.ema_value, adjust=False).mean()
    d["mom"] = d["close"] / d["close"].shift(p.momentum_lookback) - 1
    prev_close = d["close"].shift()
    tr = pd.concat(
        [d["high"] - d["low"], (d["high"] - prev_close).abs(), (d["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / p.atr_len, adjust=False).mean()

    up = (d["close"] > d["sma_slow"]) & (d["sma_fast"] > d["sma_slow"]) & (d["mom"] > 0)
    dn = (d["close"] < d["sma_slow"]) & (d["sma_fast"] < d["sma_slow"]) & (d["mom"] < 0)
    d["trend"] = np.where(up, 1, np.where(dn, -1, 0))

    w = p.pullback_window
    touched_long = (d["low"] <= d["ema"]).rolling(w).max().astype(bool)
    touched_short = (d["high"] >= d["ema"]).rolling(w).max().astype(bool)
    # Pullback must not have broken structure (stays on the right side of the slow trend).
    held_long = d["low"].rolling(w).min() > d["sma_fast"] - 0.5 * d["atr"]
    held_short = d["high"].rolling(w).max() < d["sma_fast"] + 0.5 * d["atr"]

    trig_long = (d["close"] > d["high"].shift()) & (d["close"] > d["open"]) & (d["close"] > d["ema"])
    trig_short = (d["close"] < d["low"].shift()) & (d["close"] < d["open"]) & (d["close"] < d["ema"])

    if p.entry == "breakout":
        long_ok = d["close"] > d["high"].shift().rolling(p.breakout_len).max()
        short_ok = d["close"] < d["low"].shift().rolling(p.breakout_len).min()
    else:
        long_ok = touched_long & held_long & trig_long
        short_ok = touched_short & held_short & trig_short
    d["signal"] = np.where(
        (d["trend"] == 1) & long_ok, 1, np.where((d["trend"] == -1) & short_ok, -1, 0)
    )
    swing_low = d["low"].rolling(w + 1).min()
    swing_high = d["high"].rolling(w + 1).max()
    d["stop_long"] = swing_low - p.stop_buffer_atr * d["atr"]
    d["stop_short"] = swing_high + p.stop_buffer_atr * d["atr"]
    return d


def stop_and_target(
    direction: int, entry: float, row: pd.Series, p: Params
) -> tuple[float, float | None]:
    """Stop beyond the pullback swing, clamped to [min, max] ATR; target at target_r."""
    atr = row["atr"]
    raw = row["stop_long"] if direction == 1 else row["stop_short"]
    dist = abs(entry - raw)
    dist = min(max(dist, p.min_stop_atr * atr), p.max_stop_atr * atr)
    stop = entry - direction * dist
    if p.exit == "trail":
        return stop, None
    target = entry + direction * p.target_r * dist
    return stop, target


def describe(d: pd.DataFrame, pair: str, p: Params = Params()) -> dict:
    """Snapshot of the latest closed bar for live decision-making."""
    row = d.iloc[-1]
    out = {
        "pair": pair,
        "bar_time": str(d.index[-1]),
        "close": float(row["close"]),
        "trend": int(row["trend"]),
        "ema20": float(row["ema"]),
        "sma50": float(row["sma_fast"]),
        "sma200": float(row["sma_slow"]),
        "momentum_60": round(float(row["mom"]), 4),
        "atr": float(row["atr"]),
        "signal": int(row["signal"]),
        "distance_to_ema_atr": round(float((row["close"] - row["ema"]) / row["atr"]), 2),
    }
    if row["signal"] != 0:
        stop, target = stop_and_target(int(row["signal"]), float(row["close"]), row, p)
        out.update(
            {
                "side": "buy" if row["signal"] == 1 else "sell",
                "ref_entry": float(row["close"]),
                "stop": float(stop),
                "target": None if target is None else float(target),
            }
        )
    return out


def params_dict(p: Params = Params()) -> dict:
    return asdict(p)
