"""CLI: python3 -m us_trader {status,run,log,review}

`run` applies the backtested rules (see config.py) and, with --execute, sends paper orders.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import config
from .alpaca import Alpaca
from .signals import NY, indicators

NOW = datetime.now(timezone.utc)
TRADES = os.path.join(config.JOURNAL_DIR, "trades.jsonl")
UNIVERSE = sorted(set(config.DIP_SYMBOLS) | {config.CORE_SYMBOL})


def load_state():
    if os.path.exists(config.STATE_FILE):
        return json.load(open(config.STATE_FILE))
    return {"core_on": False, "dip": {}, "peak_equity": 0.0, "halted": False}


def save_state(st):
    os.makedirs(config.JOURNAL_DIR, exist_ok=True)
    json.dump(st, open(config.STATE_FILE, "w"), indent=1, sort_keys=True)


def journal(event, **kw):
    os.makedirs(config.JOURNAL_DIR, exist_ok=True)
    with open(TRADES, "a") as f:
        f.write(json.dumps({"ts": NOW.isoformat(timespec="seconds"), "event": event, **kw}) + "\n")


def snapshot(alp, market_open):
    """Indicators for the universe; while the market is open the live price stands in for today's close."""
    return {s: indicators(s, alp.latest_price(s) if market_open else None) for s in UNIVERSE}


def cmd_status(args):
    alp, st = Alpaca(config.API_KEY, config.API_SECRET), load_state()
    acc, clock, pos = alp.account(), alp.clock(), alp.positions()
    eq, last = float(acc["equity"]), float(acc["last_equity"])
    print(f"Alpaca PAPER account: equity ${eq:,.2f} (today {eq - last:+,.2f}, {eq / last - 1:+.2%}) cash ${float(acc['cash']):,.2f}")
    print(f"Market {'OPEN' if clock['is_open'] else 'closed'}; next open {clock['next_open'][:16]} next close {clock['next_close'][:16]} (ET)")
    print(f"Peak equity ${st['peak_equity']:,.2f}; drawdown halt at -{config.MAX_DRAWDOWN_HALT:.0%}; halted={st['halted']}")
    print("Positions:")
    for s, p in sorted(pos.items()):
        tag = "" if s in UNIVERSE else "  [not managed]"
        print(f"  {s:5s} {float(p['qty']):>10.4f} @ {float(p['avg_entry_price']):>8.2f} now {float(p['current_price']):>8.2f} "
              f"value ${float(p['market_value']):>11,.2f} P&L {float(p['unrealized_pl']):+,.2f} ({float(p['unrealized_plpc']):+.2%}){tag}")
    print(f"Sleeves: core SPY {'ON' if st['core_on'] else 'off'}; dip-buys open: "
          + (", ".join(f"{s} since {v['entry_date']} @ {v['entry_price']}" for s, v in st["dip"].items()) or "none"))
    print("Signals (" + ("live price as today's close" if clock["is_open"] else "last close") + "):")
    for s, i in snapshot(alp, clock["is_open"]).items():
        print(f"  {s:4s} {i['price']:>8.2f} sma200 {i['sma200']:>8.2f} ({'above' if i['above_sma200'] else 'BELOW'}) "
              f"sma5 {i['sma5']:>8.2f} rsi2 {i['rsi2']:>5.1f}")


