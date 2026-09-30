"""CLI: python -m fx_trader {status,scan,open,modify,close,log,review}

Every order passes the hard risk gates below; a failed gate refuses the order.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import analysis, config, news
from .metaapi import MetaApi

NOW = datetime.now(timezone.utc)


def api():
    return None if config.DRY_RUN else MetaApi(config.METAAPI_TOKEN, config.METAAPI_ACCOUNT_ID, config.METAAPI_REGION)


def sym(pair):
    return pair.upper() + config.SYMBOL_SUFFIX


def pair_of(symbol):
    return symbol[:6].upper()


# ---------- journal ----------
TRADES = os.path.join(config.JOURNAL_DIR, "trades.jsonl")


def journal(event, **kw):
    os.makedirs(config.JOURNAL_DIR, exist_ok=True)
    rec = {"ts": NOW.isoformat(timespec="seconds"), "event": event, "dry_run": config.DRY_RUN, **kw}
    with open(TRADES, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def entries():
    if not os.path.exists(TRADES):
        return {}
    out = {}
    for line in open(TRADES):
        r = json.loads(line)
        if r["event"] == "open" and not r["dry_run"]:
            out[str(r["position_id"])] = r
    return out


# ---------- account state ----------
def account_state(a):
    if a is None:
        return {"balance": 10000.0, "equity": 10000.0, "currency": "USD", "type": "DRY_RUN"}, []
    return a.account(), [p for p in a.positions()]


def position_risk(a, p, balance):
    """Money lost if the position's stop is hit (0 once the stop is at/through entry)."""
    if not p.get("stopLoss"):
        return balance * config.RISK_PER_TRADE * 2  # unprotected: count double and flag
    d = 1 if p["type"] == "POSITION_TYPE_BUY" else -1
    dist = d * (p["openPrice"] - p["stopLoss"])
    if dist <= 0:
        return 0.0
    tick = a.spec(p["symbol"])["tickSize"]
    return dist / tick * p.get("currentTickValue", 0) * p["volume"]


