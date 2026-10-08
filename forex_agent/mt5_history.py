"""Hourly candles from the MT5 broker via MetaApi (faster than Dukascopy's throttled feed).

Pages backwards 1000 bars at a time to START and caches one CSV per symbol in the same
place and format as dukascopy.py, so intraday.py can use either source.

    python -m forex_agent.mt5_history [SYMBOL ...]
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys

import pandas as pd
from metaapi_cloud_sdk import MetaApi

from . import dukascopy
from .config import broker_symbol

START = dt.datetime(2010, 1, 1, tzinfo=dt.timezone.utc)


async def fetch(account, sym: str) -> pd.DataFrame:
    rows, t = [], None
    while True:
        c = await account.get_historical_candles(broker_symbol(sym), "1h", t, 1000)
        if not c:
            break
        rows.extend(c)
        first = c[0]["time"]
        if first <= START or len(c) < 2:
            break
        t = first - dt.timedelta(hours=1)
    df = pd.DataFrame(rows)
    df = df.set_index(pd.to_datetime(df["time"], utc=True))[
        ["open", "high", "low", "close", "tickVolume"]].rename(columns={"tickVolume": "volume"})
    df = df[~df.index.duplicated()].sort_index()
    # Drop the still-forming last bar.
    if len(df) and df.index[-1] > pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=1):
        df = df.iloc[:-1]
    return df[df.index >= START]


async def main(symbols: list[str]) -> None:
    api = MetaApi(os.environ["METAAPI_TOKEN"])
    try:
        account = await api.metatrader_account_api.get_account(os.environ["METAAPI_ACCOUNT_ID"])
        os.makedirs(dukascopy.CACHE, exist_ok=True)
        for sym in symbols:
            df = await fetch(account, sym)
            df.to_csv(dukascopy.path(sym), compression="gzip", float_format="%.6f")
            print(f"{sym}: {len(df)} bars {df.index[0]} .. {df.index[-1]}", flush=True)
    finally:
        api.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or dukascopy.SYMBOLS))
