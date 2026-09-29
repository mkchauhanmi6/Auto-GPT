"""Live NSE data through Zerodha Kite Connect (real option LTPs, bid/ask and OI).

This module only *reads* market data. It never places orders on Kite.

Needs ``pip install kiteconnect`` plus env vars ``KITE_API_KEY`` and
``KITE_ACCESS_TOKEN`` (a Kite Connect app; the access token must be refreshed
daily through the Kite login flow). Historical candles need the historical-data
permission on your Kite Connect app.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from ..instruments import atm_strike, get_spec
from ..models import Candle, OptionContract, OptionType, Quote
from ..pricing import implied_vol, time_to_expiry
from ..timeutil import IST
from .base import MarketData

log = logging.getLogger(__name__)


class KiteMarketData(MarketData):
    name = "kite"

    def __init__(self, api_key: Optional[str] = None, access_token: Optional[str] = None):
        try:
            from kiteconnect import KiteConnect  # type: ignore
        except ImportError as e:  # pragma: no cover - optional dependency
            raise RuntimeError("Kite data needs `pip install kiteconnect`") from e
        api_key = api_key or os.environ.get("KITE_API_KEY")
        access_token = access_token or os.environ.get("KITE_ACCESS_TOKEN")
        if not api_key or not access_token:
            raise RuntimeError("Set KITE_API_KEY and KITE_ACCESS_TOKEN to use Kite data")
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self._nfo: Optional[list[dict]] = None
        self._by_contract: dict[tuple, dict] = {}

    # ---- instruments ------------------------------------------------------
    def _instruments(self) -> list[dict]:
        if self._nfo is None:
            self._nfo = [
                i for i in self.kite.instruments("NFO") if i.get("segment") == "NFO-OPT"
            ]
            for i in self._nfo:
                key = (i["name"], i["expiry"], float(i["strike"]), i["instrument_type"])
                self._by_contract[key] = i
        return self._nfo

    def _instrument(self, c: OptionContract) -> dict:
        self._instruments()
        key = (c.underlying.upper(), c.expiry, float(c.strike), c.option_type.value)
        try:
            return self._by_contract[key]
        except KeyError as e:
            raise ValueError(f"No Kite instrument for {c.symbol}") from e

    def expiries(self, underlying: str) -> list[date]:
        today = self.now().date()
        u = underlying.upper()
        return sorted({i["expiry"] for i in self._instruments() if i["name"] == u and i["expiry"] >= today})

    def lot_size(self, underlying: str) -> int:
        u = underlying.upper()
        for i in self._instruments():
            if i["name"] == u:
                return int(i["lot_size"])
        return super().lot_size(underlying)

    # ---- prices -----------------------------------------------------------
    def _index_ltp(self, underlying: str) -> dict:
        sym = get_spec(underlying).kite_index_symbol
        return self.kite.ltp([sym])[sym]

    def spot(self, underlying: str) -> float:
        return float(self._index_ltp(underlying)["last_price"])

    def _history(self, underlying: str, interval: str, days: int) -> list[Candle]:
        token = self._index_ltp(underlying)["instrument_token"]
        to = self.now()
        rows = self.kite.historical_data(token, to - timedelta(days=days), to, interval)
        out = []
        for r in rows:
            ts = r["date"]
            if isinstance(ts, datetime) and ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            out.append(Candle(ts, r["open"], r["high"], r["low"], r["close"], float(r.get("volume") or 0)))
        return out

    def candles(self, underlying: str, interval_minutes: int = 5, days: int = 5) -> list[Candle]:
        interval = {1: "minute", 3: "3minute", 5: "5minute", 15: "15minute", 30: "30minute", 60: "60minute"}
        # calendar days, padded for weekends/holidays
        return self._history(underlying, interval.get(interval_minutes, "5minute"), days + 4)

    def daily_candles(self, underlying: str, days: int = 60) -> list[Candle]:
        return self._history(underlying, "day", int(days * 1.6) + 5)[-days:]

    def option_quotes(self, contracts: Iterable[OptionContract]) -> dict[OptionContract, Quote]:
        contracts = list(contracts)
        keys = {f"NFO:{self._instrument(c)['tradingsymbol']}": c for c in contracts}
        raw = self.kite.quote(list(keys))
        out: dict[OptionContract, Quote] = {}
        now = self.now()
        spots: dict[str, float] = {}
        for k, c in keys.items():
            q = raw.get(k)
            if not q:
                raise ValueError(f"No quote for {k}")
            depth = q.get("depth") or {}
            bid = (depth.get("buy") or [{}])[0].get("price") or None
            ask = (depth.get("sell") or [{}])[0].get("price") or None
            ltp = float(q["last_price"])
            if c.underlying not in spots:
                spots[c.underlying] = self.spot(c.underlying)
            iv = implied_vol(ltp, spots[c.underlying], c.strike, time_to_expiry(c.expiry, now), c.option_type)
            out[c] = Quote(ltp=ltp, bid=bid, ask=ask, iv=iv, oi=q.get("oi"))
        return out

    def option_quote(self, contract: OptionContract) -> Quote:
        return self.option_quotes([contract])[contract]

    def atm_iv(self, underlying: str) -> Optional[float]:
        """Average implied vol of the ATM call and put of the nearest expiry (>= 1 day away)."""
        spec = get_spec(underlying)
        today = self.now().date()
        exps = [e for e in self.expiries(underlying) if e > today] or self.expiries(underlying)
        if not exps:
            return None
        strike = atm_strike(self.spot(underlying), spec.strike_step)
        contracts = [OptionContract(spec.name, exps[0], strike, t) for t in OptionType]
        try:
            ivs = [q.iv for q in self.option_quotes(contracts).values() if q.iv]
        except Exception as e:  # noqa: BLE001
            log.warning("ATM IV unavailable: %s", e)
            return None
        return sum(ivs) / len(ivs) if ivs else None
