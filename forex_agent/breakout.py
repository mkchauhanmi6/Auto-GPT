"""London-open breakout, forward test on the MT5 demo run as a FundedNext 2-Step challenge.

    python -m forex_agent.breakout run [--dry-run]   # does what the current UTC time needs
    python -m forex_agent.breakout status            # orders, positions, challenge ledger

Rules (identical to intraday.london_breakout with the 20:00 time exit; tested 2010-2026):
- Symbols EURUSD, GBPUSD, USDJPY, EURJPY, GBPJPY, XAUUSD. Weekdays only, times in UTC.
- Asian range = high/low of the 00:00-06:59 hourly bars. Trade only if the range is
  0.2-1.0 x daily ATR (hourly ATR(120) x sqrt(24) at the 06:00 bar).
- From 07:00: buy-stop at the range high, sell-stop at the range low, each with its stop at
  the other side of the range. Orders expire at 12:00. The first fill wins: the other order
  is cancelled (if both fill before a check, the later position is closed).
- Every position is closed at 20:00, before the daily rollover, so no swap is paid.
- If the range was already broken before the orders could be placed, skip that symbol today.

Sizing and FundedNext emulation:
- Each trade risks RISK_PCT of the challenge's initial balance (FundedNext limits are
  measured on the initial balance). The demo charges no commission, so the ledger deducts
  FN_COMMISSION per lot round trip (worst case quoted for FundedNext) from every trade.
- journal/fn_ledger.json tracks the virtual challenge: phase, 8%/5% targets, 5 trading
  days, 5% daily and 10% max loss on commission-adjusted equity. A failed attempt is
  recorded and a new Phase 1 starts the next trading day; the forward test keeps going.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import pathlib

import pandas as pd

from . import prop
from .broker import session
from .config import MAGIC, broker_symbol

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "XAUUSD"]
RISK_PCT = float(os.environ.get("LB_RISK_PCT", "0.25"))
FN_COMMISSION = float(os.environ.get("FN_COMMISSION", "7"))   # USD per lot, round trip
RANGE_MIN, RANGE_MAX = 0.2, 1.0
LATE_MAX = 0.25        # max distance past the level for a late market entry, x range
ENTRY_FROM, ENTRY_UNTIL, EXIT_AT = 7, 12, 20                   # UTC hours
TAG = "LB"
DIR = pathlib.Path(__file__).parent / "journal"
STATE = DIR / "breakout_state.json"
LEDGER = DIR / "fn_ledger.json"
JOURNAL = DIR / "trades.jsonl"
DAILY_LOSS, MAX_LOSS, TARGETS, MIN_DAYS = 5.0, 10.0, {1: 8.0, 2: 5.0}, 5


def load(p: pathlib.Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def save(p: pathlib.Path, obj) -> None:
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, default=str))


DRY = False


def journal(event: dict) -> None:
    if DRY:
        return
    JOURNAL.parent.mkdir(exist_ok=True)
    with JOURNAL.open("a") as f:
        f.write(json.dumps({"time": dt.datetime.now(dt.timezone.utc).isoformat(), **event},
                           default=str) + "\n")


def fn_day(t: dt.datetime) -> str:
    """FundedNext trading day (server midnight GMT+3 = 21:00 UTC)."""
    return (t + dt.timedelta(hours=3)).date().isoformat()


def asian_setup(bars: pd.DataFrame, day: pd.Timestamp) -> dict:
    """Range and filter for `day` from closed hourly bars (UTC, stamped at bar open)."""
    asia = bars[(bars.index >= day) & (bars.index < day + pd.Timedelta(hours=ENTRY_FROM))]
    if len(asia) < 6:
        return {"ok": False, "why": f"only {len(asia)} Asian bars"}
    pc = bars["close"].shift()
    tr = pd.concat([bars.high - bars.low, (bars.high - pc).abs(), (bars.low - pc).abs()],
                   axis=1).max(axis=1)
    daily_atr = tr.rolling(120).mean().loc[asia.index[-1]] * math.sqrt(24)
    hi, lo = float(asia["high"].max()), float(asia["low"].min())
    ratio = (hi - lo) / daily_atr
    ok = RANGE_MIN <= ratio <= RANGE_MAX
    return {"ok": ok, "hi": hi, "lo": lo, "range_atr": round(ratio, 3),
            "why": "" if ok else f"range {ratio:.2f} x ATR outside {RANGE_MIN}-{RANGE_MAX}"}


async def closed_bars(b, sym: str, now: dt.datetime) -> pd.DataFrame:
    c = await b.candles(broker_symbol(sym), "1h", 200)
    df = pd.DataFrame(c)
    df.index = pd.to_datetime(df["time"], utc=True)
    df = df[["open", "high", "low", "close"]].sort_index()
    return df[df.index + pd.Timedelta(hours=1) <= now]          # drop the forming bar


def lb_positions(positions: list[dict]) -> list[dict]:
    return [p for p in positions if str(p.get("comment", "")).startswith(TAG)]


async def run(dry: bool) -> dict:
    global DRY
    DRY = dry
    now = dt.datetime.now(dt.timezone.utc)
    today = pd.Timestamp(now.date(), tz="UTC")
    dkey = now.date().isoformat()
    state = load(STATE, {})
    day_state = state.setdefault(dkey, {})
    actions: list[dict] = []
    async with session() as b:
        info = await b.info()
        ledger = await update_ledger(b, info, now)
        positions = lb_positions(await b.positions())
        orders = [o for o in await b.orders() if str(o.get("comment", "")).startswith(TAG)]

        # 1. Fills: record slippage, cancel the opposite order, close a second fill.
        for sym in SYMBOLS:
            st = day_state.get(sym, {})
            pos = sorted([p for p in positions if p["symbol"] == broker_symbol(sym)],
                         key=lambda p: p["time"])
            for p in pos:
                if p["id"] not in st.get("seen", []):
                    side = 1 if p["type"].endswith("BUY") else -1
                    level = st.get("hi") if side > 0 else st.get("lo")
                    slip = side * (p["openPrice"] - level) if level else None
                    journal({"event": "filled", "symbol": sym, "id": p["id"], "side": side,
                             "level": level, "fill": p["openPrice"], "slippage_price": slip,
                             "lots": p["volume"]})
                    st.setdefault("seen", []).append(p["id"])
                    ledger.setdefault("trading_days", [])
                    if dkey not in ledger["trading_days"]:
                        ledger["trading_days"].append(dkey)
            if pos:
                for o in [o for o in orders if o["symbol"] == broker_symbol(sym)]:
                    actions.append({"cancel_opposite": sym, "order": o["id"]})
                    if not dry:
                        await b.cancel(o["id"])
                for p in pos[1:]:
                    actions.append({"close_second_fill": sym, "id": p["id"]})
                    if not dry:
                        await b.close(p["id"])
            day_state[sym] = st

        # 2. Time exit at 20:00, and anything left from an earlier day.
        for p in positions:
            opened = pd.Timestamp(p["time"]).tz_convert("UTC")
            if now.hour >= EXIT_AT or opened.date() < now.date():
                actions.append({"time_exit": p["symbol"], "id": p["id"]})
                if not dry:
                    await b.close(p["id"])
        if now.hour >= ENTRY_UNTIL:
            for o in orders:
                actions.append({"cancel_expired": o["symbol"], "order": o["id"]})
                if not dry:
                    await b.cancel(o["id"])

        # 3. New orders between 07:00 and 12:00 on weekdays.
        guard = await prop_guard(b, info)
        if now.weekday() < 5 and ENTRY_FROM <= now.hour < ENTRY_UNTIL:
            if not guard["ok"]:
                actions.append({"no_new_orders": guard["why"]})
            else:
                for sym in SYMBOLS:
                    if day_state.get(sym, {}).get("status"):
                        continue
                    day_state[sym] = await setup_symbol(b, sym, now, today, ledger, dry)
                    actions.append({sym: {k: v for k, v in day_state[sym].items() if k != "seen"}})
    if not dry:
        save(STATE, {k: v for k, v in state.items() if k >= (now.date() - dt.timedelta(days=10)).isoformat()})
        save(LEDGER, ledger)
    return {"time": now.isoformat(timespec="minutes"), "dry_run": dry, "actions": actions,
            "guard": guard, "ledger": summary(ledger)}


async def setup_symbol(b, sym, now, today, ledger, dry) -> dict:
    bars = await closed_bars(b, sym, now)
    s = asian_setup(bars, today)
    if not s["ok"]:
        journal({"event": "skip", "symbol": sym, "why": s["why"]})
        return {"status": "skipped", **s}
    since = bars[bars.index >= today + pd.Timedelta(hours=ENTRY_FROM)]
    px = await b.price(broker_symbol(sym))
    hi_seen = max([px["ask"]] + list(since["high"]))
    lo_seen = min([px["bid"]] + list(since["low"]))
    risk_money = RISK_PCT / 100 * ledger["initial"]
    if hi_seen >= s["hi"] or lo_seen <= s["lo"]:
        return await late_entry(b, sym, s, px, hi_seen, lo_seen, risk_money, dry)
    lots = await b.lots_for_risk(broker_symbol(sym), s["hi"] - s["lo"], risk_money)
    if lots <= 0:
        return {"status": "skipped", **s, "why": "below minimum lot size"}
    expires = (today + pd.Timedelta(hours=ENTRY_UNTIL)).to_pydatetime()
    out = {"status": "placed", **s, "lots": lots}
    if not dry:
        buy = await b.place_stop(broker_symbol(sym), "buy", lots, s["hi"], s["lo"], expires,
                                 f"{TAG} {sym} {now:%m%d}")
        sell = await b.place_stop(broker_symbol(sym), "sell", lots, s["lo"], s["hi"], expires,
                                  f"{TAG} {sym} {now:%m%d}")
        out.update(buy_order=buy.get("orderId"), sell_order=sell.get("orderId"))
    journal({"event": "orders" if not dry else "orders_dry_run", "symbol": sym, **out,
             "risk_money": round(risk_money, 2)})
    return out


async def late_entry(b, sym, s, px, hi_seen, lo_seen, risk_money, dry) -> dict:
    """The range broke before the orders could rest (e.g. in the minutes after 07:00). The
    backtest would be in at the level, so enter at market if price is still within
    LATE_MAX x range of it; the extra distance is recorded as slippage. Both sides broken
    = whipsaw, skipped as in the backtest."""
    rng = s["hi"] - s["lo"]
    if hi_seen >= s["hi"] and lo_seen <= s["lo"]:
        journal({"event": "skip", "symbol": sym, "why": "both sides broken before orders"})
        return {"status": "missed", **s}
    side = 1 if hi_seen >= s["hi"] else -1
    level, stop = (s["hi"], s["lo"]) if side > 0 else (s["lo"], s["hi"])
    entry = px["ask"] if side > 0 else px["bid"]
    late = side * (entry - level)
    if late > LATE_MAX * rng or side * (entry - stop) <= 0:
        journal({"event": "skip", "symbol": sym, "why": f"broke before orders, now {late / rng:.2f} x range past the level"})
        return {"status": "missed", **s}
    lots = await b.lots_for_risk(broker_symbol(sym), side * (entry - stop), risk_money)
    if lots <= 0:
        return {"status": "skipped", **s, "why": "below minimum lot size"}
    out = {"status": "entered_late", **s, "lots": lots, "side": side, "late_by": round(late, 6)}
    if not dry:
        res = await b.open(broker_symbol(sym), "buy" if side > 0 else "sell", lots, stop, None,
                           f"{TAG} {sym} {dt.datetime.now(dt.timezone.utc):%m%d}")
        out["result"] = res.get("stringCode")
    journal({"event": "late_entry" if not dry else "late_entry_dry_run", "symbol": sym, **out,
             "risk_money": round(risk_money, 2)})
    return out


async def prop_guard(b, info) -> dict:
    """FundedNext safety from prop.py: no new risk after a kill switch, with unprotected
    positions, or if all stops hitting could breach a firm floor."""
    p = prop.profile()
    if p is None:
        return {"ok": True, "why": "no prop profile"}
    all_pos = await b.positions(ours_only=False)
    risks = {}
    for x in all_pos:
        spec = await b.spec(x["symbol"])
        risks[x["id"]] = prop.position_risk(x, spec)
    deals = await b.deals_since(prop.server_day_start(dt.datetime.now(dt.timezone.utc)),
                                ours_only=False)
    a = prop.assess(p, info, all_pos, risks, deals)
    why = ("kill switch" if a["kill_switch"] else
           "unprotected position" if a["unprotected_positions"] else
           "worst case below firm floor" if a["worst_case_shortfall"] > 0 else "")
    return {"ok": not why, "why": why, "equity": a["equity"],
            "worst_case_equity": a["worst_case_equity"], "firm_floor": a["firm_floor"]}


# ------------------------------------------------------------------ FundedNext ledger

async def update_ledger(b, info, now) -> dict:
    """Commission-adjusted virtual challenge. Uses this agent's LB deals since the ledger
    started; P&L = profit + swap + demo commission - FN_COMMISSION x lots (per round trip)."""
    led = load(LEDGER, None)
    if led is None:
        led = {"started": now.isoformat(), "initial": round(info["balance"], 2), "attempt": 1,
               "phase": 1, "phase_start": now.isoformat(), "phase_pnl_start": 0.0,
               "trading_days": [], "history": [], "day": fn_day(now), "day_start_pnl": 0.0}
        journal({"event": "ledger_start", **led})
    start = dt.datetime.fromisoformat(led["started"])
    # Closing deals (stop-loss hits, our closes) may not carry the magic number, so match
    # every deal to positions whose opening deal is ours and tagged LB.
    all_deals = [d for d in await b.deals_since(start, ours_only=False)
                 if d.get("type") in ("DEAL_TYPE_BUY", "DEAL_TYPE_SELL")]
    ours = {d.get("positionId") for d in all_deals
            if d.get("entryType") == "DEAL_ENTRY_IN" and d.get("magic") == MAGIC
            and str(d.get("comment", "")).startswith(TAG)}
    deals = [d for d in all_deals if d.get("positionId") in ours]
    closes = [d for d in deals if d.get("entryType") == "DEAL_ENTRY_OUT"]
    realized = sum(d.get("profit", 0) + d.get("swap", 0) + d.get("commission", 0) for d in deals)
    fn_comm = FN_COMMISSION * sum(d.get("volume", 0) for d in closes)
    floating = sum(p.get("profit", 0) + p.get("swap", 0)
                   for p in lb_positions(await b.positions()))
    open_comm = FN_COMMISSION * sum(p["volume"] for p in lb_positions(await b.positions()))
    pnl = realized - fn_comm                       # closed, commission-adjusted
    equity_pnl = pnl + floating - open_comm
    if fn_day(now) != led["day"]:
        led["day"], led["day_start_pnl"] = fn_day(now), pnl
    phase_pnl = pnl - led["phase_pnl_start"]
    phase_eq = equity_pnl - led["phase_pnl_start"]
    init = led["initial"]
    led.update(closed_pnl=round(pnl, 2), equity_pnl=round(equity_pnl, 2),
               commission_charged=round(fn_comm, 2), trades_closed=len(closes),
               phase_pnl_pct=round(100 * phase_pnl / init, 2),
               phase_equity_pct=round(100 * phase_eq / init, 2),
               day_pnl_pct=round(100 * (equity_pnl - led["day_start_pnl"]) / init, 2))
    days_in_phase = [d for d in led["trading_days"] if d >= led["phase_start"][:10]]
    fail = None
    if equity_pnl - led["day_start_pnl"] <= -DAILY_LOSS / 100 * init:
        fail = "daily loss 5%"
    elif phase_eq <= -MAX_LOSS / 100 * init:
        fail = "max loss 10%"
    if fail:
        end_phase(led, now, f"FAILED phase {led['phase']}: {fail}", pnl, phase_one=True)
    elif (phase_pnl >= TARGETS[led["phase"]] / 100 * init and len(days_in_phase) >= MIN_DAYS
          and not lb_positions(await b.positions())):
        if led["phase"] == 1:
            end_phase(led, now, "PASSED phase 1", pnl, phase_one=False)
        else:
            end_phase(led, now, "PASSED phase 2: would be funded", pnl, phase_one=True)
    return led


def end_phase(led, now, result, pnl, phase_one: bool) -> None:
    led["history"].append({"attempt": led["attempt"], "phase": led["phase"], "result": result,
                           "from": led["phase_start"], "to": now.isoformat(),
                           "phase_pnl_pct": led["phase_pnl_pct"]})
    journal({"event": "challenge", "result": result, "attempt": led["attempt"]})
    if phase_one:
        led["attempt"] += 1 if "FAILED" in result or "funded" in result else 0
        led["phase"] = 1
    else:
        led["phase"] = 2
    led["phase_start"], led["phase_pnl_start"] = now.isoformat(), pnl


def summary(led: dict) -> dict:
    keys = ["initial", "attempt", "phase", "phase_pnl_pct", "phase_equity_pct", "day_pnl_pct",
            "closed_pnl", "commission_charged", "trades_closed"]
    out = {k: led.get(k) for k in keys}
    out["trading_days_in_phase"] = len([d for d in led.get("trading_days", [])
                                        if d >= led["phase_start"][:10]])
    out["results"] = [h["result"] for h in led.get("history", [])]
    return out


async def status() -> dict:
    async with session() as b:
        info = await b.info()
        return {"balance": info["balance"], "equity": info["equity"],
                "positions": [{k: p.get(k) for k in ("symbol", "type", "volume", "openPrice",
                                                     "stopLoss", "profit", "comment")}
                              for p in lb_positions(await b.positions())],
                "orders": [{k: o.get(k) for k in ("symbol", "type", "volume", "openPrice",
                                                  "stopLoss", "comment")}
                           for o in await b.orders()],
                "ledger": summary(load(LEDGER, {"phase_start": "9999"}))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "status"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out = asyncio.run(run(a.dry_run) if a.cmd == "run" else status())
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
