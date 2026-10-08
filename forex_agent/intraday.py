"""Shorter-horizon strategy research on Dukascopy hourly data (see dukascopy.py).

Each strategy turns hourly bars into candidate trades (entry, side, stop, optional target,
deadline). `simulate` walks every trade bar by bar with costs and returns its result in
R (multiples of the initial stop distance) plus its worst floating R per trading day,
which challenge.py needs for FundedNext's daily-loss rule.

Rules were fixed before looking at results. Train 2010-2018, test 2019 onward.

    python -m forex_agent.intraday            # trade stats for every strategy
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from . import dukascopy

SYMBOLS = dukascopy.SYMBOLS
# Conservative all-in costs: typical retail spread (price units) + commission per side.
SPREAD = {"EURUSD": 1.0e-4, "GBPUSD": 1.5e-4, "USDJPY": 1.2e-2, "AUDUSD": 1.3e-4,
          "USDCAD": 1.8e-4, "USDCHF": 1.7e-4, "NZDUSD": 1.8e-4, "EURJPY": 2.0e-2,
          "GBPJPY": 3.0e-2, "EURGBP": 1.5e-4, "XAUUSD": 0.30}
COMMISSION_PER_LOT = float(os.environ.get("FN_COMMISSION", "7"))  # USD round turn, worst case
SWAP_BP_PER_NIGHT = 0.3   # assumed net cost of holding overnight (both directions)
DAY_SHIFT_H = 3           # FundedNext server time is GMT+2/+3; day rolls at 21:00 UTC
TRAIN_END = "2019-01-01"


def lot_notional_usd(sym: str, px: float) -> float:
    if sym == "XAUUSD":
        return 100 * px
    base = sym[:3]
    if base == "USD":
        return 100_000
    return 100_000 * (px if sym.endswith("USD") else 1.3)   # crosses: rough EUR/GBP in USD


def cost_price(sym: str, px: float) -> float:
    """Round-trip cost in price units: spread plus commission."""
    comm_frac = COMMISSION_PER_LOT / lot_notional_usd(sym, px)
    return SPREAD[sym] + comm_frac * px


def load_all() -> dict[str, pd.DataFrame]:
    out = {}
    for s in SYMBOLS:
        if os.path.exists(dukascopy.path(s)):
            df = dukascopy.load(s)
            out[s] = df[~df.index.duplicated()].sort_index()
    return out


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n).mean()


def day_of(ts: pd.DatetimeIndex) -> pd.Index:
    """FundedNext trading day for each timestamp (rolls over at 21:00 UTC)."""
    return (ts + pd.Timedelta(hours=DAY_SHIFT_H)).normalize()


# ---------------------------------------------------------------- strategies
# Each returns a DataFrame: sym, side (+1/-1), entry_time (bar whose OPEN fills the trade),
# entry (price, or NaN = bar open), stop, target (NaN = none), deadline (exit at that bar's open).

HOME = {"EUR": "eu", "GBP": "eu", "CHF": "eu", "USD": "us", "CAD": "us",
        "JPY": "asia", "AUD": "asia", "NZD": "asia"}
SESSIONS = {"asia": (0, 7), "eu": (7, 12), "us": (13, 20)}


def seasonality(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Breedon & Ranaldo (2013): a currency tends to weaken during its home market hours.
    Sell the base (buy the quote) in the base's session, and vice versa."""
    rows = []
    for sym, df in data.items():
        if sym == "XAUUSD":
            continue
        b, q = HOME[sym[:3]], HOME[sym[3:]]
        a = atr(df, 24 * 5)
        for sess, (h0, h1) in SESSIONS.items():
            if b == q or sess not in (b, q):
                continue
            side = -1 if sess == b else +1
            starts = df.index[(df.index.hour == h0) & (df.index.dayofweek < 5)]
            for t in starts:
                end = t + pd.Timedelta(hours=h1 - h0)
                if end not in df.index or np.isnan(a.get(t, np.nan)):
                    continue
                px = df.at[t, "open"]
                stop = px - side * 3 * a[t]
                rows.append((sym, side, t, np.nan, stop, np.nan, end))
    return pd.DataFrame(rows, columns=["sym", "side", "entry_time", "entry", "stop", "target",
                                       "deadline"])


