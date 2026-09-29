"""Signal generation: market view (trend + volatility regime) -> defined-risk option structure.

Decision matrix (never sells naked options; every trade has a known max loss):

    trend \\ IV         CHEAP / FAIR             RICH
    strong bull        buy ATM CE               bull put credit spread
    mild bull          bull call debit spread   bull put credit spread
    neutral            no trade                 iron condor
    mild bear          bear put debit spread    bear call credit spread
    strong bear        buy ATM PE               bear call credit spread

This is a rules-based starting point for *paper trading*, not investment advice.
Measure it on paper for a few weeks before trusting it with money.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .config import RiskConfig, StrategyConfig
from .data.base import MarketData
from .indicators import ema, realized_vol, rsi, vwap
from .instruments import atm_strike, get_spec, round_to_step
from .models import Leg, OptionContract, OptionType, Side, Signal, SignalAction
from .payoff import analyse
from .pricing import time_to_expiry
from .timeutil import to_ist

MAX_SCORE = 4.5


@dataclass
class MarketView:
    underlying: str
    spot: float
    score: float
    bias: str  # BULLISH / BEARISH / NEUTRAL
    strength: str  # STRONG / MILD / ""
    iv: float
    rv: float
    iv_ratio: float
    vol_regime: str  # RICH / FAIR / CHEAP
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.strength} {self.bias}".strip()


class SignalGenerator:
    def __init__(self, data: MarketData, cfg: StrategyConfig, risk: RiskConfig):
        self.data = data
        self.cfg = cfg
        self.risk = risk

    # ------------------------------------------------------------------ view
    def analyse(self, underlying: str) -> Optional[MarketView]:
        cfg = self.cfg
        candles = self.data.candles(underlying, cfg.candle_minutes, days=5)
        if len(candles) < cfg.trend_ema + 5:
            return None
        closes = [c.close for c in candles]
        spot = self.data.spot(underlying)
        f, s, t = ema(closes, cfg.fast_ema), ema(closes, cfg.slow_ema), ema(closes, cfg.trend_ema)
        r = rsi(closes, cfg.rsi_period)[-1]
        notes: list[str] = []
        score = 0.0

        if f[-1] > s[-1]:
            score += 1
            notes.append(f"EMA{cfg.fast_ema} above EMA{cfg.slow_ema} (+1)")
        else:
            score -= 1
            notes.append(f"EMA{cfg.fast_ema} below EMA{cfg.slow_ema} (-1)")
        crossed_up = any(f[-i - 1] <= s[-i - 1] and f[-i] > s[-i] for i in range(1, 4))
        crossed_dn = any(f[-i - 1] >= s[-i - 1] and f[-i] < s[-i] for i in range(1, 4))
        if crossed_up:
            score += 0.5
            notes.append("fresh bullish EMA crossover (+0.5)")
        elif crossed_dn:
            score -= 0.5
            notes.append("fresh bearish EMA crossover (-0.5)")

        if closes[-1] > t[-1]:
            score += 1
            notes.append(f"price above EMA{cfg.trend_ema} trend (+1)")
        else:
            score -= 1
            notes.append(f"price below EMA{cfg.trend_ema} trend (-1)")

        if r >= cfg.rsi_bull:
            score += 1
            notes.append(f"RSI {r:.0f} bullish (+1)")
        elif r <= cfg.rsi_bear:
            score -= 1
            notes.append(f"RSI {r:.0f} bearish (-1)")
        else:
            notes.append(f"RSI {r:.0f} neutral (0)")

        today = to_ist(candles[-1].ts).date()
        session = [c for c in candles if to_ist(c.ts).date() == today]
        vw = vwap(session)
        if vw:
            if closes[-1] > vw:
                score += 1
                notes.append(f"price above VWAP {vw:.1f} (+1)")
            else:
                score -= 1
                notes.append(f"price below VWAP {vw:.1f} (-1)")

        daily = [c.close for c in self.data.daily_candles(underlying, 30)]
        rv = realized_vol(daily[-21:], 252)
        if rv <= 0:
            rv = realized_vol(closes, 252 * 375 / cfg.candle_minutes)
        iv = self.data.atm_iv(underlying)
        if not iv:
            iv = rv * 1.1
            notes.append("implied vol unknown; assuming 1.1x realised vol")
        rv = max(rv, 0.01)
        ratio = iv / rv
        if ratio >= cfg.iv_rich_ratio:
            regime = "RICH"
        elif ratio <= cfg.iv_cheap_ratio:
            regime = "CHEAP"
        else:
            regime = "FAIR"
        notes.append(f"IV {iv:.1%} vs 20d realised {rv:.1%} -> options {regime}")

        if score >= cfg.strong_score:
            bias, strength = "BULLISH", "STRONG"
        elif score >= cfg.mild_score:
            bias, strength = "BULLISH", "MILD"
        elif score <= -cfg.strong_score:
            bias, strength = "BEARISH", "STRONG"
        elif score <= -cfg.mild_score:
            bias, strength = "BEARISH", "MILD"
        else:
            bias, strength = "NEUTRAL", ""
        return MarketView(underlying, spot, score, bias, strength, iv, rv, ratio, regime, notes)

    # --------------------------------------------------------------- signals
    def pick_expiry(self, underlying: str) -> Optional[date]:
        today = self.data.now().date()
        for e in self.data.expiries(underlying):
            if (e - today).days >= self.cfg.min_days_to_expiry:
                return e
        return None

    def generate(self, underlying: str, capital: Optional[float] = None) -> Signal:
        underlying = underlying.upper()
        view = self.analyse(underlying)
        if view is None:
            return self._no_trade(underlying, 0.0, "Not enough candle history yet", "")
        expiry = self.pick_expiry(underlying)
        if expiry is None:
            return self._no_trade(underlying, view.spot, "No suitable expiry found", "", view)

        structure = self._choose_structure(view)
        if structure is None:
            return self._no_trade(
                underlying, view.spot, f"{view.bias} with {view.vol_regime} IV: no edge, stay flat", "", view
            )
        name, legs_spec, kind = structure
        return self._build(view, expiry, name, legs_spec, kind, capital or self.risk.capital)

    def _choose_structure(self, v: MarketView):
        """Returns (strategy name, [(side, type, strike_offset_in_steps or None)], kind)."""
        c = self.cfg
        w = c.spread_width_steps
        rich = v.vol_regime == "RICH"
        if v.bias == "NEUTRAL":
            if rich and c.allow_iron_condor:
                return "Iron Condor", "condor", "credit"
            return None
        bull = v.bias == "BULLISH"
        if rich:
            if bull:
                return "Bull Put Spread", [(Side.SELL, OptionType.PE, -1), (Side.BUY, OptionType.PE, -1 - w)], "credit"
            return "Bear Call Spread", [(Side.SELL, OptionType.CE, 1), (Side.BUY, OptionType.CE, 1 + w)], "credit"
        if v.strength == "STRONG" and c.allow_naked_long:
            opt = OptionType.CE if bull else OptionType.PE
            return f"Long {opt.value}", [(Side.BUY, opt, 0)], "long"
        if bull:
            return "Bull Call Spread", [(Side.BUY, OptionType.CE, 0), (Side.SELL, OptionType.CE, w)], "debit"
        return "Bear Put Spread", [(Side.BUY, OptionType.PE, 0), (Side.SELL, OptionType.PE, -w)], "debit"

    def _condor_legs(self, v: MarketView, expiry: date, step: float):
        t = time_to_expiry(expiry, self.data.now())
        move = v.spot * v.iv * math.sqrt(max(t, 1 / 365)) * self.cfg.condor_short_sd
        atm = atm_strike(v.spot, step)
        short_put = min(round_to_step(v.spot - move, step), atm - step)
        short_call = max(round_to_step(v.spot + move, step), atm + step)
        wing = self.cfg.condor_wing_steps * step
        return [
            (Side.BUY, OptionType.PE, short_put - wing),
            (Side.SELL, OptionType.PE, short_put),
            (Side.SELL, OptionType.CE, short_call),
            (Side.BUY, OptionType.CE, short_call + wing),
        ]

    def _build(self, v: MarketView, expiry: date, name: str, legs_spec, kind: str, capital: float) -> Signal:
        c = self.cfg
        spec = get_spec(v.underlying)
        step = spec.strike_step
        lot = self.data.lot_size(v.underlying)
        atm = atm_strike(v.spot, step)
        if legs_spec == "condor":
            strikes = self._condor_legs(v, expiry, step)
        else:
            strikes = [(side, opt, atm + off * step) for side, opt, off in legs_spec]

        contracts = [OptionContract(v.underlying, expiry, k, opt) for _, opt, k in strikes]
        quotes = self.data.option_quotes(contracts)
        legs = [
            Leg(ct, side, 1, lot, quotes[ct].ltp) for (side, _, _), ct in zip(strikes, contracts)
        ]
        legs.sort(key=lambda leg: leg.side is not Side.BUY)  # buy legs first (margin benefit)
        estimated = any(q.estimated for q in quotes.values())
        net = sum(leg.side.sign * leg.price for leg in legs)  # + debit / - credit, per unit

        if kind == "credit" and net >= 0:
            return self._no_trade(v.underlying, v.spot, f"{name}: no net credit available", name, v)
        if kind in ("debit", "long") and net <= 0:
            return self._no_trade(v.underlying, v.spot, f"{name}: invalid debit", name, v)

        per_lot = analyse(legs)
        if per_lot.max_loss is None or per_lot.max_loss <= 0:
            return self._no_trade(v.underlying, v.spot, f"{name}: undefined risk, skipped", name, v)

        # Size so that hitting the stop loses <= risk_per_trade_pct of capital, and the
        # worst case (gap through the stop, held to expiry) stays <= max_loss_per_trade_pct.
        stop_lot, _ = self._exits(kind, abs(net) * lot, per_lot.max_loss, per_lot.max_profit)
        stop_budget = capital * self.risk.risk_per_trade_pct / 100.0
        worst_budget = capital * self.risk.max_loss_per_trade_pct / 100.0
        lots = min(
            self.risk.max_lots,
            int(stop_budget // -stop_lot) if stop_lot < 0 else self.risk.max_lots,
            int(worst_budget // per_lot.max_loss),
        )
        if lots < 1:
            return self._no_trade(
                v.underlying,
                v.spot,
                f"{name}: 1 lot risks Rs {-stop_lot:,.0f} to stop / Rs {per_lot.max_loss:,.0f} worst case, "
                f"over budget (Rs {stop_budget:,.0f} / Rs {worst_budget:,.0f})",
                name,
                v,
            )
        for leg in legs:
            leg.lots = lots
        summary = analyse(legs)
        stop, target = self._exits(kind, abs(net) * lot * lots, summary.max_loss, summary.max_profit)

        confidence = self._confidence(v, kind)
        rationale = [f"View: {v.label} (score {v.score:+.1f}), IV {v.vol_regime}", *v.notes]
        if estimated:
            rationale.append("Premiums are Black-Scholes ESTIMATES - check live prices in Sensibull")
        if confidence < c.min_confidence:
            return self._no_trade(
                v.underlying, v.spot, f"{name}: confidence {confidence:.0%} below threshold", name, v
            )

        return Signal(
            action=SignalAction.ENTER,
            underlying=v.underlying,
            strategy=name,
            spot=v.spot,
            legs=legs,
            net_premium=round(net, 2),
            max_profit=summary.max_profit,
            max_loss=summary.max_loss,
            stop_loss=round(stop, 2),
            target=round(target, 2),
            breakevens=summary.breakevens,
            confidence=round(confidence, 2),
            rationale=rationale,
            estimated_prices=estimated,
            created_at=self.data.now(),
        )

    def _exits(
        self, kind: str, premium_total: float, max_loss: Optional[float], max_profit: Optional[float]
    ) -> tuple[float, float]:
        """(stop-loss P&L, target P&L) in rupees for a position whose net premium is ``premium_total``."""
        c = self.cfg
        if kind == "long":
            return -c.long_sl_pct * premium_total, c.long_target_pct * premium_total
        if kind == "debit":
            return -c.debit_spread_sl_pct * premium_total, c.debit_spread_target_pct * (max_profit or premium_total)
        return -min(c.credit_sl_mult * premium_total, max_loss or premium_total), c.credit_target_pct * premium_total

    @staticmethod
    def _confidence(v: MarketView, kind: str) -> float:
        if kind == "credit" and v.bias == "NEUTRAL":
            rich = min(1.0, max(0.0, (v.iv_ratio - 1.0) / 0.5))
            calm = 1.0 - min(1.0, abs(v.score) / 1.5)
            return min(0.95, 0.45 + 0.3 * rich + 0.2 * calm)
        base = min(1.0, abs(v.score) / MAX_SCORE)
        bonus = {"long": 0.15 if v.vol_regime == "CHEAP" else 0.0, "credit": 0.15, "debit": 0.05}[kind]
        return min(0.95, 0.4 + 0.5 * base + bonus)

    def _no_trade(
        self, underlying: str, spot: float, reason: str, strategy: str, view: Optional[MarketView] = None
    ) -> Signal:
        rationale = [reason]
        if view:
            rationale = [reason, f"View: {view.label} (score {view.score:+.1f})", *view.notes]
        return Signal(
            action=SignalAction.NO_TRADE,
            underlying=underlying,
            strategy=strategy or "-",
            spot=spot,
            rationale=rationale,
            created_at=self.data.now(),
        )
