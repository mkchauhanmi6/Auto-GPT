"""CLI: python3 -m us_trader {status,run,log,review}

`run` applies the backtested rules (see config.py) and, with --execute, sends paper orders.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import config, earnings
from .alpaca import Alpaca
from .signals import NY, indicators
from .universe import SECTOR

NOW = datetime.now(timezone.utc)
TRADES = os.path.join(config.JOURNAL_DIR, "trades.jsonl")
ETFS = sorted(set(config.DIP_SYMBOLS) | {config.CORE_SYMBOL})
STOCKS = sorted(SECTOR)
UNIVERSE = ETFS + STOCKS


# ---------- state & journal ----------
def load_state(alp):
    if os.path.exists(config.STATE_FILE):
        st = json.load(open(config.STATE_FILE))
        st.setdefault("stocks", {})
        return st
    # No state file (first run, or a run whose push failed): rebuild it from the account so held
    # positions are not mistaken for strays and sold.
    pos, eq = alp.positions(), float(alp.account()["equity"])
    val = lambda s: float(pos[s]["market_value"]) if s in pos else 0.0
    core = val(config.CORE_SYMBOL) >= 0.5 * config.CORE_WEIGHT * eq
    dip = {}
    for s in config.DIP_SYMBOLS:
        v = val(s) - (config.CORE_WEIGHT * eq if s == config.CORE_SYMBOL and core else 0)
        if s in pos and v >= 0.5 * config.DIP_WEIGHT * eq:
            dip[s] = {"entry_date": "unknown", "entry_price": float(pos[s]["avg_entry_price"])}
    stocks = {s: {"entry_date": "unknown", "entry_price": float(pos[s]["avg_entry_price"])} for s in STOCKS if s in pos}
    hist = alp.portfolio_history()
    peak = max([float(x) for x in hist.get("equity") or [] if x] + [eq])
    if pos:
        print(f"[state rebuilt from account: core={'ON' if core else 'off'} dip={sorted(dip)} stocks={sorted(stocks)}]")
    return {"core_on": core, "dip": dip, "stocks": stocks, "peak_equity": peak, "halted": False}


def save_state(st):
    os.makedirs(config.JOURNAL_DIR, exist_ok=True)
    json.dump(st, open(config.STATE_FILE, "w"), indent=1, sort_keys=True)


def journal(event, **kw):
    os.makedirs(config.JOURNAL_DIR, exist_ok=True)
    with open(TRADES, "a") as f:
        f.write(json.dumps({"ts": NOW.isoformat(timespec="seconds"), "event": event, **kw}) + "\n")


def snapshot(alp, market_open, symbols=UNIVERSE):
    """Indicators per symbol; while the market is open the live price stands in for today's close."""
    live = alp.latest_prices(symbols) if market_open else {}

    def one(s):
        try:
            return s, indicators(s, live.get(s))
        except Exception as e:  # one bad data feed must not stop the run
            print(f"  [skip {s}: {str(e)[:80]}]")
            return s, None

    with ThreadPoolExecutor(8) as ex:
        return {s: i for s, i in ex.map(one, symbols) if i}


def is_dip_entry(i):
    return i["above_sma200"] and i["rsi2"] < config.DIP_RSI_ENTRY


# ---------- commands ----------
def cmd_status(args):
    alp = Alpaca(config.API_KEY, config.API_SECRET)
    st = load_state(alp)
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
    fmt = lambda d: ", ".join(f"{s} since {v['entry_date']} @ {v['entry_price']}" for s, v in d.items()) or "none"
    print(f"Sleeves: core SPY {'ON' if st['core_on'] else 'off'}; ETF dip-buys: {fmt(st['dip'])}")
    print(f"         stocks ({len(st['stocks'])}/{config.STOCK_SLOTS}): {fmt(st['stocks'])}")
    ind = snapshot(alp, clock["is_open"])
    print("ETF signals (" + ("live price as today's close" if clock["is_open"] else "last close") + "):")
    for s in ETFS:
        i = ind[s]
        print(f"  {s:4s} {i['price']:>8.2f} sma200 {i['sma200']:>8.2f} ({'above' if i['above_sma200'] else 'BELOW'}) "
              f"sma5 {i['sma5']:>8.2f} rsi2 {i['rsi2']:>5.1f}")
    cands = sorted((ind[s]["rsi2"], s) for s in STOCKS if s in ind and is_dip_entry(ind[s]))
    print("Stock dip candidates (above sma200, rsi2 < 10): "
          + (", ".join(f"{s} {r:.1f} ({SECTOR[s]})" for r, s in cands) or "none"))


