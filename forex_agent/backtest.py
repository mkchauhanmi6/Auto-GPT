"""Backtest the strategy on daily data, in R-multiples, with spread costs.

Conservative fill assumptions:
- Signal on bar t's close, entry at bar t+1's open (+ spread cost).
- If stop and target are both inside one bar, the stop is assumed hit first.
- If a bar gaps through the stop, the fill is at the open (worse than the stop).
- Swap/rollover is ignored.

Usage: python -m forex_agent.backtest [--split 2019-01-01] [--target-r 2.0]
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .config import PAIRS, spread_price
from .data import yahoo_candles
from .strategy import Params, indicators, stop_and_target


def run_pair(df: pd.DataFrame, pair: str, p: Params, mode: str = "strategy") -> pd.DataFrame:
    """mode='strategy' uses the full rules; mode='trend_only' enters on any trend bar
    (baseline that shows whether pullback + price action adds value)."""
    d = indicators(df, p)
    cost = spread_price(pair)
    sig_col = d["signal"] if mode == "strategy" else d["trend"]
    trades = []
    i, n = p.sma_slow + 1, len(d)
    while i < n - 1:
        direction = int(sig_col.iloc[i])
        if direction == 0:
            i += 1
            continue
        row = d.iloc[i]
        entry = d["open"].iloc[i + 1]
        stop, target = stop_and_target(direction, entry, row, p)
        risk = abs(entry - stop)
        exit_px, exit_i = None, None
        for j in range(i + 1, n):
            o, h, l, c = (d[k].iloc[j] for k in ("open", "high", "low", "close"))
            if direction == 1:
                if o <= stop:
                    exit_px = o
                elif l <= stop:
                    exit_px = stop
                elif target is not None and h >= target:
                    exit_px = max(o, target)
            else:
                if o >= stop:
                    exit_px = o
                elif h >= stop:
                    exit_px = stop
                elif target is not None and l <= target:
                    exit_px = min(o, target)
            if exit_px is not None:
                exit_i = j
                break
            if p.exit == "trail":  # chandelier stop, ratchets at each close
                trail = c - direction * p.trail_atr * d["atr"].iloc[j]
                stop = max(stop, trail) if direction == 1 else min(stop, trail)
        if exit_px is None:
            break
        r = (direction * (exit_px - entry) - cost) / risk
        trades.append(
            {
                "pair": pair,
                "entry_time": d.index[i + 1],
                "exit_time": d.index[exit_i],
                "dir": direction,
                "r": r,
                "bars": exit_i - i,
            }
        )
        i = exit_i + 1
    return pd.DataFrame(trades)


def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    t = t.sort_values("exit_time")
    eq = t["r"].cumsum()
    dd = (eq - eq.cummax()).min()
    wins = t.loc[t["r"] > 0, "r"].sum()
    losses = -t.loc[t["r"] <= 0, "r"].sum()
    years = max((t["exit_time"].max() - t["entry_time"].min()).days / 365.25, 1e-9)
    # Worst losing streak.
    streak = worst = 0
    for r in t["r"]:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    return {
        "trades": len(t),
        "win_rate": round((t["r"] > 0).mean(), 3),
        "avg_R": round(t["r"].mean(), 3),
        "profit_factor": round(wins / losses, 2) if losses else np.inf,
        "total_R": round(t["r"].sum(), 1),
        "R_per_year": round(t["r"].sum() / years, 1),
        "max_drawdown_R": round(dd, 1),
        "worst_losing_streak": worst,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2019-01-01", help="out-of-sample start date")
    ap.add_argument("--target-r", type=float, default=2.0)
    ap.add_argument("--entry", default="pullback", choices=["pullback", "breakout"])
    ap.add_argument("--exit", default="target", choices=["target", "trail"])
    ap.add_argument("--baseline", action="store_true", help="also run trend-only baseline")
    ap.add_argument("--pairs", default=",".join(PAIRS))
    args = ap.parse_args()
    p = Params(target_r=args.target_r, entry=args.entry, exit=args.exit)
    split = pd.Timestamp(args.split, tz="UTC")

    data = {pair: yahoo_candles(pair) for pair in args.pairs.split(",")}
    for mode in ("strategy", "trend_only") if args.baseline else ("strategy",):
        all_t = pd.concat([run_pair(df, pair, p, mode) for pair, df in data.items()])
        print(f"\n=== {mode}: entry={p.entry} exit={p.exit} target={p.target_r}R ===")
        print("in-sample   :", stats(all_t[all_t["entry_time"] < split]))
        print("out-of-sample:", stats(all_t[all_t["entry_time"] >= split]))
        if mode == "strategy":
            per_pair = {k: stats(g)["avg_R"] for k, g in all_t.groupby("pair")}
            print("avg R per pair (full):", per_pair)


if __name__ == "__main__":
    main()
