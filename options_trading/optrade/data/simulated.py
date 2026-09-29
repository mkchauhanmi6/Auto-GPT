"""Synthetic market for offline testing, demos and quick backtests.

Spot follows a geometric Brownian motion on 5-minute bars (75 bars per NSE
session) with regime switches in drift, so the strategy sees trending and
range-bound days. Options are priced with Black-Scholes off an implied vol that
drifts around realised vol.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta
from typing import Optional

from ..instruments import get_spec, rule_based_expiries
from ..models import Candle
from ..timeutil import IST, MARKET_OPEN
from .base import EstimatedOptionsMixin, MarketData

BAR_MINUTES = 5
BARS_PER_DAY = 75  # 09:15 -> 15:30
DEFAULT_SPOTS = {"NIFTY": 25000.0, "BANKNIFTY": 56000.0, "FINNIFTY": 26500.0, "MIDCPNIFTY": 13000.0}


def _trading_days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class SimulatedMarketData(EstimatedOptionsMixin, MarketData):
    name = "simulated"

    def __init__(
        self,
        underlyings: list[str],
        start: date = date(2026, 1, 5),
        history_days: int = 10,
        future_days: int = 20,
        annual_vol: float = 0.14,
        seed: Optional[int] = 7,
    ):
        self.rng = random.Random(seed)
        self.annual_vol = annual_vol
        self.days = _trading_days(start, history_days + future_days)
        self.bars: dict[str, list[Candle]] = {}
        self.iv_path: list[float] = []
        for u in underlyings:
            get_spec(u)  # validate
            self.bars[u] = self._generate(DEFAULT_SPOTS.get(u.upper(), 20000.0))
        self._gen_iv_path()
        # Start "now" at the end of the history window.
        self.index = history_days * BARS_PER_DAY - 1

    # ---- generation -------------------------------------------------------
    def _generate(self, spot: float) -> list[Candle]:
        dt = BAR_MINUTES / (60 * 6.25 * 252)  # fraction of a trading year per bar
        bars: list[Candle] = []
        for d in self.days:
            # daily regime: trending up / down or choppy
            regime = self.rng.choice([-1, 0, 0, 1])
            drift = regime * self.rng.uniform(0.6, 1.6)
            vol = self.annual_vol * self.rng.uniform(0.7, 1.3)
            ts = datetime.combine(d, MARKET_OPEN, tzinfo=IST)
            # overnight gap
            spot *= math.exp(self.rng.gauss(0, self.annual_vol * math.sqrt(1 / 252) * 0.5))
            for _ in range(BARS_PER_DAY):
                o = spot
                path = [o]
                for _ in range(5):  # 1-minute sub-steps for realistic highs/lows
                    z = self.rng.gauss(0, 1)
                    step = (drift - 0.5 * vol * vol) * (dt / 5) + vol * math.sqrt(dt / 5) * z
                    path.append(path[-1] * math.exp(step))
                spot = path[-1]
                bars.append(
                    Candle(ts, o, max(path), min(path), spot, volume=self.rng.randint(5_000, 50_000))
                )
                ts += timedelta(minutes=BAR_MINUTES)
        return bars

    def _gen_iv_path(self) -> None:
        iv = self.annual_vol
        for _ in self.days:
            iv = min(max(iv + self.rng.gauss(0, 0.012) + 0.2 * (self.annual_vol - iv), 0.08), 0.45)
            self.iv_path.append(iv)

    # ---- clock ------------------------------------------------------------
    @property
    def total_bars(self) -> int:
        return len(self.days) * BARS_PER_DAY

    def has_next(self) -> bool:
        return self.index + 1 < self.total_bars

    def advance(self, bars: int = 1) -> bool:
        if self.index + bars >= self.total_bars:
            return False
        self.index += bars
        return True

    def now(self) -> datetime:
        any_bars = next(iter(self.bars.values()))
        return any_bars[self.index].ts + timedelta(minutes=BAR_MINUTES)

    # ---- MarketData -------------------------------------------------------
    def spot(self, underlying: str) -> float:
        return self.bars[underlying.upper()][self.index].close

    def candles(self, underlying: str, interval_minutes: int = 5, days: int = 5) -> list[Candle]:
        bars = self.bars[underlying.upper()][: self.index + 1]
        bars = bars[-days * BARS_PER_DAY :]
        if interval_minutes == BAR_MINUTES:
            return list(bars)
        return _resample(bars, max(1, interval_minutes // BAR_MINUTES))

    def daily_candles(self, underlying: str, days: int = 60) -> list[Candle]:
        bars = self.bars[underlying.upper()][: self.index + 1]
        out: list[Candle] = []
        for i in range(0, len(bars), BARS_PER_DAY):
            chunk = bars[i : i + BARS_PER_DAY]
            out.append(_merge(chunk))
        return out[-days:]

    def atm_iv(self, underlying: str) -> Optional[float]:
        return self.iv_path[self.index // BARS_PER_DAY]

    def vol_for_pricing(self, underlying: str) -> float:
        return self.atm_iv(underlying) or self.annual_vol

    def expiries(self, underlying: str) -> list[date]:
        return rule_based_expiries(get_spec(underlying), self.now().date())


def _merge(chunk: list[Candle]) -> Candle:
    return Candle(
        chunk[0].ts,
        chunk[0].open,
        max(c.high for c in chunk),
        min(c.low for c in chunk),
        chunk[-1].close,
        sum(c.volume for c in chunk),
    )


def _resample(bars: list[Candle], factor: int) -> list[Candle]:
    return [_merge(bars[i : i + factor]) for i in range(0, len(bars), factor)]
