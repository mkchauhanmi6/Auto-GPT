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
    if interval == "1d":
        # Since ~2011 Yahoo's daily FX "close" is a snapshot taken just after the open
        # (median |open[t+1] - close[t]| is ~0.5 ATR, real FX gaps are ~0). The true
        # close is effectively the next bar's open, so rebuild it from that.
        nxt = df["open"].shift(-1)
        df = df.assign(close=nxt.clip(lower=df["low"], upper=df["high"])).iloc[:-1]
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        df.resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )


CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


_calendar_cache: list | None = None


def calendar_events(impact: tuple[str, ...] = ("High",)) -> list[dict]:
    """This week's economic events, filtered by impact. Times are timezone-aware.

    The feed is rate limited, so it is fetched once per process (with one retry).
    """
    global _calendar_cache
    if _calendar_cache is None:
        import time

        try:
            _calendar_cache = _get_json(CALENDAR_URL)
        except Exception:
            time.sleep(20)
            _calendar_cache = _get_json(CALENDAR_URL)
    events = _calendar_cache
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
    try:
        events = calendar_events()
    except Exception as e:  # fail safe: an unknown calendar blocks new entries
        return [{"currency": "?", "title": f"calendar unavailable ({e})", "time": now}]
    return [e for e in events if e["currency"] in ccys and now <= e["time"] <= horizon]


BIS_AREAS = {"USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP", "AUD": "AU",
             "CAD": "CA", "CHF": "CH", "NZD": "NZ"}


def policy_rates(start: str = "2005-01") -> pd.DataFrame:
    """Monthly central-bank policy rates (%, BIS WS_CBPOL), one column per currency."""
    import io

    areas = "+".join(BIS_AREAS.values())
    url = (
        f"https://stats.bis.org/api/v1/data/WS_CBPOL/M.{areas}/all"
        f"?startPeriod={start}&format=csv"
    )
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = pd.read_csv(io.StringIO(resp.read().decode()))
    inv = {v: k for k, v in BIS_AREAS.items()}
    raw["ccy"] = raw["REF_AREA"].map(inv)
    raw["date"] = pd.to_datetime(raw["TIME_PERIOD"]).dt.tz_localize("UTC")
    wide = raw.pivot_table(index="date", columns="ccy", values="OBS_VALUE")
    return wide.sort_index()


def rate_differential(pair: str, rates: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Base minus quote policy rate (%), aligned (as of, no look-ahead) to `index`.

    Monthly values are shifted one month so a bar only sees rates already published.
    """
    diff = (rates[pair[:3]] - rates[pair[3:6]]).shift(1).dropna()
    return diff.reindex(diff.index.union(index)).ffill().reindex(index)
