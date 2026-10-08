"""Hourly bid candles from Dukascopy's free historical datafeed (research only).

Monthly files: https://datafeed.dukascopy.com/datafeed/{SYM}/{YYYY}/{MM-1:02d}/BID_candles_hour_1.bi5
Each is LZMA-compressed records of 24 bytes: seconds-from-month-start, open, close,
low, high (integers in points) and volume (float). Cached as one CSV per symbol.

    python -m forex_agent.dukascopy            # download/refresh all symbols
"""
from __future__ import annotations

import datetime as dt
import lzma
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
           "EURJPY", "GBPJPY", "EURGBP", "XAUUSD"]
POINT = {"USDJPY": 1e-3, "EURJPY": 1e-3, "GBPJPY": 1e-3, "XAUUSD": 1e-3}
START_YEAR = 2010
CACHE = os.environ.get("DUKA_CACHE", os.path.expanduser("~/.cache/forex_agent/dukascopy"))
THREADS = int(os.environ.get("DUKA_THREADS", "12"))
URL = "https://datafeed.dukascopy.com/datafeed/{s}/{y}/{m:02d}/BID_candles_hour_1.bi5"


def _fetch(url: str, tries: int = 6) -> bytes | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (i + 1))          # 503 = rate limited
        except Exception:
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"failed: {url}")


def month(sym: str, y: int, m: int) -> pd.DataFrame:
    raw = _fetch(URL.format(s=sym, y=y, m=m - 1))
    if not raw:
        return pd.DataFrame()
    data = lzma.decompress(raw, format=lzma.FORMAT_ALONE)
    p = POINT.get(sym, 1e-5)
    base = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    rows = [struct.unpack(">IIIIIf", data[i:i + 24]) for i in range(0, len(data), 24)]
    df = pd.DataFrame(rows, columns=["t", "open", "close", "low", "high", "volume"])
    df = df[df["volume"] > 0]                 # flat placeholder bars (weekends) have 0 volume
    df.index = pd.to_datetime(base) + pd.to_timedelta(df.pop("t"), unit="s")
    df[["open", "close", "low", "high"]] *= p
    return df[["open", "high", "low", "close", "volume"]]


def path(sym: str) -> str:
    return os.path.join(CACHE, f"{sym}_H1.csv.gz")


def load(sym: str) -> pd.DataFrame:
    df = pd.read_csv(path(sym), index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def download(sym: str) -> None:
    os.makedirs(CACHE, exist_ok=True)
    today = dt.date.today()
    have = load(sym) if os.path.exists(path(sym)) else pd.DataFrame()
    # Refetch from the last complete month we hold (the current month is always refreshed).
    if len(have):
        last = have.index[-1]
        y, m = last.year, last.month
        have = have[have.index < pd.Timestamp(y, m, 1, tz="UTC")]
    else:
        y, m = START_YEAR, 1
    months = []
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    # The feed answers slowly (~15 s per file) but tolerates parallel requests.
    with ThreadPoolExecutor(THREADS) as ex:
        parts = [have] + list(ex.map(lambda ym: month(sym, *ym), months))
    out = pd.concat([p for p in parts if len(p)]).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.to_csv(path(sym), compression="gzip", float_format="%.6f")
    print(f"{sym}: {len(out)} bars {out.index[0]} .. {out.index[-1]}", flush=True)


if __name__ == "__main__":
    syms = sys.argv[1:] or SYMBOLS
    with ThreadPoolExecutor(2) as ex:
        list(ex.map(download, syms))
