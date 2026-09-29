"""Market data interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Iterable, Optional

from ..instruments import get_spec, rule_based_expiries
from ..models import Candle, OptionContract, Quote
from ..pricing import bs_price, skewed_vol, time_to_expiry
from ..timeutil import now_ist


class MarketData(ABC):
    """A source of spot prices, candles and option quotes."""

    name: str = "base"

    def now(self) -> datetime:
        return now_ist()

    @abstractmethod
    def spot(self, underlying: str) -> float: ...

    @abstractmethod
    def candles(self, underlying: str, interval_minutes: int = 5, days: int = 5) -> list[Candle]:
        """Intraday candles, oldest first."""

    @abstractmethod
    def daily_candles(self, underlying: str, days: int = 60) -> list[Candle]:
        """Daily candles, oldest first."""

    @abstractmethod
    def option_quote(self, contract: OptionContract) -> Quote: ...

    def option_quotes(self, contracts: Iterable[OptionContract]) -> dict[OptionContract, Quote]:
        return {c: self.option_quote(c) for c in contracts}

    def atm_iv(self, underlying: str) -> Optional[float]:
        """Market implied vol for the near expiry ATM (annualised, e.g. 0.14). None if unknown."""
        return None

    def expiries(self, underlying: str) -> list[date]:
        return rule_based_expiries(get_spec(underlying), self.now().date())

    def lot_size(self, underlying: str) -> int:
        return get_spec(underlying).lot_size


class EstimatedOptionsMixin:
    """Prices options with Black-Scholes when no live option quotes are available.

    Subclasses must implement ``spot``, ``now`` and ``vol_for_pricing``.
    """

    risk_free_rate: float = 0.065

    def vol_for_pricing(self, underlying: str) -> float:  # pragma: no cover - interface
        raise NotImplementedError

    def option_quote(self, contract: OptionContract) -> Quote:
        spot = self.spot(contract.underlying)  # type: ignore[attr-defined]
        atm_vol = self.vol_for_pricing(contract.underlying)
        vol = skewed_vol(atm_vol, spot, contract.strike, contract.option_type)
        t = time_to_expiry(contract.expiry, self.now())  # type: ignore[attr-defined]
        price = bs_price(spot, contract.strike, t, vol, contract.option_type, self.risk_free_rate)
        price = max(round(price * 20) / 20, 0.05)  # NSE tick size 0.05
        spread = max(0.05, round(price * 0.005 * 20) / 20)
        return Quote(
            ltp=price,
            bid=max(price - spread, 0.05),
            ask=price + spread,
            iv=vol,
            estimated=True,
        )