def today_pnl(a, positions):
    if a is None:
        return 0.0
    start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    deals = a.deals(start.isoformat().replace("+00:00", "Z"), (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"))
    realized = sum(d.get("profit", 0) + d.get("commission", 0) + d.get("swap", 0)
                   for d in deals if d.get("type") in ("DEAL_TYPE_BUY", "DEAL_TYPE_SELL"))
    return realized + sum(p.get("profit", 0) for p in positions)


def exposure(positions):
    exp = {}
    for p in positions:
        pr, d = pair_of(p["symbol"]), 1 if p["type"] == "POSITION_TYPE_BUY" else -1
        for cur, s in ((pr[:3], d), (pr[3:], -d)):
            exp[(cur, s)] = exp.get((cur, s), 0) + 1
    return exp


# ---------- commands ----------
def cmd_status(args):
    a = api()
    if a is not None:
        st = a.account_state()
        print(f"MetaApi account: state={st.get('state')} connection={st.get('connectionStatus')}")
        if st.get("state") != "DEPLOYED":
            print("Account not deployed; deploying (takes ~1 min)..."); a.deploy(); return
    acc, pos = account_state(a)
    bal = acc["balance"]
    print(f"Account {acc.get('login', '-')} {acc.get('server', '')} type={acc.get('type')} "
          f"balance={bal:.2f} equity={acc['equity']:.2f} {acc['currency']}")
    pnl = today_pnl(a, pos)
    print(f"Today P&L (UTC day, incl. floating): {pnl:+.2f} ({pnl / bal:+.2%}); halt at {-config.DAILY_LOSS_HALT:.0%}")
    ent, total_risk = entries(), 0.0
    print(f"Open positions: {len(pos)}/{config.MAX_POSITIONS}")
    for p in pos:
        risk = position_risk(a, p, bal)
        total_risk += risk
        e = ent.get(str(p["id"]))
        r_mult = ""
        if e:
            d = 1 if p["type"] == "POSITION_TYPE_BUY" else -1
            r_mult = f" R={d * (p['currentPrice'] - e['entry']) / e['risk_distance']:+.2f}"
        print(f"  #{p['id']} {p['symbol']} {p['type'][14:]} {p['volume']} @ {p['openPrice']} now {p['currentPrice']} "
              f"SL {p.get('stopLoss')} TP {p.get('takeProfit')} P&L {p.get('profit', 0):+.2f}{r_mult} "
              f"risk-left {risk:.2f} opened {p['time'][:16]}{'' if p.get('magic') == config.MAGIC else ' [manual]'}")
    print(f"Open risk: {total_risk:.2f} ({total_risk / bal:.2%} of max {config.MAX_OPEN_RISK:.0%})")
    print("High-impact events next 36h:")
    for e in news.upcoming(36):
        print(f"  {e['time']:%a %H:%M} UTC {e['currency']} {e['title']} (f {e['forecast']}, p {e['previous']})")


def cmd_scan(args):
    a = api()
    rows = []
    for pair in args.pairs or config.PAIRS:
        try:
            rows.append(analysis.snapshot(pair.upper(), a))
        except Exception as e:
            rows.append({"pair": pair, "error": str(e)[:200]})
    print(json.dumps(rows, indent=1))


def gate(ok, msg, fails):
    if not ok:
        fails.append(msg)


def cmd_open(args):
    a, pair = api(), args.pair.upper()
    side = args.side.lower()
    d = 1 if side == "buy" else -1
    acc, pos = account_state(a)
    bal = acc["balance"]
    risk_frac = min(args.risk, config.RISK_PER_TRADE)
    h4 = analysis.get_candles(pair, "4h", a)
    h4_atr = analysis.atr(h4)
    if a is None:
        last = h4[-1]["close"]
        spread = 0.00012 if "JPY" not in pair else 0.012
        bid, ask, tick, tick_val, vmin, vstep, vmax = last - spread / 2, last + spread / 2, (0.001 if "JPY" in pair else 0.00001), 1.0, 0.01, 0.01, 100
    else:
        pr, sp = a.price(sym(pair)), a.spec(sym(pair))
        bid, ask, tick_val = pr["bid"], pr["ask"], pr["lossTickValue"]
        tick, vmin, vstep, vmax = sp["tickSize"], sp["minVolume"], sp["volumeStep"], sp["maxVolume"]
    entry = ask if d == 1 else bid
    dist = d * (entry - args.sl)
    fails = []
    gate(dist > 0, f"stop {args.sl} is on the wrong side of entry {entry}", fails)
    gate(args.tp is not None and d * (args.tp - entry) > 0, "take-profit missing or on the wrong side", fails)
    rr = d * (args.tp - entry) / dist if dist > 0 and args.tp else 0
    gate(rr >= config.MIN_REWARD_RISK, f"reward:risk {rr:.2f} < {config.MIN_REWARD_RISK}", fails)
    gate(dist >= 0.5 * h4_atr, f"stop {dist:.5f} tighter than 0.5x H4 ATR {h4_atr:.5f} (noise)", fails)
    gate(ask - bid <= config.MAX_SPREAD_ATR * h4_atr, f"spread {ask - bid:.5f} too wide vs H4 ATR", fails)
    gate(not any(pair_of(p["symbol"]) == pair for p in pos), f"already have a {pair} position", fails)
    gate(len(pos) < config.MAX_POSITIONS, f"max {config.MAX_POSITIONS} positions open", fails)
    exp = exposure(pos)
    for cur, s in ((pair[:3], d), (pair[3:], -d)):
        gate(exp.get((cur, s), 0) < config.MAX_PER_CURRENCY, f"already {config.MAX_PER_CURRENCY} positions {'long' if s > 0 else 'short'} {cur}", fails)
    open_risk = sum(position_risk(a, p, bal) for p in pos) if a else 0.0
    gate(open_risk + bal * risk_frac <= bal * config.MAX_OPEN_RISK + 1e-9, f"open risk would exceed {config.MAX_OPEN_RISK:.0%}", fails)
    pnl = today_pnl(a, pos)
    gate(pnl > -config.DAILY_LOSS_HALT * bal, f"daily loss halt: today {pnl:+.2f}", fails)
    blk = news.blocking(pair, config.NEWS_BEFORE_MIN, config.NEWS_AFTER_MIN)
    gate(not blk, "news blackout: " + "; ".join(f"{e['currency']} {e['title']} {e['time']:%H:%M}" for e in blk), fails)
    gate(not (NOW.weekday() == 4 and NOW.hour >= 18) and NOW.weekday() < 5, "no new entries Friday after 18:00 UTC or weekends", fails)
    gate(len(args.reason) >= 40, "reason must state the technical + fundamental case (>=40 chars)", fails)
    volume = 0.0
    if dist > 0:
        raw = bal * risk_frac / (dist / tick * tick_val)
        volume = round(min(vmax, int(raw / vstep + 1e-9) * vstep), 2)
        gate(volume >= vmin, f"position size {raw:.3f} below broker minimum {vmin}", fails)
    plan = dict(pair=pair, side=side, entry=round(entry, 5), sl=args.sl, tp=args.tp, rr=round(rr, 2), volume=volume,
                risk_money=round(volume * dist / tick * tick_val, 2), risk_distance=round(dist, 6), reason=args.reason)
    if fails:
        journal("rejected", **plan, failures=fails)
        print("REFUSED:\n  - " + "\n  - ".join(fails))
        sys.exit(2)
    if a is None:
        journal("open", **plan, position_id="dry-run")
        print("DRY RUN (no order sent):", json.dumps(plan))
        return
    res = a.trade({"actionType": "ORDER_TYPE_BUY" if d == 1 else "ORDER_TYPE_SELL", "symbol": sym(pair),
                   "volume": volume, "stopLoss": args.sl, "takeProfit": args.tp, "magic": config.MAGIC,
                   "comment": "fxt"})
    pid = res.get("positionId") or res.get("orderId")
    journal("open", **plan, position_id=pid, response=res)
    print("OPENED", pid, json.dumps(plan))


def find_position(a, pid):
    for p in a.positions():
        if str(p["id"]) == str(pid):
            return p
    sys.exit(f"position {pid} not found")


def cmd_modify(args):
    a = api()
    if a is None:
        journal("modify", position_id=args.id, sl=args.sl, tp=args.tp, reason=args.reason); print("DRY RUN modify"); return
    p = find_position(a, args.id)
    d = 1 if p["type"] == "POSITION_TYPE_BUY" else -1
    if args.sl is not None and p.get("stopLoss") and d * (args.sl - p["stopLoss"]) < 0 and not args.allow_widen:
        sys.exit("REFUSED: widening a stop is not allowed (pass --allow-widen only with a written reason)")
    body = {"actionType": "POSITION_MODIFY", "positionId": str(args.id),
            "stopLoss": args.sl if args.sl is not None else p.get("stopLoss"),
            "takeProfit": args.tp if args.tp is not None else p.get("takeProfit")}
    res = a.trade(body)
    journal("modify", position_id=args.id, symbol=p["symbol"], old_sl=p.get("stopLoss"), old_tp=p.get("takeProfit"),
            sl=body["stopLoss"], tp=body["takeProfit"], reason=args.reason, response=res)
    print("MODIFIED", args.id, body)


def cmd_close(args):
    a = api()
    if a is None:
        journal("close", position_id=args.id, reason=args.reason); print("DRY RUN close"); return
    p = find_position(a, args.id)
    if args.volume:
        res = a.trade({"actionType": "POSITION_PARTIAL", "positionId": str(args.id), "volume": args.volume})
    else:
        res = a.trade({"actionType": "POSITION_CLOSE_ID", "positionId": str(args.id)})
    journal("close", position_id=args.id, symbol=p["symbol"], volume=args.volume or p["volume"],
            price=p["currentPrice"], profit=p.get("profit"), reason=args.reason, response=res)
    print("CLOSED", args.id, p["symbol"], f"P&L {p.get('profit', 0):+.2f}")


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
    a = api()
    if a is None:
        sys.exit("review needs a live MetaApi connection")
    start = (NOW - timedelta(days=args.days)).isoformat().replace("+00:00", "Z")
    deals = [d for d in a.deals(start, NOW.isoformat().replace("+00:00", "Z"))
             if d.get("magic") == config.MAGIC and d.get("entryType") == "DEAL_ENTRY_OUT"]
    ent = entries()
    pnl = [d.get("profit", 0) + d.get("commission", 0) + d.get("swap", 0) for d in deals]
    rs = []
    for d in deals:
        e = ent.get(str(d.get("positionId")))
        if e:
            rs.append((1 if e["side"] == "buy" else -1) * (d["price"] - e["entry"]) / e["risk_distance"])
    n = len(pnl)
    print(f"Last {args.days} days: {n} closed deals, net {sum(pnl):+.2f}")
    if n:
        print(f"  win rate {sum(x > 0 for x in pnl) / n:.0%}, avg win {sum(x for x in pnl if x > 0) / max(1, sum(x > 0 for x in pnl)):.2f}, "
              f"avg loss {sum(x for x in pnl if x <= 0) / max(1, sum(x <= 0 for x in pnl)):.2f}")
    if rs:
        print(f"  avg R {sum(rs) / len(rs):+.2f} over {len(rs)} journaled exits")


def main():
    ap = argparse.ArgumentParser(prog="fx_trader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("scan"); s.add_argument("pairs", nargs="*")
    o = sub.add_parser("open"); o.add_argument("pair"); o.add_argument("side", choices=["buy", "sell"])
    o.add_argument("--sl", type=float, required=True); o.add_argument("--tp", type=float, required=True)
    o.add_argument("--risk", type=float, default=config.RISK_PER_TRADE); o.add_argument("--reason", required=True)
    m = sub.add_parser("modify"); m.add_argument("id"); m.add_argument("--sl", type=float); m.add_argument("--tp", type=float)
    m.add_argument("--reason", required=True); m.add_argument("--allow-widen", action="store_true")
    c = sub.add_parser("close"); c.add_argument("id"); c.add_argument("--volume", type=float); c.add_argument("--reason", required=True)
    lg = sub.add_parser("log"); lg.add_argument("text")
    rv = sub.add_parser("review"); rv.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    if config.DRY_RUN:
        print("[DRY RUN: METAAPI_TOKEN / METAAPI_ACCOUNT_ID not set; Yahoo data, no orders sent]", file=sys.stderr)
    {"status": cmd_status, "scan": cmd_scan, "open": cmd_open, "modify": cmd_modify, "close": cmd_close,
     "log": cmd_log, "review": cmd_review}[args.cmd](args)


if __name__ == "__main__":
    main()
