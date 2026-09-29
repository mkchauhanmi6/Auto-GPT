"""Index option specifications and expiry calendar.

Lot sizes / expiry rules below were checked against the NSE contract master
(Dhan scrip master) in Sep 2026. NSE revises lot sizes periodically; when a
live data source is configured the real lot size and expiry list from the
exchange instrument dump take precedence over these defaults.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

from .timeutil import is_trading_day


@dataclass(frozen=True)
class UnderlyingSpec:
    name: str
    lot_size: int
    strike_step: float
    weekly_expiry: bool
    yahoo_symbol: Optional[str]
    kite_index_symbol: str  # e.g. "NSE:NIFTY 50"
    dhan_index_security_id: int
    expiry_weekday: int = 1  # Tuesday (NSE moved all expiries to Tuesday in Sep 2025)


UNDERLYINGS: dict[str, UnderlyingSpec] = {
    "NIFTY": UnderlyingSpec("NIFTY", 65, 50, True, "^NSEI", "NSE:NIFTY 50", 13),
    "BANKNIFTY": UnderlyingSpec("BANKNIFTY", 30, 100, False, "^NSEBANK", "NSE:NIFTY BANK", 25),
    "FINNIFTY": UnderlyingSpec("FINNIFTY", 60, 50, False, "NIFTY_FIN_SERVICE.NS", "NSE:NIFTY FIN SERVICE", 27),
    "MIDCPNIFTY": UnderlyingSpec("MIDCPNIFTY", 120, 25, False, None, "NSE:NIFTY MID SELECT", 442),
}


def get_spec(underlying: str) -> UnderlyingSpec:
    try:
        return UNDERLYINGS[underlying.upper()]
    except KeyError as e:
        raise ValueError(
            f"Unsupported underlying {underlying!r}. Choose from {', '.join(UNDERLYINGS)}"
        ) from e


def _adjust_for_holiday(d: date, holidays: Iterable[date]) -> date:
    hol = set(holidays)
    while not is_trading_day(d, hol):
        d -= timedelta(days=1)
    return d


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def rule_based_expiries(
    spec: UnderlyingSpec, today: date, count: int = 6, holidays: Iterable[date] = ()
) -> list[date]:
    """Upcoming expiries from the calendar rule (fallback when no instrument dump)."""
    holidays = list(holidays)
    out: list[date] = []
    if spec.weekly_expiry:
        d = today
        while d.weekday() != spec.expiry_weekday:
            d += timedelta(days=1)
        while len(out) < count:
            e = _adjust_for_holiday(d, holidays)
            if e >= today:
                out.append(e)
            d += timedelta(days=7)
    else:
        y, m = today.year, today.month
        while len(out) < count:
            e = _adjust_for_holiday(_last_weekday_of_month(y, m, spec.expiry_weekday), holidays)
            if e >= today:
                out.append(e)
            m += 1
            if m > 12:
                y, m = y + 1, 1
    return out


def round_to_step(value: float, step: float) -> float:
    return round(value / step) * step


def atm_strike(spot: float, step: float) -> float:
    return round_to_step(spot, step)
