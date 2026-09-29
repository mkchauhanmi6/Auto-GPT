"""Sensibull order tickets.

Sensibull has no public API for placing (virtual or real) orders, so this
cannot click "Place order" for you. Instead every entry/exit is rendered as a
precise ticket you can copy into Sensibull Virtual Trading in under a minute;
tickets are printed, appended to ``$OPTRADE_HOME/sensibull_tickets.txt`` and
(optionally) pushed to your phone via Telegram.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..models import Signal
from .base import OrderMirror
from .position import Position

log = logging.getLogger(__name__)


def _rs(x: Optional[float]) -> str:
    if x is None:
        return "unlimited"
    return f"Rs {x:,.0f}"


def signal_ticket(sig: Signal, square_off: Optional[str] = None) -> str:
    if not sig.is_trade:
        return (
            f"[{sig.created_at:%H:%M}] {sig.underlying} spot {sig.spot:,.2f}: NO TRADE - "
            + "; ".join(sig.rationale[:1])
        )
    expiry = sig.legs[0].contract.expiry
    lines = [
        f"=== SIGNAL {sig.id} | {sig.underlying} | {sig.strategy} | confidence {sig.confidence:.0%} ===",
        f"Time {sig.created_at:%d-%b %H:%M} | Spot {sig.spot:,.2f} | Expiry {expiry:%d-%b-%Y}"
        + (" | premiums ESTIMATED" if sig.estimated_prices else ""),
        f"Sensibull: Virtual Trading -> {sig.underlying} option chain / Strategy Builder, expiry {expiry:%d %b}, add:",
    ]
    for i, leg in enumerate(sig.legs, 1):
        lines.append(
            f"  {i}. {leg.side.value:<4} {leg.lots} lot(s) = {leg.quantity} qty  "
            f"{leg.contract.symbol}  @ ~{leg.price:,.2f}"
        )
    qty = sig.legs[0].quantity
    kind = "debit" if sig.net_premium > 0 else "credit"
    lines.append(f"Net {kind} ~{abs(sig.net_premium):,.2f}/unit = {_rs(abs(sig.net_premium) * qty)}")
    be = ", ".join(f"{b:,.1f}" for b in sig.breakevens) or "-"
    lines.append(f"Max profit {_rs(sig.max_profit)} | Max loss {_rs(sig.max_loss)} | Breakeven {be}")
    exit_rule = f"Exit: stop-loss at P&L {_rs(sig.stop_loss)} | target {_rs(sig.target)}"
    if square_off:
        exit_rule += f" | square off by {square_off}"
    lines.append(exit_rule)
    lines.append("Why: " + "; ".join(sig.rationale[:6]))
    return "\n".join(lines)


def exit_ticket(pos: Position) -> str:
    return (
        f"=== EXIT {pos.id} | {pos.underlying} | {pos.strategy} | reason {pos.exit_reason} ===\n"
        f"Sensibull: Virtual Trading -> Positions -> select all {len(pos.legs)} leg(s) of this "
        f"{pos.strategy} -> Exit.  Paper P&L {_rs(pos.realized_pnl)} (after charges)"
    )


class SensibullTickets(OrderMirror):
    name = "sensibull"

    def __init__(
        self,
        ticket_file: Path,
        square_off: Optional[str] = None,
        echo: Callable[[str], None] = print,
        notify: Optional[Callable[[str], None]] = None,
        show_no_trade: bool = True,
    ):
        self.ticket_file = Path(ticket_file)
        self.square_off = square_off
        self.echo = echo
        self.notify = notify
        self.show_no_trade = show_no_trade

    def _emit(self, text: str, push: bool) -> None:
        self.echo(text)
        self.ticket_file.parent.mkdir(parents=True, exist_ok=True)
        with self.ticket_file.open("a") as fh:
            fh.write(f"# {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n\n")
        if push and self.notify:
            try:
                self.notify(text)
            except Exception as e:  # noqa: BLE001 - notifications must never stop trading
                log.warning("Notification failed: %s", e)

    def on_signal(self, signal: Signal) -> None:
        if not signal.is_trade and self.show_no_trade:
            self.echo(signal_ticket(signal))

    def on_entry(self, position: Position, signal: Signal) -> None:
        fills = ", ".join(f"{leg.side.value} {leg.contract.symbol} @ {leg.price:.2f}" for leg in position.legs)
        self._emit(
            signal_ticket(signal, self.square_off) + f"\nPaper position {position.id} filled: {fills}",
            push=True,
        )

    def on_exit(self, position: Position) -> None:
        self._emit(exit_ticket(position), push=True)
