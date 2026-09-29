"""Free data: index candles from Yahoo Finance, India VIX as implied vol.

Yahoo has no NSE option quotes, so option premiums are *estimated* with
Black-Scholes (India VIX / realised vol + a skew). Signals built on this source
are flagged as estimated: check the actual premium in Sensibull before entering.
Yahoo intraday data can be delayed by a few minutes.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Optional

from ..indicators import realized_vol
from ..instruments import get_spec
from ..models import Candle
from ..timeutil import IST
from .base import EstimatedOptionsMixin, MarketData
from .scripmaster import ScripMaster

log = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
VIX_SYMBOL = "^INDIAVIX"
# Rough ratio of each index's ATM IV to India VIX (which is NIFTY-based).
VIX_MULTIPLIER = {"NIFTY": 1.0, "BANKNIFTY": 1.2, "FINNIFTY": 1.1, "MIDCPNIFTY": 1.3}


class YahooMarketData(EstimatedOptionsMixin, MarketData):
    name = "yahoo"

    def __init__(self, scrip_master: Optional[ScripMaster] = None, cache_seconds: int = 30):
        self.scrip_master = scrip_master
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, dict]] = {}

    # ---- http -------------------------------------------------------------
    def _chart(self, symbol: str, interval: str, rng: str) -> dict:
        key = f"{symbol}|{interval}|{rng}"
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_seconds:
            return hit[1]
        url = CHART_URL.format(
            symbol=urllib.parse.quote(symbol),
            query=urllib.parse.urlencode({"interval": interval, "range": rng}),
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        result = payload["chart"]["result"]
        if not result:
            raise RuntimeError(f"Yahoo returned no data for {symbol}: {payload['chart'].get('error')}")
        self._cache[key] = (time.time(), result[0])
        return result[0]

    def _symbol(self, underlying: str) -> str:
        sym = get_spec(underlying).yahoo_symbol
        if not sym:
            raise ValueError(f"No Yahoo symbol for {underlying}; use --data kite for it")
        return sym

    @staticmethod
    def _to_candles(result: dict) -> list[Candle]:
        ts = result.get("timestamp") or []
        q = result["indicators"]["quote"][0]
        out = []
        for i, t in enumerate(ts):
            o, h, low, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, low, c):
                continue
            vol = (q.get("volume") or [0] * len(ts))[i] or 0
            out.append(Candle(datetime.fromtimestamp(t, IST), o, h, low, c, float(vol)))
        return out

    # ---- MarketData -------------------------------------------------------
    def spot(self, underlying: str) -> float:
        res = self._chart(self._symbol(underlying), "5m", "1d")
        return float(res["meta"]["regularMarketPrice"])

    def candles(self, underlying: str, interval_minutes: int = 5, days: int = 5) -> list[Candle]:
        interval = {1: "1m", 2: "2m", 5: "5m", 15: "15m", 30: "30m", 60: "60m"}.get(interval_minutes, "5m")
        return self._to_candles(self._chart(self._symbol(underlying), interval, f"{max(days, 1)}d"))

    def daily_candles(self, underlying: str, days: int = 60) -> list[Candle]:
        rng = "3mo" if days <= 60 else "1y"
        return self._to_candles(self._chart(self._symbol(underlying), "1d", rng))[-days:]

    def atm_iv(self, underlying: str) -> Optional[float]:
        try:
            vix = float(self._chart(VIX_SYMBOL, "5m", "1d")["meta"]["regularMarketPrice"])
        except Exception as e:  # noqa: BLE001 - data source hiccup shouldn't crash the loop
            log.warning("India VIX unavailable: %s", e)
            return None
        return vix / 100.0 * VIX_MULTIPLIER.get(underlying.upper(), 1.1)

    def vol_for_pricing(self, underlying: str) -> float:
        iv = self.atm_iv(underlying)
        if iv:
            return iv
        closes = [c.close for c in self.daily_candles(underlying, 30)]
        return max(realized_vol(closes, 252) * 1.1, 0.08)

    def expiries(self, underlying: str) -> list[date]:
        if self.scrip_master:
            try:
                exp = self.scrip_master.expiries(underlying)
                if exp:
                    return exp
            except Exception as e:  # noqa: BLE001
                log.warning("Scrip master unavailable, using calendar rule: %s", e)
        return super().expiries(underlying)

    def lot_size(self, underlying: str) -> int:
        if self.scrip_master:
            try:
                lot = self.scrip_master.lot_size(underlying)
                if lot:
                    return lot
            except Exception as e:  # noqa: BLE001
                log.warning("Scrip master unavailable, using default lot size: %s", e)
        return super().lot_size(underlying)
