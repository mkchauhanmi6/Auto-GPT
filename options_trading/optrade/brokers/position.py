"""A multi-leg paper position."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import Leg


@dataclass
class Position:
    id: str
    signal_id: str
    underlying: str
    strategy: str
    legs: list[Leg]  # leg.price = entry fill price
    entry_time: str
    stop_loss: Optional[float]
    target: Optional[float]
    max_loss: Optional[float]
    entry_charges: float
    estimated_prices: bool = False
    status: str = "OPEN"  # OPEN / CLOSED
    exit_prices: list[float] = field(default_factory=list)
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    exit_charges: float = 0.0
    realized_pnl: Optional[float] = None
    last_mtm: float = 0.0
    last_prices: list[float] = field(default_factory=list)
    mirror_refs: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"

    @property
    def net_premium(self) -> float:
        """Per unit: + debit paid / - credit received."""
        return sum(leg.side.sign * leg.price for leg in self.legs)

    def gross_pnl(self, prices: list[float]) -> float:
        return sum(
            leg.side.sign * (p - leg.price) * leg.quantity for leg, p in zip(self.legs, prices)
        )

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["legs"] = [leg.to_dict() for leg in self.legs]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        d = dict(d)
        d["legs"] = [Leg.from_dict(x) for x in d["legs"]]
        return cls(**d)
