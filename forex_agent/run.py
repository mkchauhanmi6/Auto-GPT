"""Daily runner for the G10 carry strategy with per-leg stops (see README for evidence).

    python -m forex_agent.run status
    python -m forex_agent.run plan    [--offline] [--rate JPY=1.5 ...]
    python -m forex_agent.run execute [--rate JPY=1.5 ...] [--dry-run]
    python -m forex_agent.run close-all --reason "..."   (closes this agent's trades only)

Rules (identical to portfolio_backtest.daily_carry_with_stops, stop_atr=3):
- Rank the 8 currencies (USD incl.) by central-bank policy rate. Long the top 3, short the
  bottom 3; each non-USD currency is traded through its USD pair.
- Each leg: market order, stop 3 x daily ATR(14), no target, sized so the stop loses
  RISK['risk_per_trade_pct'] of balance.
- A leg stopped out stays flat until next month. A leg whose currency leaves the long or
  short group (e.g. after a rate decision) is closed.
- Guardrails: no new legs within the news blackout of a high-impact event for either
  currency, after the daily loss limit is hit, or beyond max_open_trades.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import io
import json
import pathlib
import urllib.request

import pandas as pd

from . import prop
from .config import RISK, broker_symbol
from .data import BIS_AREAS, UA, pair_events, upcoming_news, yahoo_candles
from .portfolio_backtest import VS_USD, carry_weights

STOP_ATR = 3.0
JOURNAL = pathlib.Path(__file__).parent / "journal" / "trades.jsonl"


def latest_policy_rates(overrides: dict[str, float]) -> tuple[pd.Series, dict]:
    areas = "+".join(BIS_AREAS.values())
    url = (f"https://stats.bis.org/api/v1/data/WS_CBPOL/D.{areas}/all"
           "?lastNObservations=1&format=csv")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        raw = pd.read_csv(io.StringIO(r.read().decode()))
    inv = {v: k for k, v in BIS_AREAS.items()}
    rates = pd.Series({inv[a]: v for a, v in zip(raw["REF_AREA"], raw["OBS_VALUE"])})
    asof = {inv[a]: t for a, t in zip(raw["REF_AREA"], raw["TIME_PERIOD"])}
    for ccy, val in overrides.items():
        rates[ccy], asof[ccy] = val, "override"
    return rates, asof


def atr(df: pd.DataFrame, n: int = 14) -> float:
    prev = df["close"].shift()
    tr = pd.concat([df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def target_legs(rates: pd.Series) -> dict[str, str]:
    """{pair: 'buy'|'sell'} for every leg the carry ranking wants open."""
    legs = {}
    for ccy, w in carry_weights(rates).items():
        if w:
            pair, sign = VS_USD[ccy]
            legs[pair] = "buy" if w * sign > 0 else "sell"
    return legs


def journal(entry: dict) -> None:
    JOURNAL.parent.mkdir(exist_ok=True)
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **entry}
    with JOURNAL.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


async def broker_atr(b, symbol: str) -> float:
    candles = await b.candles(symbol, "1d", 60)
    df = pd.DataFrame(candles).sort_values("time")
    today = dt.datetime.now(dt.timezone.utc).date()
    df = df[pd.to_datetime(df["time"], utc=True).dt.date < today]  # closed bars only
    return atr(df)


def side_of(position: dict) -> str:
    return "buy" if position["type"].endswith("BUY") else "sell"


async def build_plan(b, rates: pd.Series) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    targets = target_legs(rates)
    info = await b.info()
    positions = await b.positions()
    open_by_pair = {p["symbol"]: p for p in positions}
    deals = await b.deals_since(month_start)
    closed_this_month = {d["symbol"] for d in deals if d.get("entryType") == "DEAL_ENTRY_OUT"}
    today_pnl = sum(d.get("profit", 0) + d.get("swap", 0) + d.get("commission", 0)
                    for d in deals if pd.Timestamp(d["time"]).date() == now.date())
    today_pnl += sum(p.get("unrealizedProfit", p.get("profit", 0)) for p in positions)
    loss_limit_hit = today_pnl <= -info["balance"] * RISK["daily_loss_limit_pct"] / 100

    pp = prop.profile()
    guard = None
    if pp:
        all_pos = await b.positions(ours_only=False)
        risks = {x["id"]: prop.position_risk(x, await b.spec(x["symbol"])) for x in all_pos}
        deals_today = await b.deals_since(prop.server_day_start(now), ours_only=False)
        guard = prop.assess(pp, info, all_pos, risks, deals_today)
        if guard["worst_case_shortfall"] > 0 and not guard["kill_switch"]:
            # Close our riskiest legs until every stop hitting could no longer breach the firm floor.
            need, actions = guard["worst_case_shortfall"], []
            for x in sorted(positions, key=lambda x: -(risks.get(x["id"]) or 0)):
                if need <= 0:
                    break
                actions.append({"action": "close", "symbol": x["symbol"], "position_id": x["id"],
                                "reason": "worst case (all stops hit) would breach the FundedNext floor"})
                need -= risks.get(x["id"]) or 0
            return {"time": now.isoformat(timespec="seconds"), "balance": info["balance"],
                    "equity": info["equity"], "prop_guard": guard, "actions": actions}
        if guard["kill_switch"]:
            actions = [{"action": "close", "symbol": x["symbol"], "position_id": x["id"],
                        "reason": "EMERGENCY: 75% of a FundedNext loss limit used"}
                       for x in positions]
            return {"time": now.isoformat(timespec="seconds"), "balance": info["balance"],
                    "equity": info["equity"], "prop_guard": guard, "actions": actions}

    def in_news_window(pair: str) -> str | None:
        if not pp:
            return None
        try:
            hits = prop.news_window(pair_events(pair), now)
        except Exception as e:
            return f"calendar unavailable ({e})"
        return "; ".join(f"{e['currency']} {e['title']} {e['time']}" for e in hits) or None

    actions = []
    for sym, p in open_by_pair.items():
        pair = sym[:6]
        want = targets.get(pair)
        if want != side_of(p):
            news = in_news_window(pair)
            if news:
                actions.append({"action": "skip", "symbol": sym,
                                "reason": f"close deferred, FundedNext news window: {news}"})
                continue
            actions.append({"action": "close", "symbol": sym, "position_id": p["id"],
                            "reason": f"carry ranking now wants {want or 'no position'}"})
    n_open = len(open_by_pair) - sum(a["action"] == "close" for a in actions)
    for pair, side in targets.items():
        sym = broker_symbol(pair)
        if sym in open_by_pair and side_of(open_by_pair[sym]) == side:
            actions.append({"action": "hold", "symbol": sym, "side": side})
            continue
        skip = None
        if sym in closed_this_month:
            skip = "leg closed earlier this month; flat until next month"
        elif loss_limit_hit:
            skip = f"daily loss limit hit ({today_pnl:.2f})"
        elif n_open >= RISK["max_open_trades"]:
            skip = "max open trades reached"
        elif guard and guard["unprotected_positions"]:
            skip = f"positions without a stop-loss: {guard['unprotected_positions']}"
        elif guard and not guard["margin_ok"]:
            skip = f"margin use {guard['margin_use']:.0%} too high"
        else:
            news = upcoming_news(pair, RISK["news_blackout_hours"], now)
            if news:
                skip = "news blackout: " + "; ".join(f"{e['currency']} {e['title']} {e['time']}" for e in news)
            elif in_news_window(pair):
                skip = "FundedNext news window: " + in_news_window(pair)
        if skip:
            actions.append({"action": "skip", "symbol": sym, "side": side, "reason": skip})
            continue
        a = await broker_atr(b, sym)
        px = await b.price(sym)
        entry = px["ask"] if side == "buy" else px["bid"]
        stop_dist = STOP_ATR * a
        stop = entry - stop_dist if side == "buy" else entry + stop_dist
        risk_pct = prop.risk_per_trade_pct(pp, RISK["risk_per_trade_pct"], RISK["max_open_trades"])
        risk_money = info["balance"] * risk_pct / 100
        lots = await b.lots_for_risk(sym, stop_dist, risk_money)
        if lots == 0:
            actions.append({"action": "skip", "symbol": sym, "side": side,
                            "reason": "min lot size would exceed risk limit"})
            continue
        if guard:
            if risk_money * prop.SLIPPAGE > guard["new_risk_allowed"]:
                actions.append({"action": "skip", "symbol": sym, "side": side,
                                "reason": f"FundedNext guard: worst case would breach our safe floor "
                                          f"(room {guard['new_risk_allowed']})"})
                continue
            guard["new_risk_allowed"] = round(guard["new_risk_allowed"] - risk_money * prop.SLIPPAGE, 2)
        digits = (await b.spec(sym)).get("digits", 5)
        actions.append({"action": "open", "symbol": sym, "side": side, "lots": lots,
                        "ref_price": entry, "stop": round(stop, digits), "atr": a,
                        "risk_money": round(risk_money, 2),
                        "reason": f"carry: {pair[:3]} {rates[pair[:3]]}% vs {pair[3:]} {rates[pair[3:]]}%"})
        n_open += 1
    return {"time": now.isoformat(timespec="seconds"), "balance": info["balance"],
            "equity": info["equity"], "currency": info.get("currency"),
            "today_pnl": round(today_pnl, 2), "rates": rates.to_dict(), "prop_guard": guard,
            "actions": actions}


def offline_plan(rates: pd.Series) -> dict:
    out = []
    for pair, side in target_legs(rates).items():
        df = yahoo_candles(pair)
        a = atr(df)
        news = upcoming_news(pair, RISK["news_blackout_hours"])
        out.append({"pair": pair, "side": side, "last_close": round(float(df.close.iloc[-1]), 5),
                    "stop_distance": round(STOP_ATR * a, 5),
                    "news_blackout": [f"{e['currency']} {e['title']} {e['time']}" for e in news]})
    return {"rates": rates.to_dict(), "legs": out}


async def main_async(args) -> None:
    overrides = dict((k, float(v)) for k, v in (r.split("=") for r in args.rate))
    rates, asof = latest_policy_rates(overrides)
    if args.cmd == "plan" and args.offline:
        print(json.dumps({"rates_asof": asof, **offline_plan(rates)}, indent=2, default=str))
        return
    from .broker import session

    async with session() as b:
        allowed, why = prop.automation_allowed(prop.profile())
        if args.cmd in ("execute", "close-all") and not allowed and not args.dry_run:
            print("AUTOMATION BLOCKED:", why)
            if args.cmd == "close-all":
                for p in await b.positions():
                    print(f"MANUAL ACTION: close {p['symbol']} position {p['id']} ({args.reason})")
                return
            args.dry_run = True  # still print the plan so it can be placed by hand
        if args.cmd == "close-all":
            if not args.reason:
                raise SystemExit("close-all needs --reason")
            for p in await b.positions():
                a = {"action": "close", "symbol": p["symbol"], "position_id": p["id"],
                     "reason": args.reason}
                try:
                    res = await b.close(p["id"])
                    journal({**a, "result": res})
                    print("DONE close", p["symbol"], res)
                except Exception as e:
                    journal({**a, "error": repr(e)})
                    print("FAILED close", p["symbol"], repr(e))
            return
        if args.cmd == "status":
            info = await b.info()
            pos = await b.positions(ours_only=False)
            print(json.dumps({"account": {k: info.get(k) for k in
                                          ("broker", "server", "currency", "balance", "equity",
                                           "margin", "freeMargin", "leverage", "type")},
                              "positions": [{k: p.get(k) for k in
                                             ("id", "symbol", "type", "volume", "openPrice",
                                              "currentPrice", "stopLoss", "takeProfit",
                                              "profit", "swap", "magic", "comment")} for p in pos]},
                             indent=2, default=str))
            return
        plan = await build_plan(b, rates)
        plan["rates_asof"] = asof
        print(json.dumps(plan, indent=2, default=str))
        if args.cmd != "execute" or args.dry_run:
            return
        for a in plan["actions"]:
            try:
                if a["action"] == "close":
                    res = await b.close(a["position_id"])
                elif a["action"] == "open":
                    res = await b.open(a["symbol"], a["side"], a["lots"], a["stop"], None, "carry")
                else:
                    continue
                journal({**a, "result": res})
                print("DONE", a["action"], a["symbol"], res)
            except Exception as e:  # keep going: one failed leg must not block the others
                journal({**a, "error": repr(e)})
                print("FAILED", a["action"], a["symbol"], repr(e))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "plan", "execute", "close-all"])
    ap.add_argument("--reason", default="")
    ap.add_argument("--offline", action="store_true", help="plan from free data, no broker")
    ap.add_argument("--rate", action="append", default=[], help="override a policy rate, CCY=x")
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
