"""FundedNext Stellar 2-Step simulator: how often does a strategy pass, and what does a
funded account then earn?

Rules (help.fundednext.com, checked 2026-10-08):
- Phase 1 target +8%, Phase 2 target +5% of the initial balance; no time limit.
- At least 5 trading days (days with a trade) per phase.
- Daily loss: equity may not fall more than 5% of the initial balance below the day's
  starting balance (floating P&L counts). Max loss: equity >= 90% of initial, always.
- Funded account: open risk (stop distances) capped at 3% of initial at any time;
  rewards paid every 21 days at 80% of profit.

Trades come from intraday.simulate (results in R, plus worst/closing R per trading day).
Each trade risks `risk_pct` of the initial balance. A new trade is skipped when the
already-open risk would exceed `max_open_pct`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAILY_LOSS = 5.0
MAX_LOSS = 10.0
TARGETS = (8.0, 5.0)
MIN_DAYS = 5


def select(trades: pd.DataFrame, risk_pct: float, max_open_pct: float = 3.0) -> pd.DataFrame:
    """Greedy chronological selection under the open-risk cap (start-independent)."""
    cap = max(int(max_open_pct / risk_pct + 1e-9), 1)
    open_exits: list = []
    keep = []
    for i, tr in enumerate(trades.itertuples(index=False)):
        open_exits = [e for e in open_exits if e > tr.entry_time]
        if len(open_exits) < cap:
            keep.append(i)
            open_exits.append(tr.exit_time)
    return trades.iloc[keep].reset_index(drop=True)


def day_table(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per (trade, day): change in R over the day and worst R relative to day start."""
    rows = []
    for k, tr in enumerate(trades.itertuples(index=False)):
        prev = 0.0
        first = True
        for d in sorted(tr.daily):
            worst, end = tr.daily[d]
            rows.append((k, d, tr.entry_time, end - prev, worst - prev, first))
            prev, first = end, False
    return pd.DataFrame(rows, columns=["k", "day", "entry_time", "delta", "worst", "opened"])


def run_phase(days: pd.DataFrame, start, target: float, risk_pct: float):
    """Walk days from `start`. Returns ('pass'|'fail'|'open', end_day, n_days_elapsed)."""
    d = days[(days["day"] >= start) & (days["entry_time"] >= start)]
    if d.empty:
        return "open", None, 0
    g = d.groupby("day").agg(delta=("delta", "sum"), worst=("worst", "sum"),
                            opened=("opened", "sum"))
    eq = 0.0
    tdays = 0
    for day, row in g.iterrows():
        worst_eq = eq + row["worst"] * risk_pct
        if worst_eq - eq <= -DAILY_LOSS or worst_eq <= -MAX_LOSS:
            return "fail", day, (day - start).days
        eq += row["delta"] * risk_pct
        tdays += row["opened"] > 0
        if eq >= target and tdays >= MIN_DAYS:
            return "pass", day, (day - start).days
    return "open", None, 0


def challenge(trades: pd.DataFrame, risk_pct: float, starts, max_open_pct: float = 3.0) -> dict:
    sel = select(trades, risk_pct, max_open_pct)
    days = day_table(sel)
    res = []
    for s in starts:
        r1, end1, n1 = run_phase(days, s, TARGETS[0], risk_pct)
        r2, n2 = None, 0
        if r1 == "pass":
            r2, _, n2 = run_phase(days, end1 + pd.Timedelta(days=1), TARGETS[1], risk_pct)
        res.append((r1, r2, n1, n2))
    df = pd.DataFrame(res, columns=["p1", "p2", "d1", "d2"])
    done1 = df[df["p1"] != "open"]
    p1 = (done1["p1"] == "pass").mean() if len(done1) else np.nan
    done2 = df[df["p2"].isin(["pass", "fail"])]
    p2 = (done2["p2"] == "pass").mean() if len(done2) else np.nan
    both = df[(df["p1"] == "fail") | df["p2"].isin(["pass", "fail"])]
    p_both = (both["p2"] == "pass").mean() if len(both) else np.nan
    passed = df[df["p2"] == "pass"]
    return {"risk_pct": risk_pct, "starts": len(df), "p_phase1": round(p1, 3),
            "p_phase2": round(p2, 3), "p_both": round(p_both, 3),
            "median_days_p1": float(df.loc[df["p1"] == "pass", "d1"].median()) if (df["p1"] == "pass").any() else None,
            "median_days_total": float((passed["d1"] + passed["d2"]).median()) if len(passed) else None}