def cmd_run(args):
    alp, st = Alpaca(config.API_KEY, config.API_SECRET), load_state()
    clock, acc = alp.clock(), alp.account()
    if args.execute and not clock["is_open"]:
        sys.exit("REFUSED: market is closed. The rules were tested on end-of-day fills; run in the last 30 min of the session.")
    if st["halted"] and not args.resume:
        sys.exit("HALTED after the drawdown limit. Only the owner may resume (run --execute --resume).")
    st["halted"] = False
    eq = float(acc["equity"])
    st["peak_equity"] = max(st["peak_equity"], eq)
    pos = alp.positions()
    vetoes = {v.upper() for v in args.veto}
    if vetoes and len(args.reason or "") < 30:
        sys.exit("a veto needs --reason (>=30 chars) naming the news/fundamental cause")

    if eq < (1 - config.MAX_DRAWDOWN_HALT) * st["peak_equity"]:
        print(f"DRAWDOWN HALT: equity {eq:,.2f} is >{config.MAX_DRAWDOWN_HALT:.0%} below peak {st['peak_equity']:,.2f}")
        if args.execute:
            for s in UNIVERSE:
                if s in pos:
                    alp.close_position(s)
            st.update(halted=True, core_on=False, dip={})
            journal("halt", equity=eq, peak=st["peak_equity"])
            save_state(st)
        return

    ind = snapshot(alp, clock["is_open"])
    today = str(datetime.now(NY).date())
    notes = []
    core_on = ind[config.CORE_SYMBOL]["above_sma200"]
    if core_on != st["core_on"]:
        notes.append(f"core SPY {'ON' if core_on else 'OFF'} (price {ind['SPY']['price']} vs sma200 {ind['SPY']['sma200']})")
    dip = dict(st["dip"])
    changed = {config.CORE_SYMBOL} if core_on != st["core_on"] else set()
    for s in config.DIP_SYMBOLS:
        i = ind[s]
        if s in dip and i["price"] > i["sma5"]:
            notes.append(f"dip EXIT {s}: {i['price']} > sma5 {i['sma5']}")
            del dip[s]
            changed.add(s)
        elif s not in dip and i["above_sma200"] and i["rsi2"] < config.DIP_RSI_ENTRY:
            if s in vetoes:
                notes.append(f"dip ENTRY {s} VETOED: {args.reason}")
                if args.execute:
                    journal("veto", symbol=s, price=i["price"], rsi2=i["rsi2"], reason=args.reason)
                continue
            notes.append(f"dip ENTRY {s}: rsi2 {i['rsi2']} < {config.DIP_RSI_ENTRY}, above sma200 {i['sma200']}")
            dip[s] = {"entry_date": today, "entry_price": i["price"]}
            changed.add(s)

    orders = []
    for s in UNIVERSE:
        px = ind[s]["price"]
        target = eq * ((config.CORE_WEIGHT if s == config.CORE_SYMBOL and core_on else 0) + (config.DIP_WEIGHT if s in dip else 0))
        cur_qty = float(pos[s]["qty"]) if s in pos else 0.0
        diff = target - cur_qty * px
        if target == 0 and cur_qty:
            orders.append((s, "close", cur_qty, diff))
        elif abs(diff) > 1 and (s in changed or abs(diff) > config.REBALANCE_DRIFT * eq):
            orders.append((s, "buy" if diff > 0 else "sell", abs(diff) / px, diff))
    orders.sort(key=lambda o: o[1] == "buy")  # sells first to free cash

    print(f"Equity ${eq:,.2f}; core {'ON' if core_on else 'off'}; dip-buys {sorted(dip) or 'none'}")
    print("\n".join("  " + n for n in notes) or "  no signal changes")
    for s, side, qty, val in orders:
        print(f"  ORDER {side.upper():5s} {s} qty {qty:.4f} (~${abs(val):,.0f})")
    if not orders:
        print("  no orders needed")
    if not args.execute:
        print("(plan only; add --execute to send)")
        return
    for s, side, qty, val in orders:
        cid = f"ust-{today}-{s}-{side}"
        res = alp.close_position(s) if side == "close" else alp.market_order(s, side, qty, cid)
        journal("order", symbol=s, side=side, qty=round(qty, 4), value=round(val, 2), price=ind[s]["price"],
                order_id=(res or {}).get("id"), notes=[n for n in notes if s in n])
    st.update(core_on=core_on, dip=dip, last_run=NOW.isoformat(timespec="seconds"))
    save_state(st)
    print("sent", len(orders), "orders")


def cmd_log(args):
    os.makedirs(config.JOURNAL_DIR, exist_ok=True)
    path = os.path.join(config.JOURNAL_DIR, f"{NOW:%Y-%m-%d}.md")
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write(f"# Trading log {NOW:%Y-%m-%d}\n\n")
        f.write(f"## {NOW:%H:%M} UTC\n\n{args.text.strip()}\n\n")
    print("logged to", path)


def cmd_review(args):
    """Closed dip-buy round trips and vetoes from the journal."""
    if not os.path.exists(TRADES):
        sys.exit("no trades yet")
    recs = [json.loads(l) for l in open(TRADES)]
    opens, trips = {}, []
    for r in recs:
        if r["event"] != "order":
            continue
        entry = any("dip ENTRY" in n for n in r.get("notes", []))
        exit_ = any("dip EXIT" in n for n in r.get("notes", []))
        if entry:
            opens[r["symbol"]] = r
        elif exit_ and r["symbol"] in opens:
            o = opens.pop(r["symbol"])
            trips.append((r["symbol"], o["ts"][:10], r["ts"][:10], r["price"] / o["price"] - 1))
    for s, a, b, ret in trips:
        print(f"  {s} {a} -> {b} {ret:+.2%}")
    if trips:
        rets = [t[3] for t in trips]
        print(f"dip-buy round trips: {len(rets)}, win {sum(r > 0 for r in rets) / len(rets):.0%}, avg {sum(rets) / len(rets):+.2%}"
              f"  (backtest 2016-2026: win 70%, avg win +1.20%, avg loss -1.57%)")
    print("vetoes:", sum(r["event"] == "veto" for r in recs))


def main():
    ap = argparse.ArgumentParser(prog="us_trader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    r = sub.add_parser("run")
    r.add_argument("--execute", action="store_true")
    r.add_argument("--veto", action="append", default=[], help="skip a new dip-buy entry in SYMBOL")
    r.add_argument("--reason")
    r.add_argument("--resume", action="store_true")
    lg = sub.add_parser("log"); lg.add_argument("text")
    sub.add_parser("review")
    args = ap.parse_args()
    {"status": cmd_status, "run": cmd_run, "log": cmd_log, "review": cmd_review}[args.cmd](args)


if __name__ == "__main__":
    main()
