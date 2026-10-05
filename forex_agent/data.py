"""Free market data (Yahoo Finance) and the economic calendar (ForexFactory feed).

Yahoo is used for research/backtests and as a fallback when no broker connection
is available. Live decisions should use broker candles (see broker.py).
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request

import pandas as pd

UA = {"User-Agent": "Mozilla/5.0"}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def yahoo_candles(pair: str, interval: str = "1d", rng: str = "20y") -> pd.DataFrame:
    """Closed OHLC candles for a pair like 'EURUSD'. interval: 1d, 1h (1h max range 730d).

    Note: range=max silently downgrades daily bars to monthly, so default to 20y.
    """
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{pair}=X"
        f"?range={rng}&interval={interval}"
    )
    res = _get_json(url)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame(
        {"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]},
        index=pd.to_datetime(res["timestamp"], unit="s", utc=True),
    ).dropna()
    # Yahoo FX history has occasional bad prints; drop bars with impossible ranges.
    bad = (df["high"] < df[["open", "close"]].max(axis=1)) | (
        df["low"] > df[["open", "close"]].min(axis=1)
    )
    df = df[~bad]
    spike = (df["high"] - df["low"]) / df["close"] > 0.08
    df = df[~spike]
    # The last row is the still-forming bar (stamped with the current time): drop it.
    if len(df) > 2 and df.index[-1].time() != df.index[-2].time():
        df = df.iloc[:-1]
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        df.resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )


CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def calendar_events(impact: tuple[str, ...] = ("High",)) -> list[dict]:
    """This week's economic events, filtered by impact. Times are timezone-aware."""
    events = _get_json(CALENDAR_URL)
    out = []
    for e in events:
        if e.get("impact") not in impact:
            continue
        out.append(
            {
                "time": dt.datetime.fromisoformat(e["date"]),
                "currency": e["country"],
                "title": e["title"],
                "impact": e["impact"],
                "forecast": e.get("forecast"),
                "previous": e.get("previous"),
            }
        )
    return out


def upcoming_news(pair: str, within_hours: float, now: dt.datetime | None = None) -> list[dict]:
    """High-impact events for either currency of `pair` within the next N hours."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ccys = {pair[:3], pair[3:6]}
    horizon = now + dt.timedelta(hours=within_hours)
    return [
        e for e in calendar_events() if e["currency"] in ccys and now <= e["time"] <= horizon
    ]
