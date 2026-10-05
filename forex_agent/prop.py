"""FundedNext (CFD, Stellar) rule guard. Applied to every plan and every order.

Rules encoded (FundedNext help centre, checked 2026-10-05):
- Daily loss: equity (floating P&L, swaps and commissions included) must not fall more
  than X% of the INITIAL balance below the day's starting balance. The day resets at
  00:00 server time (GMT+2, or GMT+3 in summer). 2-Step 5%, 1-Step 3%, Lite 4%.
- Max loss: equity must never fall below initial x (1 - Y%). 2-Step 10%, 1-Step 6%, Lite 8%.
- Automation: EAs/bots are allowed only on MT4/MT5 accounts below $50,000, and only with
  FundedNext's approval (EA add-on). At $50,000 and above, trading must be fully manual.
- News: on funded Stellar accounts, trades opened or closed within 5 minutes of a
  high-impact event for the traded pair keep only 40% of their profit.
- Gambling: 70%+ cumulative margin use or all-in risk is forbidden.

Our own limits are stricter: by default the worst case (every stop hit, with 10% slippage)
may use at most half of the remaining daily or overall room.
"""
from __future__ import annotations

import datetime as dt
import os

PROFILES = {
    "fundednext_2step": {"daily_loss_pct": 5.0, "max_loss_pct": 10.0},
    "fundednext_1step": {"daily_loss_pct": 3.0, "max_loss_pct": 6.0},
    "fundednext_lite": {"daily_loss_pct": 4.0, "max_loss_pct": 8.0},
}
EA_MAX_ACCOUNT = 50_000
NEWS_WINDOW_MIN = 5          # firm's window; we use a wider one below
NEWS_AFTER_BUFFER_MIN = 15   # no opens/closes for this long after a high-impact event
MAX_MARGIN_USE = 0.30        # firm treats 70% as gambling; we stop at 30%
SLIPPAGE = 1.10              # assume stops fill 10% worse


def profile() -> dict | None:
    # On by default: the user's target is a FundedNext Stellar 2-Step account (<= $15k).
    name = os.environ.get("PROP_PROFILE", "fundednext_2step")
    if name == "none":
        return None
    if name not in PROFILES:
        raise SystemExit(f"Unknown PROP_PROFILE {name}; use one of {list(PROFILES)} or none")
    initial = os.environ.get("PROP_INITIAL_BALANCE")
    if not initial:
        raise SystemExit("PROP_INITIAL_BALANCE must be set when PROP_PROFILE is set")
    return {
        "name": name,
        **PROFILES[name],
        "initial": float(initial),
        "buffer": float(os.environ.get("PROP_BUFFER", "0.5")),
        "ea_approved": os.environ.get("PROP_EA_APPROVED") == "1",
    }


def automation_allowed(p: dict | None) -> tuple[bool, str]:
    if p is None:
        return True, "no prop profile"
    if p["initial"] >= EA_MAX_ACCOUNT:
        return False, (f"FundedNext accounts of ${EA_MAX_ACCOUNT:,}+ must be traded manually: "
                       "this run only prints the plan for you to place by hand")
    if not p["ea_approved"]:
        return False, ("FundedNext requires approval (EA add-on) before automated trading; "
                       "set PROP_EA_APPROVED=1 only after you have it")
    return True, "automation allowed (<$50k, EA approval confirmed)"


def server_day_start(now: dt.datetime) -> dt.datetime:
    """Start of the FundedNext trading day. The server runs at GMT+2 or GMT+3, so we use
    21:00 UTC (midnight GMT+3), the earlier of the two possible boundaries."""
    start = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return start if start <= now else start - dt.timedelta(days=1)


def position_risk(pos: dict, spec: dict) -> float | None:
    """Money lost from the current price to the stop (None if the position has no stop)."""
    sl = pos.get("stopLoss")
    if not sl:
        return None
    buy = pos["type"].endswith("BUY")
    dist = (pos["currentPrice"] - sl) if buy else (sl - pos["currentPrice"])
    ticks = dist / spec["tickSize"]
    return max(ticks * pos["currentTickValue"] * pos["volume"], 0.0) * SLIPPAGE


def assess(p: dict, info: dict, all_positions: list[dict], risks: dict, deals_today: list[dict]) -> dict:
    """Floors, remaining room and how much new risk may still be added."""
    trades = [d for d in deals_today if d.get("type") in ("DEAL_TYPE_BUY", "DEAL_TYPE_SELL")]
    realized = sum(d.get("profit", 0) + d.get("swap", 0) + d.get("commission", 0) for d in trades)
    day_start_balance = info["balance"] - realized
    daily_amt = p["initial"] * p["daily_loss_pct"] / 100
    max_amt = p["initial"] * p["max_loss_pct"] / 100
    day_floor = day_start_balance - daily_amt
    max_floor = p["initial"] - max_amt
    unprotected = [x["symbol"] for x in all_positions if risks.get(x["id"]) is None]
    open_risk = sum(r for r in risks.values() if r is not None)
    worst = info["equity"] - open_risk
    # Our own floors: keep (1 - buffer) of each firm limit untouched even in the worst case.
    safe_day = day_floor + (1 - p["buffer"]) * daily_amt
    safe_max = max_floor + (1 - p["buffer"]) * max_amt
    safe_floor = max(safe_day, safe_max)
    # Emergency: 75% of either firm limit already consumed by current equity.
    kill = info["equity"] <= max(day_floor + 0.25 * daily_amt, max_floor + 0.25 * max_amt)
    margin_use = info.get("margin", 0) / info["equity"] if info["equity"] else 1.0
    firm_floor = max(day_floor, max_floor)
    return {
        "firm_floor": round(firm_floor, 2),
        # Positive = all stops hitting would breach the firm's floor by this much.
        "worst_case_shortfall": round(max(firm_floor - worst, 0.0), 2),
        "profile": p["name"], "initial_balance": p["initial"],
        "day_start_balance": round(day_start_balance, 2),
        "firm_daily_floor": round(day_floor, 2), "firm_max_floor": round(max_floor, 2),
        "our_safe_floor": round(safe_floor, 2), "equity": info["equity"],
        "open_risk_to_stops": round(open_risk, 2), "worst_case_equity": round(worst, 2),
        "new_risk_allowed": round(max(worst - safe_floor, 0.0), 2),
        "margin_use": round(margin_use, 3), "margin_ok": margin_use < MAX_MARGIN_USE,
        "unprotected_positions": unprotected, "kill_switch": kill,
    }


def risk_per_trade_pct(p: dict | None, configured: float, max_open: int) -> float:
    """Equal risk per leg such that all legs together use at most buffer x daily limit."""
    if p is None:
        return configured
    return min(configured, p["buffer"] * p["daily_loss_pct"] / max_open)


def news_window(events: list[dict], now: dt.datetime) -> list[dict]:
    """High-impact events that make opening/closing now unsafe for reward share:
    starting within 5 minutes, or released less than NEWS_AFTER_BUFFER_MIN ago."""
    lo = now - dt.timedelta(minutes=NEWS_AFTER_BUFFER_MIN)
    hi = now + dt.timedelta(minutes=NEWS_WINDOW_MIN)
    return [e for e in events if lo <= e["time"] <= hi]