def cmd_run(args):
    alp = Alpaca(config.API_KEY, config.API_SECRET)
    st = load_state(alp)
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
            st.update(halted=True, core_on=False, dip={}, stocks={})
            journal("halt", equity=eq, peak=st["peak_equity"])
            save_state(st)
        return

    ind = snapshot(alp, clock["is_open"])
    today = str(datetime.now(NY).date())
    notes, changed = [], set()

    def veto(s, what):
        notes.append(f"{what} {s} VETOED: {args.reason}")
        if args.execute:
            journal("veto", symbol=s, price=ind[s]["price"], rsi2=ind[s]["rsi2"], reason=args.reason)

    # core
    spy = ind[config.CORE_SYMBOL]
    core_on = spy["above_sma200"]
    if core_on != st["core_on"]:
        notes.append(f"core SPY {'ON' if core_on else 'OFF'} (price {spy['price']} vs sma200 {spy['sma200']})")
        changed.add(config.CORE_SYMBOL)

    # ETF dip-buys
    dip = dict(st["dip"])
    for s in config.DIP_SYMBOLS:
        i = ind[s]
        if s in dip and i["price"] > i["sma5"]:
            notes.append(f"dip EXIT {s}: {i['price']} > sma5 {i['sma5']}")
            del dip[s]
            changed.add(s)
        elif s not in dip and is_dip_entry(i):
            if s in vetoes:
                veto(s, "dip ENTRY")
                continue
            notes.append(f"dip ENTRY {s}: rsi2 {i['rsi2']} < {config.DIP_RSI_ENTRY}, above sma200 {i['sma200']}")
            dip[s] = {"entry_date": today, "entry_price": i["price"]}
            changed.add(s)

    # stock dip-buys
    stocks = dict(st["stocks"])
    for s in list(stocks):
        i = ind.get(s)
        if i and i["price"] > i["sma5"]:
            notes.append(f"stock EXIT {s}: {i['price']} > sma5 {i['sma5']}")
            del stocks[s]
            changed.add(s)
    cands = sorted((ind[s]["rsi2"], s) for s in STOCKS if s in ind and s not in stocks and is_dip_entry(ind[s]))
    if cands and not spy["above_sma200"]:
        notes.append("stock entries paused: SPY below its 200-day average")
    elif cands and len(stocks) < config.STOCK_SLOTS:
        try:
            reporting = earnings.reporting_within(config.EARNINGS_BLACKOUT_DAYS)
        except Exception as e:
            reporting = None
            notes.append(f"stock entries paused: earnings calendar unavailable ({str(e)[:60]})")
        for r, s in cands if reporting is not None else []:
            if len(stocks) >= config.STOCK_SLOTS:
                break
            if sum(SECTOR[h] == SECTOR[s] for h in stocks) >= config.SECTOR_CAP:
                continue
            if s in reporting:
                notes.append(f"stock skip {s}: earnings within {config.EARNINGS_BLACKOUT_DAYS} days")
                continue
            if s in vetoes:
                veto(s, "stock ENTRY")
                continue
            notes.append(f"stock ENTRY {s} ({SECTOR[s]}): rsi2 {r} < {config.DIP_RSI_ENTRY}, above sma200 {ind[s]['sma200']}")
            stocks[s] = {"entry_date": today, "entry_price": ind[s]["price"]}
            changed.add(s)

    # orders
    orders = []
    for s in UNIVERSE:
        cur_qty = float(pos[s]["qty"]) if s in pos else 0.0
        if s not in ind:
            continue
        px = ind[s]["price"]
        w = (config.CORE_WEIGHT if s == config.CORE_SYMBOL and core_on else 0) + (config.DIP_WEIGHT if s in dip else 0) \
            + (config.STOCK_WEIGHT if s in stocks else 0)
        target = eq * w
        diff = target - cur_qty * px
        if target == 0 and cur_qty:
            orders.append((s, "close", cur_qty, diff))
        elif abs(diff) > 1 and (s in changed or abs(diff) > config.REBALANCE_DRIFT * eq):
            orders.append((s, "buy" if diff > 0 else "sell", abs(diff) / px, diff))
    orders.sort(key=lambda o: o[1] == "buy")  # sells first to free cash

    print(f"Equity ${eq:,.2f}; core {'ON' if core_on else 'off'}; ETF dip-buys {sorted(dip) or 'none'}; "
          f"stocks {sorted(stocks) or 'none'} ({len(stocks)}/{config.STOCK_SLOTS})")
    print("\n".join("  " + n for n in notes) or "  no signal changes")
    for s, side, qty, val in orders:
        print(f"  ORDER {side.upper():5s} {s} qty {qty:.4f} (~${abs(val):,.0f})")
    if not orders:
        print("  no orders needed")
    if not args.execute:
        print("(plan only; add --execute to send)")
        return
    sent = 0
    for s, side, qty, val in orders:
        try:
            res = alp.close_position(s) if side == "close" else alp.market_order(s, side, qty, f"ust-{today}-{s}-{side}")
            sent += 1
        except Exception as e:  # keep going; the journal and summary record the failure
            print(f"  FAILED {side} {s}: {e}")
            journal("order_failed", symbol=s, side=side, error=str(e)[:300])
            continue
        journal("order", symbol=s, side=side, qty=round(qty, 4), value=round(val, 2), price=ind[s]["price"],
                order_id=(res or {}).get("id"), notes=[n for n in notes if f" {s}" in n])
    st.update(core_on=core_on, dip=dip, stocks=stocks, last_run=NOW.isoformat(timespec="seconds"))
    save_state(st)
    print(f"sent {sent}/{len(orders)} orders")


