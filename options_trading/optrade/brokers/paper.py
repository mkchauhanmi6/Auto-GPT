"""Local paper-trading ledger: fills at live/estimated bid-ask with slippage and charges.

State lives in ``$OPTRADE_HOME/paper_account.json`` so positions survive restarts.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import CostConfig
from ..costs import order_charges
from ..data.base import MarketData
from ..models import Leg, OptionType, Quote, Side, Signal
from ..pricing import time_to_expiry
from ..timeutil import to_ist
from .position import Position

TICK = 0.05


class InsufficientMargin(Exception):
    pass


def _tick(price: float) -> float:
    return max(round(round(price / TICK) * TICK, 2), TICK)


class PaperBroker:
    def __init__(self, state_file: Path, data: MarketData, costs: CostConfig, capital: float):
        self.state_file = Path(state_file)
        self.data = data
        self.costs = costs
        self.capital = capital
        self.positions: list[Position] = []
        self._load()

    # ---- persistence ------------------------------------------------------
    def _load(self) -> None:
        if self.state_file.exists():
            raw = json.loads(self.state_file.read_text())
            self.capital = raw.get("capital", self.capital)
            self.positions = [Position.from_dict(p) for p in raw.get("positions", [])]

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"capital": self.capital, "positions": [p.to_dict() for p in self.positions]}
        fd, tmp = tempfile.mkstemp(dir=self.state_file.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, self.state_file)

    def reset(self, capital: float) -> None:
        self.capital = capital
        self.positions = []
        self.save()

    # ---- queries ----------------------------------------------------------
    def open_positions(self, underlying: Optional[str] = None) -> list[Position]:
        return [
            p for p in self.positions if p.is_open and (underlying is None or p.underlying == underlying)
        ]

    def closed_positions(self) -> list[Position]:
        return [p for p in self.positions if not p.is_open]

    def get(self, position_id: str) -> Position:
        for p in self.positions:
            if p.id == position_id:
                return p
        raise KeyError(f"No position {position_id}")

    def realized_pnl(self, on_date=None) -> float:
        total = 0.0
        for p in self.closed_positions():
            if on_date is None or (p.exit_time and datetime.fromisoformat(p.exit_time).date() == on_date):
                total += p.realized_pnl or 0.0
        return total

    def blocked_margin(self) -> float:
        return sum(p.max_loss or 0.0 for p in self.open_positions())

    def equity(self) -> float:
        return self.capital + self.realized_pnl() + sum(p.last_mtm for p in self.open_positions())

    # ---- pricing ----------------------------------------------------------
    def _fill_price(self, side: Side, q: Quote) -> float:
        slip = self.costs.slippage_pct / 100
        if side is Side.BUY:
            return _tick((q.ask or q.ltp) * (1 + slip))
        return _tick((q.bid or q.ltp) * (1 - slip))

    def _exit_quotes(self, legs: list[Leg]) -> list[Quote]:
        """Quotes for closing each leg; expired contracts settle at intrinsic value."""
        now = self.data.now()
        live = [leg.contract for leg in legs if time_to_expiry(leg.contract.expiry, now) > 0]
        quotes = self.data.option_quotes(live) if live else {}
        out = []
        for leg in legs:
            c = leg.contract
            if c in quotes:
                out.append(quotes[c])
            else:
                spot = self.data.spot(c.underlying)
                intrinsic = max(spot - c.strike, 0) if c.option_type is OptionType.CE else max(c.strike - spot, 0)
                out.append(Quote(ltp=intrinsic, bid=intrinsic, ask=intrinsic))
        return out

    # ---- trading ----------------------------------------------------------
    def open(self, signal: Signal) -> Position:
        if not signal.is_trade:
            raise ValueError("Signal is not a trade")
        max_loss = signal.max_loss or 0.0
        free = self.capital + self.realized_pnl() - self.blocked_margin()
        if max_loss > free:
            raise InsufficientMargin(f"Needs Rs {max_loss:,.0f} margin, only Rs {free:,.0f} free")

        quotes = self.data.option_quotes([leg.contract for leg in signal.legs])
        legs, charges = [], 0.0
        for leg in signal.legs:
            price = self._fill_price(leg.side, quotes[leg.contract])
            legs.append(Leg(leg.contract, leg.side, leg.lots, leg.lot_size, price))
            charges += order_charges(leg.side, price, leg.quantity, self.costs)

        pos = Position(
            id=uuid.uuid4().hex[:6],
            signal_id=signal.id,
            underlying=signal.underlying,
            strategy=signal.strategy,
            legs=legs,
            entry_time=to_ist(self.data.now()).isoformat(),
            stop_loss=signal.stop_loss,
            target=signal.target,
            max_loss=signal.max_loss,
            entry_charges=round(charges, 2),
            estimated_prices=signal.estimated_prices,
            last_mtm=-round(charges, 2),
            last_prices=[leg.price for leg in legs],
        )
        self.positions.append(pos)
        self.save()
        return pos

    def mark(self, pos: Position) -> float:
        """Update and return mark-to-market P&L (after entry charges) using LTPs."""
        quotes = self._exit_quotes(pos.legs)
        prices = [q.ltp for q in quotes]
        pos.last_prices = prices
        pos.last_mtm = round(pos.gross_pnl(prices) - pos.entry_charges, 2)
        return pos.last_mtm

    def close(self, pos: Position, reason: str) -> Position:
        if not pos.is_open:
            return pos
        quotes = self._exit_quotes(pos.legs)
        # buy back shorts first, then sell longs
        order = sorted(range(len(pos.legs)), key=lambda i: pos.legs[i].side is Side.BUY)
        exit_prices = [0.0] * len(pos.legs)
        charges = 0.0
        for i in order:
            leg = pos.legs[i]
            side = leg.side.opposite()
            price = self._fill_price(side, quotes[i]) if quotes[i].ltp > 0 else 0.0
            exit_prices[i] = price
            if price > 0:
                charges += order_charges(side, price, leg.quantity, self.costs)
        pos.exit_prices = exit_prices
        pos.exit_charges = round(charges, 2)
        pos.realized_pnl = round(pos.gross_pnl(exit_prices) - pos.entry_charges - pos.exit_charges, 2)
        pos.last_mtm = pos.realized_pnl
        pos.exit_time = to_ist(self.data.now()).isoformat()
        pos.exit_reason = reason
        pos.status = "CLOSED"
        self.save()
        return pos
