"""Indian market clock helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def is_trading_day(d: date, holidays: Iterable[date] = ()) -> bool:
    return d.weekday() < 5 and d not in set(holidays)


def is_market_open(now: datetime, holidays: Iterable[date] = ()) -> bool:
    now = to_ist(now)
    return is_trading_day(now.date(), holidays) and MARKET_OPEN <= now.time() < MARKET_CLOSE