def cmd_monitor(args):
    """Intraday check: no orders. Flags positions that need a news check and previews likely entries."""
    alp = Alpaca(config.API_KEY, config.API_SECRET)
    st = load_state(alp)
    clock, acc, pos = alp.clock(), alp.account(), alp.positions()
    eq, last = float(acc["equity"]), float(acc["last_equity"])
    peak = max(st["peak_equity"], eq)
    alerts = []
    print(f"{NOW:%H:%M} UTC  equity ${eq:,.2f} (today {eq / last - 1:+.2%}), drawdown from peak {eq / peak - 1:+.2%}, "
          f"market {'OPEN' if clock['is_open'] else 'closed'}")
    if eq < (1 - config.MAX_DRAWDOWN_HALT * 0.75) * peak:
        alerts.append(f"account within 5 pts of the {config.MAX_DRAWDOWN_HALT:.0%} drawdown halt")
    for s, p in sorted(pos.items()):
        day = float(p.get("change_today") or 0)
        from_entry = float(p["unrealized_plpc"])
        print(f"  {s:5s} ${float(p['market_value']):>10,.0f}  today {day:+.2%}  since entry {from_entry:+.2%}")
        if day <= -config.ALERT_DAY_MOVE:
            alerts.append(f"{s} down {day:.1%} today")
        if (s in st["stocks"] or s in st["dip"]) and from_entry <= -config.ALERT_FROM_ENTRY:
            alerts.append(f"{s} down {from_entry:.1%} since entry")
    held = [s for s in st["stocks"]] + [s for s in st["dip"]]
    if clock["is_open"]:
        ind = snapshot(alp, True, ETFS + STOCKS)
        spy_day = ind["SPY"]["price"] / indicators("SPY")["price"] - 1
        if spy_day <= -config.ALERT_SPY_DAY_MOVE:
            alerts.append(f"SPY down {spy_day:.1%} today")
        exits = [s for s in held if s in ind and ind[s]["price"] > ind[s]["sma5"]]
        entries = sorted((ind[s]["rsi2"], s) for s in ETFS + STOCKS
                         if s in ind and s not in held and is_dip_entry(ind[s]))
        print("If the close were now: exits " + (", ".join(exits) or "none") + "; entry candidates "
              + (", ".join(f"{s} {r:.1f}" for r, s in entries) or "none")
              + ("" if ind["SPY"]["above_sma200"] else " (stock entries paused: SPY below 200d)"))
    print("ALERTS: " + ("; ".join(alerts) if alerts else "none"))


