"""The trading loop: manage exits, apply risk gates, generate signals, paper-trade them."""

from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

from .brokers.base import OrderMirror
from .brokers.paper import InsufficientMargin, PaperBroker
from .brokers.position import Position
from .config import RiskConfig
from .data.base import MarketData
from .models import Signal
from .strategy import SignalGenerator
from .timeutil import is_market_open, to_ist

log = logging.getLogger(__name__)


@dataclass
class TickResult:
    signals: list[Signal] = field(default_factory=list)
    entries: list[Position] = field(default_factory=list)
    exits: list[Position] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    market_open: bool = True


class TradingEngine:
    def __init__(
        self,
        data: MarketData,
        broker: PaperBroker,
        generator: SignalGenerator,
        risk: RiskConfig,
        mirrors: Iterable[OrderMirror] = (),
        journal_file: Optional[Path] = None,
        echo: Callable[[str], None] = print,
        ignore_market_hours: bool = False,
    ):
        self.data = data
        self.broker = broker
        self.generator = generator
        self.risk = risk
        self.mirrors = list(mirrors)
        self.journal_file = journal_file
        self.echo = echo
        self.ignore_market_hours = ignore_market_hours

    # ---- helpers ----------------------------------------------------------
    def _journal(self, kind: str, payload: dict) -> None:
        if not self.journal_file:
            return
        self.journal_file.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_file.open("a") as fh:
            fh.write(json.dumps({"ts": to_ist(self.data.now()).isoformat(), "kind": kind, **payload}, default=str))
            fh.write("\n")

    def _mirror(self, hook: str, *args) -> None:
        for m in self.mirrors:
            try:
                getattr(m, hook)(*args)
            except Exception as e:  # noqa: BLE001 - a failing mirror must not break the ledger
                log.error("%s.%s failed: %s", m.name, hook, e)
                self.echo(f"WARNING: {m.name} {hook} failed: {e}")

    def daily_pnl(self, now: datetime) -> float:
        today = to_ist(now).date()
        return self.broker.realized_pnl(today) + sum(p.last_mtm for p in self.broker.open_positions())

    def daily_loss_limit(self) -> float:
        return self.risk.capital * self.risk.daily_loss_limit_pct / 100.0

    # ---- exits ------------------------------------------------------------
    def exit_reason(self, pos: Position, pnl: float, now: datetime) -> Optional[str]:
        now = to_ist(now)
        today, t = now.date(), now.time()
        if pos.stop_loss is not None and pnl <= pos.stop_loss:
            return "STOP_LOSS"
        if pos.target is not None and pnl >= pos.target:
            return "TARGET"
        if any(leg.contract.expiry < today for leg in pos.legs):
            return "EXPIRED"
        if t >= self.risk.square_off_t:
            if self.risk.intraday:
                return "SQUARE_OFF"
            if any(leg.contract.expiry == today for leg in pos.legs):
                return "EXPIRY_DAY"
        if self.daily_pnl(now) <= -self.daily_loss_limit():
            return "DAILY_LOSS_LIMIT"
        # Positional trades opened on an earlier day are squared off if they have outlived their session
        if self.risk.intraday and datetime.fromisoformat(pos.entry_time).date() < today:
            return "SQUARE_OFF"
        return None

    def manage_exits(self, now: datetime) -> list[Position]:
        closed = []
        for pos in self.broker.open_positions():
            try:
                pnl = self.broker.mark(pos)
            except Exception as e:  # noqa: BLE001
                self.echo(f"WARNING: could not price position {pos.id}: {e}")
                continue
            reason = self.exit_reason(pos, pnl, now)
            if reason:
                self.broker.close(pos, reason)
                self._mirror("on_exit", pos)
                self._journal("exit", pos.to_dict())
                closed.append(pos)
        self.broker.save()
        return closed

    # ---- entries ----------------------------------------------------------
    def entry_block_reason(self, underlying: str, now: datetime) -> Optional[str]:
        now = to_ist(now)
        t = now.time()
        if t >= self.risk.no_new_entries_after_t:
            return f"no new entries after {self.risk.no_new_entries_after}"
        if len(self.broker.open_positions()) >= self.risk.max_open_positions:
            return "max open positions reached"
        if self.broker.open_positions(underlying):
            return "already holding a position"
        if self.daily_pnl(now) <= -self.daily_loss_limit():
            return "daily loss limit hit"
        cooldown = timedelta(minutes=self.risk.entry_cooldown_minutes)
        for p in self.broker.closed_positions():
            if p.underlying == underlying and p.exit_time and now - datetime.fromisoformat(p.exit_time) < cooldown:
                return "cooling down after last exit"
        return None

    def tick(self, underlyings: Iterable[str]) -> TickResult:
        now = self.data.now()
        res = TickResult()
        if not self.ignore_market_hours and not is_market_open(now):
            res.market_open = False
            return res

        res.exits = self.manage_exits(now)
        for u in underlyings:
            u = u.upper()
            why = self.entry_block_reason(u, now)
            if why:
                res.skipped[u] = why
                continue
            try:
                equity = self.broker.equity()
                sig = self.generator.generate(u, capital=min(equity, self.risk.capital))
            except Exception as e:  # noqa: BLE001 - data outages shouldn't kill the loop
                self.echo(f"WARNING: signal generation failed for {u}: {e}")
                res.skipped[u] = f"error: {e}"
                continue
            res.signals.append(sig)
            self._journal("signal", sig.to_dict())
            self._mirror("on_signal", sig)
            if not sig.is_trade:
                continue
            try:
                pos = self.broker.open(sig)
            except InsufficientMargin as e:
                self.echo(f"{u}: skipped {sig.strategy} - {e}")
                continue
            self._mirror("on_entry", pos, sig)
            self.broker.save()
            self._journal("entry", pos.to_dict())
            res.entries.append(pos)
        return res

    def run(self, underlyings: list[str], every_seconds: int = 300, max_ticks: Optional[int] = None) -> None:
        """Poll forever (Ctrl-C to stop). Sleeps while the market is closed."""
        ticks = 0
        self.echo(f"Paper trading {', '.join(underlyings)} every {every_seconds}s via {self.data.name} data. Ctrl-C to stop.")
        while max_ticks is None or ticks < max_ticks:
            res = self.tick(underlyings)
            ticks += 1
            now = to_ist(self.data.now())
            if not res.market_open:
                self.echo(f"[{now:%H:%M}] market closed - waiting")
            else:
                open_pos = self.broker.open_positions()
                mtm = sum(p.last_mtm for p in open_pos)
                skipped = "; ".join(f"{k}: {v}" for k, v in res.skipped.items())
                self.echo(
                    f"[{now:%H:%M}] open {len(open_pos)} | MTM Rs {mtm:,.0f} | "
                    f"today Rs {self.daily_pnl(now):,.0f}" + (f" | {skipped}" if skipped else "")
                )
            if max_ticks is not None and ticks >= max_ticks:
                break
            _time.sleep(every_seconds)