def london_breakout(data: dict[str, pd.DataFrame], target_r: float | None) -> pd.DataFrame:
    """Asian range (00-07 UTC) breakout, entries 07-12 UTC, stop at the other side of the
    range, exit at target or 20:00 UTC."""
    rows = []
    for sym in ["EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "XAUUSD"]:
        if sym not in data:
            continue
        df = data[sym]
        a = atr(df, 24 * 5) * 24 ** 0.5           # ~daily ATR from hourly
        days = df.index.normalize().unique()
        for d in days:
            if d.dayofweek >= 5:
                continue
            asia = df.loc[d: d + pd.Timedelta(hours=6, minutes=59)]
            sess = df.loc[d + pd.Timedelta(hours=7): d + pd.Timedelta(hours=11, minutes=59)]
            if len(asia) < 6 or len(sess) < 4:
                continue
            hi, lo = asia["high"].max(), asia["low"].min()
            rng = hi - lo
            at = a.get(asia.index[-1], np.nan)
            if not (0.2 * at <= rng <= 1.0 * at):
                continue
            for t, bar in sess.iterrows():
                side = +1 if bar["high"] > hi else -1 if bar["low"] < lo else 0
                if side and bar["high"] > hi and bar["low"] < lo:
                    side = 0                          # both sides in one bar: ambiguous, skip day
                    break
                if side:
                    entry = max(hi, bar["open"]) if side > 0 else min(lo, bar["open"])
                    stop = lo if side > 0 else hi
                    tgt = entry + side * target_r * rng if target_r else np.nan
                    rows.append((sym, side, t, entry, stop, tgt, d + pd.Timedelta(hours=20)))
                    break
    return pd.DataFrame(rows, columns=["sym", "side", "entry_time", "entry", "stop", "target",
                                       "deadline"])


def trend_4h(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Classic Donchian trend following on 4-hour bars: enter on a 55-bar breakout,
    initial stop 2 ATR, exit on a 20-bar opposite breakout (deadline found in simulate)."""
    rows = []
    for sym, h in data.items():
        df = h.resample("4h", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        a = atr(df, 20)
        up55, dn55 = df["high"].rolling(55).max().shift(), df["low"].rolling(55).min().shift()
        up20, dn20 = df["high"].rolling(20).max().shift(), df["low"].rolling(20).min().shift()
        pos, exit_at = 0, None
        idx = df.index
        for i in range(60, len(df) - 1):
            if pos:
                if (pos > 0 and df["low"].iat[i] < dn20.iat[i]) or (
                        pos < 0 and df["high"].iat[i] > up20.iat[i]):
                    rows[-1][-1] = idx[i + 1]          # exit next 4h bar open
                    pos = 0
                continue
            side = +1 if df["close"].iat[i] > up55.iat[i] else -1 if df["close"].iat[i] < dn55.iat[i] else 0
            if side:
                px = df["close"].iat[i]
                rows.append([sym, side, idx[i + 1], np.nan, px - side * 2 * a.iat[i], np.nan,
                             idx[-1]])
                pos = side
    return pd.DataFrame(rows, columns=["sym", "side", "entry_time", "entry", "stop", "target",
                                       "deadline"])


def randomized(trades: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """Same entries, stops and holding periods with a coin-flip direction: the no-edge
    baseline that still pays every cost."""
    rng = np.random.default_rng(seed)
    t = trades.copy()
    t["flip"] = rng.choice([-1, 1], len(t))
    return t


# ---------------------------------------------------------------- simulation

def simulate(trades: pd.DataFrame, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Walk each trade through hourly bars. Stop before target when both are hit in one bar.
    Returns trades with exit_time, r (net of costs) and daily: {day: [worst R, end R]}."""
    out = []
    for sym, grp in trades.groupby("sym"):
        df = data[sym]
        idx = df.index
        o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        days = day_of(idx)
        for tr in grp.itertuples(index=False):
            i0 = idx.searchsorted(tr.entry_time)
            i1 = min(idx.searchsorted(tr.deadline), len(idx) - 1)
            if i0 >= len(idx) or i1 <= i0:
                continue
            entry = o[i0] if np.isnan(tr.entry) else tr.entry
            dist = tr.side * (entry - tr.stop)
            if not dist > 0:
                continue
            side = tr.side * getattr(tr, "flip", 1)
            stop = entry - side * dist
            target = entry + side * abs(tr.target - entry) if not np.isnan(tr.target) else np.nan
            risk = dist
            cost = cost_price(sym, entry)
            exit_px, exit_i = o[i1], i1
            # Per FundedNext day: [worst floating R, R at the day's last bar]. Costs are
            # charged up front so floating P&L includes the spread, as on the platform.
            daily: dict = {}
            c0 = cost / risk
            for i in range(i0, i1):
                adverse = l[i] if side > 0 else h[i]
                favour = h[i] if side > 0 else l[i]
                hit_stop = side * (adverse - stop) <= 0
                hit_tgt = not np.isnan(target) and side * (favour - target) >= 0
                w = max(side * (adverse - entry) / risk, -1.0) - c0
                d = days[i]
                cur = daily.setdefault(d, [0.0, 0.0])
                cur[0] = min(cur[0], w)
                cur[1] = side * (c[i] - entry) / risk - c0
                if hit_stop:
                    exit_px, exit_i = stop, i
                    break
                if hit_tgt:
                    exit_px, exit_i = target, i
                    break
            nights = max((days[exit_i] - days[i0]).days, 0)
            swap = SWAP_BP_PER_NIGHT * 1e-4 * entry * nights
            r = (side * (exit_px - entry) - cost - swap) / risk
            d_exit = days[exit_i]
            cur = daily.setdefault(d_exit, [min(0.0, r), r])
            cur[0], cur[1] = min(cur[0], r), r
            out.append((sym, side, idx[i0], idx[exit_i], r, risk / entry, daily))
    res = pd.DataFrame(out, columns=["sym", "side", "entry_time", "exit_time", "r", "risk_frac",
                                     "daily"])
    return res.sort_values("entry_time").reset_index(drop=True)


def stats(t: pd.DataFrame) -> dict:
    if not len(t):
        return {"n": 0}
    r = t["r"]
    yrs = (t["exit_time"].max() - t["entry_time"].min()).days / 365.25
    return {"n": len(t), "per_year": round(len(t) / max(yrs, 1e-9)), "avg_r": round(r.mean(), 3),
            "win": round((r > 0).mean(), 3),
            "pf": round(r[r > 0].sum() / -r[r < 0].sum(), 2) if (r < 0).any() else None,
            "t_stat": round(r.mean() / r.std(ddof=1) * len(r) ** 0.5, 2) if len(r) > 1 else None}


def split_stats(t: pd.DataFrame) -> dict:
    tr = t[t["entry_time"] < TRAIN_END]
    te = t[t["entry_time"] >= TRAIN_END]
    return {"train": stats(tr), "test": stats(te)}


STRATEGIES = {
    "seasonality": seasonality,
    "london_breakout_time": lambda d: london_breakout(d, None),
    "london_breakout_2R": lambda d: london_breakout(d, 2.0),
    "trend_4h": trend_4h,
}


def main() -> None:
    import json
    data = load_all()
    print("symbols:", list(data))
    for name, fn in STRATEGIES.items():
        trades = fn(data)
        res = simulate(trades, data)
        rnd = simulate(randomized(trades), data)
        print(name, json.dumps({"strategy": split_stats(res), "random_baseline": split_stats(rnd)}),
              flush=True)


if __name__ == "__main__":
    main()