def cmd_close(args):
    """Emergency exit of one position for a company-altering event. Journaled separately from rule exits."""
    if len(args.reason) < 30:
        sys.exit("an emergency exit needs --reason (>=30 chars) naming the event and source")
    alp = Alpaca(config.API_KEY, config.API_SECRET)
    st = load_state(alp)
    s = args.symbol.upper()
    pos = alp.positions()
    if s not in pos:
        sys.exit(f"no {s} position")
    if s == config.CORE_SYMBOL or s in config.DIP_SYMBOLS:
        sys.exit("REFUSED: index ETFs follow their rules only; emergency exits are for single stocks")
    p = pos[s]
    alp.close_position(s)
    st["stocks"].pop(s, None)
    save_state(st)
    journal("emergency_exit", symbol=s, qty=float(p["qty"]), price=float(p["current_price"]),
            pl=float(p["unrealized_pl"]), reason=args.reason)
    print(f"CLOSED {s} at ~{p['current_price']} (P&L {float(p['unrealized_pl']):+,.2f})")


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
    """Closed round trips per sleeve, compared with the backtest."""
    if not os.path.exists(TRADES):
        sys.exit("no trades yet")
    recs = [json.loads(l) for l in open(TRADES)]
    bench = {"dip": "backtest 2016-2026: win 70%, avg win +1.20%, avg loss -1.57%",
             "stock": "backtest 2016-2026 (survivor-biased): win 65%, avg +0.20%, avg win +1.70%, avg loss -2.60%"}
    for kind in ("dip", "stock"):
        opens, trips = {}, []
        for r in recs:
            if r["event"] != "order":
                continue
            ns = r.get("notes", [])
            if any(f"{kind} ENTRY" in n for n in ns):
                opens[r["symbol"]] = r
            elif any(f"{kind} EXIT" in n for n in ns) and r["symbol"] in opens:
                o = opens.pop(r["symbol"])
                trips.append((r["symbol"], o["ts"][:10], r["ts"][:10], r["price"] / o["price"] - 1))
        print(f"{'ETF dip-buys' if kind == 'dip' else 'Stock dip-buys'}: {len(trips)} closed")
        for s, a, b, ret in trips[-10:]:
            print(f"  {s} {a} -> {b} {ret:+.2%}")
        if trips:
            rets = [t[3] for t in trips]
            print(f"  win {sum(x > 0 for x in rets) / len(rets):.0%}, avg {sum(rets) / len(rets):+.2%}  ({bench[kind]})")
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
    sub.add_parser("monitor")
    c = sub.add_parser("close"); c.add_argument("symbol"); c.add_argument("--reason", required=True)
    lg = sub.add_parser("log"); lg.add_argument("text")
    sub.add_parser("review")
    args = ap.parse_args()
    {"status": cmd_status, "run": cmd_run, "monitor": cmd_monitor, "close": cmd_close, "log": cmd_log,
     "review": cmd_review}[args.cmd](args)


if __name__ == "__main__":
    main()
