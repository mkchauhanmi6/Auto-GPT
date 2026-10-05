"""Monthly currency-portfolio factors exactly as studied in the academic literature.

- Carry (Lustig & Verdelhan; Menkhoff et al. 2012): long the 3 highest-yielding of 8
  G10 currencies, short the 3 lowest, by central-bank policy rate.
- Time-series momentum (Moskowitz, Ooi & Pedersen 2012): for each currency vs USD, hold
  the sign of its trailing 12-month excess return.
- Combo: equal blend of the two.

Returns include the interest differential (excess returns), minus a cost per unit of
turnover. Rebalanced monthly on information known at the prior month end.

Usage: python -m forex_agent.portfolio_backtest
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import policy_rates, yahoo_candles

VS_USD = {"EUR": ("EURUSD", 1), "GBP": ("GBPUSD", 1), "AUD": ("AUDUSD", 1),
          "NZD": ("NZDUSD", 1), "JPY": ("USDJPY", -1), "CAD": ("USDCAD", -1),
          "CHF": ("USDCHF", -1)}
COST_PER_TURNOVER = 0.0003  # ~3 pips round trip per unit notional traded


def monthly_excess_returns() -> tuple[pd.DataFrame, pd.DataFrame]:
    rates = policy_rates("2004-01")
    px = {}
    for ccy, (pair, sign) in VS_USD.items():
        c = yahoo_candles(pair)["close"].resample("ME").last()
        px[ccy] = c if sign == 1 else 1 / c
    px = pd.DataFrame(px)
    spot_ret = px.pct_change()
    r = rates.copy()
    r.index = r.index + pd.offsets.MonthEnd(0)
    r = r.reindex(px.index).ffill()
    carry = (r[list(VS_USD)].sub(r["USD"], axis=0)) / 100 / 12
    excess = spot_ret + carry.shift(1)  # interest earned over the month held
    return excess.dropna(how="all"), r


def perf(ret: pd.Series) -> dict:
    ret = ret.dropna()
    ann = ret.mean() * 12
    vol = ret.std() * np.sqrt(12)
    eq = (1 + ret).cumprod()
    return {"ann_ret_%": round(100 * ann, 1), "vol_%": round(100 * vol, 1),
            "sharpe": round(ann / vol, 2) if vol else np.nan,
            "max_dd_%": round(100 * (eq / eq.cummax() - 1).min(), 1)}


def main() -> None:
    ex, rates = monthly_excess_returns()
    # Carry ranks include USD itself (excess return 0 by definition).
    diff = rates[list(VS_USD) + ["USD"]]
    ranks = diff.rank(axis=1)
    w_carry = ((ranks >= 6).astype(float) - (ranks <= 3).astype(float))[list(VS_USD)] / 3
    mom = (1 + ex).rolling(12).apply(np.prod, raw=True) - 1
    w_mom = np.sign(mom) / len(VS_USD)
    out = {}
    for name, w in {"carry": w_carry, "tsmom_12m": w_mom, "combo": (w_carry + w_mom) / 2}.items():
        w = w.shift(1)  # decide at prior month end
        gross = (w * ex).sum(axis=1, min_count=1)
        cost = w.diff().abs().sum(axis=1) * COST_PER_TURNOVER
        out[name] = (gross - cost).loc["2006-01-01":]
    for name, ret in out.items():
        print(f"{name:10s} 2006-18 {perf(ret[:'2018-12-31'])}  2019-26 {perf(ret['2019-01-01':])}")
        yearly = ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)
        print("           by year %:", {y: round(100 * v, 1) for y, v in yearly.items()})




def carry_weights(rates_row: pd.Series) -> dict[str, float]:
    """Live/backtest carry ranking: +1 for the 3 highest yielders, -1 for the 3 lowest,
    among the 8 currencies (USD included). Returns weights for the 7 non-USD currencies."""
    r = rates_row[list(VS_USD) + ["USD"]].dropna()
    order = r.sort_values(kind="mergesort")
    longs, shorts = set(order.index[-3:]), set(order.index[:3])
    return {c: (1.0 if c in longs else -1.0 if c in shorts else 0.0) for c in VS_USD}


def daily_carry_with_stops(stop_atr: float = 4.0, start: str = "2006-01-01") -> pd.Series:
    """Daily simulation of the carry portfolio with a hard stop on every leg.

    Each month-start, legs are opened per carry_weights (rates known at prior month end).
    A leg is closed if price moves stop_atr x ATR(14) against it, and stays flat until
    the next rebalance. Each leg risks the same amount (1 unit) at its stop, so returns
    are in units of per-leg risk; reported as % of equity assuming 1% risk per leg.
    """
    rates = policy_rates("2004-01")
    r_m = rates.copy()
    r_m.index = r_m.index + pd.offsets.MonthEnd(0)
    daily = {}
    for ccy, (pair, _) in VS_USD.items():
        d = yahoo_candles(pair)
        tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(),
                        (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
        d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
        daily[ccy] = d
    idx = daily["EUR"].index
    idx = idx[idx >= pd.Timestamp(start, tz="UTC")]
    pnl: dict = {}
    months = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).first()
    for m_start in months:
        prev = r_m[r_m.index < m_start]
        if prev.empty:
            continue
        w = carry_weights(prev.iloc[-1])
        m_end = m_start + pd.offsets.MonthBegin(1)
        for ccy, wt in w.items():
            if wt == 0:
                continue
            pair, sign = VS_USD[ccy]
            direction = int(wt * sign)  # +1 buy pair, -1 sell pair
            d = daily[ccy]
            seg = d[(d.index >= m_start) & (d.index < m_end)]
            before = d[d.index < m_start]
            if seg.empty or before.empty:
                continue
            entry, atr = before["close"].iloc[-1], before["atr"].iloc[-1]
            risk = stop_atr * atr
            stop = entry - direction * risk
            diff_pct = (prev.iloc[-1][pair[:3]] - prev.iloc[-1][pair[3:]])
            swap_per_day = entry * (direction * diff_pct - 1.0) / 100 / 365  # 1% broker markup
            last = entry - direction * 0.00015 * entry  # pay ~1.5 pip spread on entry
            for t, bar in seg.iterrows():
                hit = bar.low <= stop if direction == 1 else bar.high >= stop
                px = stop if hit else bar.close
                if hit and ((direction == 1 and bar.open < stop) or (direction == -1 and bar.open > stop)):
                    px = bar.open
                day = (t + pd.Timedelta(hours=2)).normalize()  # bars stamp 22:00/23:00 UTC
                pnl[day] = pnl.get(day, 0.0) + (direction * (px - last) + swap_per_day) / risk
                last = px
                if hit:
                    break
    return pd.Series(pnl).sort_index() / 100  # 1 unit of risk = 1% of equity


def main_stops() -> None:
    for k in (3.0, 4.0, 6.0):
        ret = daily_carry_with_stops(k).resample("ME").sum()
        print(f"carry+stop {k}ATR 2006-18 {perf(ret[:'2018-12-31'])}  2019-26 {perf(ret['2019-01-01':])}")
        if k == 4.0:
            yearly = ret.groupby(ret.index.year).sum()
            print("           by year %:", {y: round(100 * v, 1) for y, v in yearly.items()})


if __name__ == "__main__":
    main()
    main_stops()
