"""Expiry payoff analysis for multi-leg option positions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .models import Leg, OptionType


def leg_value_at_expiry(leg: Leg, spot: float) -> float:
    k = leg.contract.strike
    intrinsic = max(spot - k, 0.0) if leg.contract.option_type is OptionType.CE else max(k - spot, 0.0)
    return intrinsic


def pnl_at_expiry(legs: Sequence[Leg], spot: float) -> float:
    """Rupee P&L of the whole position at expiry, given each leg's ``price`` as entry premium."""
    return sum(
        leg.side.sign * (leg_value_at_expiry(leg, spot) - leg.price) * leg.quantity for leg in legs
    )


@dataclass(frozen=True)
class PayoffSummary:
    max_profit: Optional[float]  # None = unlimited
    max_loss: Optional[float]  # positive rupees; None = unlimited
    breakevens: list[float]


def analyse(legs: Sequence[Leg]) -> PayoffSummary:
    strikes = sorted({leg.contract.strike for leg in legs})
    lo, hi = 0.0, strikes[-1] * 3
    points = [lo, *strikes, hi]
    values = [pnl_at_expiry(legs, s) for s in points]

    # Slope beyond the highest strike decides unlimited upside/downside.
    right_slope = pnl_at_expiry(legs, hi + 1) - values[-1]
    max_profit: Optional[float] = max(values)
    max_loss: Optional[float] = -min(values)
    if right_slope > 1e-9:
        max_profit = None
    elif right_slope < -1e-9:
        max_loss = None

    breakevens = []
    for (s0, v0), (s1, v1) in zip(zip(points, values), zip(points[1:], values[1:])):
        if v0 == 0 and s0 not in breakevens and s0 > 0:
            breakevens.append(s0)
        elif (v0 < 0 < v1) or (v0 > 0 > v1):
            breakevens.append(round(s0 + (s1 - s0) * (-v0) / (v1 - v0), 2))
    if max_loss is not None:
        max_loss = max(max_loss, 0.0)
    return PayoffSummary(max_profit, max_loss, breakevens)
