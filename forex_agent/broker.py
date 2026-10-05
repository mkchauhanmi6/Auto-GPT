"""Thin MetaApi (MT4/MT5 cloud API) wrapper.

Reads METAAPI_TOKEN and METAAPI_ACCOUNT_ID from the environment. MetaApi bills for the
time an account is deployed, so `session()` deploys only if needed and undeploys on exit
if it found the account undeployed.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import math
import os

from metaapi_cloud_sdk import MetaApi

from .config import MAGIC


class Broker:
    def __init__(self, account, conn):
        self.account = account
        self.conn = conn

    async def info(self) -> dict:
        return await self.conn.get_account_information()

    async def positions(self, ours_only: bool = True) -> list[dict]:
        pos = await self.conn.get_positions()
        return [p for p in pos if not ours_only or p.get("magic") == MAGIC]

    async def candles(self, symbol: str, timeframe: str = "1d", limit: int = 300) -> list[dict]:
        return await self.account.get_historical_candles(symbol, timeframe, None, limit)

    async def price(self, symbol: str) -> dict:
        return await self.conn.get_symbol_price(symbol)

    async def spec(self, symbol: str) -> dict:
        return await self.conn.get_symbol_specification(symbol)

    async def deals_since(self, since: dt.datetime, ours_only: bool = True) -> list[dict]:
        res = await self.conn.get_deals_by_time_range(since, dt.datetime.now(dt.timezone.utc))
        deals = res.get("deals", []) if isinstance(res, dict) else res
        return [d for d in deals if not ours_only or d.get("magic") == MAGIC]

    async def lots_for_risk(self, symbol: str, stop_distance: float, risk_money: float) -> float:
        """Largest volume whose loss at the stop does not exceed risk_money (0 if below min)."""
        spec, px = await self.spec(symbol), await self.price(symbol)
        loss_per_lot = stop_distance / spec["tickSize"] * px["lossTickValue"]
        raw = risk_money / loss_per_lot
        step = spec["volumeStep"]
        lots = math.floor(raw / step + 1e-9) * step
        if lots < spec["minVolume"]:
            return 0.0
        return round(min(lots, spec["maxVolume"]), 8)

    async def open(self, symbol: str, side: str, lots: float, stop: float,
                   target: float | None, comment: str) -> dict:
        opts = {"comment": comment[:26], "magic": MAGIC}
        fn = self.conn.create_market_buy_order if side == "buy" else self.conn.create_market_sell_order
        return await fn(symbol, lots, stop, target, opts)

    async def close(self, position_id: str) -> dict:
        return await self.conn.close_position(str(position_id))

    async def modify(self, position_id: str, stop: float | None, target: float | None) -> dict:
        return await self.conn.modify_position(str(position_id), stop, target)


@contextlib.asynccontextmanager
async def session():
    token = os.environ.get("METAAPI_TOKEN")
    account_id = os.environ.get("METAAPI_ACCOUNT_ID")
    if not token or not account_id:
        raise SystemExit("METAAPI_TOKEN and METAAPI_ACCOUNT_ID must be set in the environment")
    api = MetaApi(token)
    account = await api.metatrader_account_api.get_account(account_id)
    was_deployed = account.state in ("DEPLOYED", "DEPLOYING")
    if not was_deployed:
        await account.deploy()
    await account.wait_connected()
    conn = account.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()
    try:
        yield Broker(account, conn)
    finally:
        await conn.close()
        if not was_deployed:
            await account.undeploy()
        api.close()
