"""Core data types shared across the package."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType

    @property
    def symbol(self) -> str:
        """Human readable symbol, e.g. ``NIFTY 06OCT26 25000 CE``."""
        strike = int(self.strike) if float(self.strike).is_integer() else self.strike
        return (
            f"{self.underlying} {self.expiry.strftime('%d%b%y').upper()} "
            f"{strike} {self.option_type.value}"
        )

    def to_dict(self) -> dict:
        return {
            "underlying": self.underlying,
            "expiry": self.expiry.isoformat(),
            "strike": self.strike,
            "option_type": self.option_type.value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OptionContract":
        return cls(
            underlying=d["underlying"],
            expiry=date.fromisoformat(d["expiry"]),
            strike=float(d["strike"]),
            option_type=OptionType(d["option_type"]),
        )


@dataclass(frozen=True)
class Quote:
    ltp: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    iv: Optional[float] = None
    oi: Optional[float] = None
    # True when the price is a model (Black-Scholes) estimate, not a traded price.
    estimated: bool = False


@dataclass
class Leg:
    contract: OptionContract
    side: Side
    lots: int
    lot_size: int
    price: float = 0.0  # reference / fill premium per unit

    @property
    def quantity(self) -> int:
        return self.lots * self.lot_size

    def to_dict(self) -> dict:
        return {
            "contract": self.contract.to_dict(),
            "side": self.side.value,
            "lots": self.lots,
            "lot_size": self.lot_size,
            "price": self.price,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Leg":
        return cls(
            contract=OptionContract.from_dict(d["contract"]),
            side=Side(d["side"]),
            lots=int(d["lots"]),
            lot_size=int(d["lot_size"]),
            price=float(d.get("price", 0.0)),
        )


class SignalAction(str, Enum):
    ENTER = "ENTER"
    NO_TRADE = "NO_TRADE"


@dataclass
class Signal:
    action: SignalAction
    underlying: str
    strategy: str
    spot: float
    legs: list[Leg] = field(default_factory=list)
    # Net premium per unit of the whole structure: positive = debit paid,
    # negative = credit received.
    net_premium: float = 0.0
    max_profit: Optional[float] = None  # rupees for the whole position; None = unlimited
    max_loss: Optional[float] = None  # rupees for the whole position
    stop_loss: Optional[float] = None  # rupees of P&L at which to exit (negative)
    target: Optional[float] = None  # rupees of P&L at which to book profit
    breakevens: list[float] = field(default_factory=list)
    confidence: float = 0.0  # 0..1
    rationale: list[str] = field(default_factory=list)
    estimated_prices: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def is_trade(self) -> bool:
        return self.action is SignalAction.ENTER and bool(self.legs)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["action"] = self.action.value
        d["legs"] = [leg.to_dict() for leg in self.legs]
        d["created_at"] = self.created_at.isoformat()
        return d