def funded(trades: pd.DataFrame, risk_pct: float, starts, months: int = 12,
           split: float = 0.8, cycle_days: int = 21, max_open_pct: float = 3.0) -> dict:
    """Funded account from each start for `months`: withdraw `split` of profit every
    `cycle_days` when in profit (balance resets to initial), stop at a breach."""
    sel = select(trades, risk_pct, max_open_pct)
    days = day_table(sel)
    out = []
    for s in starts:
        d = days[(days["day"] >= s) & (days["entry_time"] >= s)]
        end = s + pd.Timedelta(days=30 * months)
        g = d[d["day"] < end].groupby("day").agg(delta=("delta", "sum"), worst=("worst", "sum"))
        eq, paid, breached, cycle_start = 0.0, 0.0, False, s
        for day, row in g.iterrows():
            worst_eq = eq + row["worst"] * risk_pct
            if worst_eq - eq <= -DAILY_LOSS or worst_eq <= -MAX_LOSS:
                breached = True
                break
            eq += row["delta"] * risk_pct
            if (day - cycle_start).days >= cycle_days:
                if eq > 0:
                    paid += split * eq
                    eq = 0.0
                cycle_start = day
        out.append((paid, breached))
    df = pd.DataFrame(out, columns=["paid_pct", "breached"])
    return {"risk_pct": risk_pct, "avg_paid_pct_of_account": round(df["paid_pct"].mean(), 2),
            "median_paid_pct": round(df["paid_pct"].median(), 2),
            "p_breach_within_period": round(df["breached"].mean(), 3)}


def daily_series(trades: pd.DataFrame, risk_pct: float, start, end,
                 max_open_pct: float = 3.0) -> pd.DataFrame:
    """Per trading day (weekdays, including days without trades): summed R change, summed
    worst R and trades opened, for trades entered in [start, end)."""
    sel = select(trades, risk_pct, max_open_pct)
    days = day_table(sel)
    days = days[(days["entry_time"] >= start) & (days["entry_time"] < end)]
    g = days.groupby("day").agg(delta=("delta", "sum"), worst=("worst", "sum"),
                                opened=("opened", "sum"))
    cal = pd.bdate_range(start, end, tz="UTC").normalize()
    return g.reindex(cal, fill_value=0.0)


def _phase_mc(delta, worst, opened, target, risk_pct, max_days):
    eq, tdays = 0.0, 0
    for i in range(min(len(delta), max_days)):
        w = eq + worst[i] * risk_pct
        if w - eq <= -DAILY_LOSS or w <= -MAX_LOSS:
            return "fail", i + 1
        eq += delta[i] * risk_pct
        tdays += opened[i] > 0
        if eq >= target and tdays >= MIN_DAYS:
            return "pass", i + 1
    return "timeout", max_days


def challenge_mc(daily: pd.DataFrame, risk_pct: float, n: int = 2000, block: int = 5,
                 max_days: int = 750, seed: int = 0) -> dict:
    """Block bootstrap of trading days: thousands of independent paths with the same daily
    return distribution (and short-range clustering) as the sample."""
    rng = np.random.default_rng(seed)
    D, W, O = (daily[c].to_numpy() for c in ("delta", "worst", "opened"))
    L = len(D)

    def path(k):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.integers(0, L - block, k // block + 1)])[:k]
        return D[idx], W[idx], O[idx]

    out = []
    for _ in range(n):
        r1, n1 = _phase_mc(*path(max_days), TARGETS[0], risk_pct, max_days)
        r2, n2 = (None, 0)
        if r1 == "pass":
            r2, n2 = _phase_mc(*path(max_days), TARGETS[1], risk_pct, max_days)
        out.append((r1, r2, n1, n2))
    df = pd.DataFrame(out, columns=["p1", "p2", "d1", "d2"])
    both = df["p2"] == "pass"
    return {"risk_pct": risk_pct, "p_phase1": round((df["p1"] == "pass").mean(), 3),
            "p_phase2_given_1": round((df.loc[df["p1"] == "pass", "p2"] == "pass").mean(), 3),
            "p_both": round(both.mean(), 3),
            "p_timeout": round(((df["p1"] == "timeout") | (df["p2"] == "timeout")).mean(), 3),
            "median_trading_days_to_pass": float((df.loc[both, "d1"] + df.loc[both, "d2"]).median()) if both.any() else None}
