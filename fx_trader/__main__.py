"""CLI: python -m fx_trader {status,scan,open,modify,close,log,review}

Every order passes the hard risk gates in cmd_open; a failed gate refuses the order.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import analysis, config, news
from .oanda import Oanda

NOW = datetime.now(timezone.utc)
TRADES = os.path.join(config.JOURNAL_DIR, "trades.jsonl")


def broker():
    return None if config.DRY_RUN else Oanda(config.OANDA_TOKEN, config.OANDA_ACCOUNT_ID)


# ---------- journal ----------
def journal(event, **kw):
    os.makedirs(config.JOURNAL_DIR, exist_ok=True)
    rec = {"ts": NOW.isoformat(timespec="seconds"), "event": event, "dry_run": config.DRY_RUN, **kw}
    with open(TRADES, "a") as f:
        f.write(json.dumps(rec) + "\n")


def entries():
    """Journaled entries by trade id: entry price, side and initial risk distance for R-multiples."""
    out = {}
    if os.path.exists(TRADES):
        for line in open(TRADES):
            r = json.loads(line)
            if r["event"] == "open" and not r["dry_run"]:
                out[str(r["trade_id"])] = r
    return out


# ---------- account state ----------
def state(b):
    if b is None:
        return {"id": "dry-run", "alias": "", "currency": "USD", "balance": 10000.0, "equity": 10000.0}, []
    return b.account(), b.positions()


def position_risk(p, balance):
    """Account-currency loss if the stop is hit; 0 once the stop is at/through entry."""
    if p["sl"] is None:
        return balance * config.RISK_PER_TRADE * 2  # unprotected: count double
    return max(0.0, p["side"] * (p["open_price"] - p["sl"])) * p["units"] * p["loss_factor"]


def today_pnl(b, positions):
    if b is None:
        return 0.0
    start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    return sum(t["pl"] for t in b.closed_trades(start)) + sum(p["pl"] for p in positions)


def exposure(positions):
    exp = {}
    for p in positions:
        for cur, s in ((p["pair"][:3], p["side"]), (p["pair"][3:], -p["side"])):
            exp[(cur, s)] = exp.get((cur, s), 0) + 1
    return exp


# ---------- commands ----------
def cmd_status(args):
    b = broker()
    acc, pos = state(b)
    bal = acc["balance"]
    print(f"OANDA practice account {acc['id']} {acc['alias']}: balance {bal:.2f} equity {acc['equity']:.2f} {acc['currency']}")
    pnl = today_pnl(b, pos)
    print(f"Today P&L (UTC day, incl. floating): {pnl:+.2f} ({pnl / bal:+.2%}); new entries halt at {-config.DAILY_LOSS_HALT:.0%}")
    ent, total = entries(), 0.0
    print(f"Open trades: {len(pos)}/{config.MAX_POSITIONS}")
    for p in pos:
        risk = position_risk(p, bal)
        total += risk
        e = ent.get(str(p["id"]))
        r = f" R={p['side'] * (p['current_price'] - e['entry']) / e['risk_distance']:+.2f}" if e else ""
        print(f"  #{p['id']} {p['pair']} {'BUY' if p['side'] > 0 else 'SELL'} {p['units']:.0f}u @ {p['open_price']} "
              f"now {p['current_price']} SL {p['sl']} TP {p['tp']} P&L {p['pl']:+.2f}{r} risk-left {risk:.2f} "
              f"opened {p['open_time']}{'' if p['ours'] else ' [manual]'}")
    print(f"Open risk: {total:.2f} ({total / bal:.2%} of max {config.MAX_OPEN_RISK:.0%})")
    print("High-impact events next 36h:")
    for e in news.upcoming(36):
        print(f"  {e['time']:%a %H:%M} UTC {e['currency']} {e['title']} (f {e['forecast']}, p {e['previous']})")


def cmd_scan(args):
    b = broker()
    rows = []
    for pair in args.pairs or config.PAIRS:
        try:
            rows.append(analysis.snapshot(pair.upper(), b))
        except Exception as e:
            rows.append({"pair": pair, "error": str(e)[:200]})
    print(json.dumps(rows, indent=1))


def cmd_open(args):
    b, pair, d = broker(), args.pair.upper(), 1 if args.side == "buy" else -1
    acc, pos = state(b)
    bal = acc["balance"]
    risk_frac = min(args.risk, config.RISK_PER_TRADE)
    h4_atr = analysis.atr(analysis.get_candles(pair, "4h", b))
    if b is None:
        last, half = analysis.get_candles(pair, "4h")[-1]["close"], (0.006 if "JPY" in pair else 0.00006)
        q = {"bid": last - half, "ask": last + half, "tradeable": True,
             "loss_factor": (1 / last if pair.endswith("JPY") or pair[3:] != "USD" else 1.0)}
        min_units = 1
    else:
        q, min_units = b.quote(pair), b.spec(pair)["min_units"]
    entry = q["ask"] if d == 1 else q["bid"]
    dist = d * (entry - args.sl)
    rr = d * (args.tp - entry) / dist if dist > 0 else 0
    fails = []

    def gate(ok, msg):
        if not ok:
            fails.append(msg)

    gate(q["tradeable"], "market closed / instrument not tradeable")
    gate(dist > 0, f"stop {args.sl} is on the wrong side of entry {entry}")
    gate(rr >= config.MIN_REWARD_RISK, f"reward:risk {rr:.2f} < {config.MIN_REWARD_RISK}")
    gate(dist >= 0.5 * h4_atr, f"stop distance {dist:.5f} tighter than 0.5x H4 ATR {h4_atr:.5f}")
    gate(q["ask"] - q["bid"] <= config.MAX_SPREAD_ATR * h4_atr, f"spread {q['ask'] - q['bid']:.5f} too wide vs H4 ATR")
    gate(not any(p["pair"] == pair for p in pos), f"already have a {pair} trade")
    gate(len(pos) < config.MAX_POSITIONS, f"max {config.MAX_POSITIONS} trades open")
    exp = exposure(pos)
    for cur, s in ((pair[:3], d), (pair[3:], -d)):
        gate(exp.get((cur, s), 0) < config.MAX_PER_CURRENCY,
             f"already {config.MAX_PER_CURRENCY} trades {'long' if s > 0 else 'short'} {cur}")
    open_risk = sum(position_risk(p, bal) for p in pos)
    gate(open_risk + bal * risk_frac <= bal * config.MAX_OPEN_RISK + 1e-9, f"open risk would exceed {config.MAX_OPEN_RISK:.0%}")
    pnl = today_pnl(b, pos)
    gate(pnl > -config.DAILY_LOSS_HALT * bal, f"daily loss halt: today {pnl:+.2f}")
    blk = news.blocking(pair, config.NEWS_BEFORE_MIN, config.NEWS_AFTER_MIN)
    gate(not blk, "news blackout: " + "; ".join(f"{e['currency']} {e['title']} {e['time']:%H:%M}" for e in blk))
    gate(NOW.weekday() < 4 or (NOW.weekday() == 4 and NOW.hour < 18), "no new entries Friday after 18:00 UTC or weekends")
    gate(len(args.reason) >= 40, "reason must state the technical + fundamental case (>=40 chars)")
    units = int(bal * risk_frac / (dist * q["loss_factor"])) if dist > 0 else 0
    gate(units >= min_units, f"size {units} units below minimum {min_units}")
    plan = dict(pair=pair, side=args.side, entry=entry, sl=args.sl, tp=args.tp, rr=round(rr, 2), units=units,
                risk_money=round(units * max(dist, 0) * q["loss_factor"], 2), risk_distance=round(dist, 6), reason=args.reason)
    if fails:
        journal("rejected", **plan, failures=fails)
        sys.exit("REFUSED:\n  - " + "\n  - ".join(fails))
    if b is None:
        journal("open", **plan, trade_id="dry-run")
        print("DRY RUN (no order sent):", json.dumps(plan))
        return
    res = b.market_order(pair, d * units, args.sl, args.tp, args.reason)
    plan["entry"] = res["price"]
    plan["risk_distance"] = round(d * (res["price"] - args.sl), 6)
    journal("open", **plan, trade_id=res["trade_id"])
    print("OPENED trade", res["trade_id"], json.dumps(plan))


def find(b, tid):
    for p in b.positions():
        if str(p["id"]) == str(tid):
            return p
    sys.exit(f"trade {tid} not open")


def cmd_modify(args):
    b = broker()
    if b is None:
        sys.exit("modify needs a live OANDA connection")
    p = find(b, args.id)
    if args.sl is not None and p["sl"] is not None and p["side"] * (args.sl - p["sl"]) < 0:
        sys.exit("REFUSED: widening a stop is not allowed")
    b.modify(args.id, p["pair"], args.sl, args.tp)
    journal("modify", trade_id=args.id, pair=p["pair"], old_sl=p["sl"], old_tp=p["tp"],
            sl=args.sl if args.sl is not None else p["sl"], tp=args.tp if args.tp is not None else p["tp"], reason=args.reason)
    print("MODIFIED", args.id, p["pair"], "SL", args.sl, "TP", args.tp)


def cmd_close(args):
    b = broker()
    if b is None:
        sys.exit("close needs a live OANDA connection")
    p = find(b, args.id)
    res = b.close(args.id, args.units)
    journal("close", trade_id=args.id, pair=p["pair"], units=args.units or p["units"], price=res["price"],
            pl=res["pl"], reason=args.reason)
    print("CLOSED", args.id, p["pair"], f"at {res['price']} P&L {res['pl']:+.2f}")


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
    b = broker()
    if b is None:
        sys.exit("review needs a live OANDA connection")
    closed = [t for t in b.closed_trades(NOW - timedelta(days=args.days)) if t["ours"]]
    ent = entries()
    pl = [t["pl"] for t in closed]
    rs = [t["side"] * (t["close_price"] - ent[t["id"]]["entry"]) / ent[t["id"]]["risk_distance"]
          for t in closed if t["id"] in ent]
    wins, losses = [x for x in pl if x > 0], [x for x in pl if x <= 0]
    print(f"Last {args.days} days: {len(pl)} closed trades, net {sum(pl):+.2f}")
    if pl:
        print(f"  win rate {len(wins) / len(pl):.0%}, avg win {sum(wins) / max(1, len(wins)):.2f}, "
              f"avg loss {sum(losses) / max(1, len(losses)):.2f}")
    if rs:
        print(f"  avg R {sum(rs) / len(rs):+.2f} over {len(rs)} journaled trades")


def main():
    ap = argparse.ArgumentParser(prog="fx_trader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("scan"); s.add_argument("pairs", nargs="*")
    o = sub.add_parser("open"); o.add_argument("pair"); o.add_argument("side", choices=["buy", "sell"])
    o.add_argument("--sl", type=float, required=True); o.add_argument("--tp", type=float, required=True)
    o.add_argument("--risk", type=float, default=config.RISK_PER_TRADE); o.add_argument("--reason", required=True)
    m = sub.add_parser("modify"); m.add_argument("id"); m.add_argument("--sl", type=float); m.add_argument("--tp", type=float)
    m.add_argument("--reason", required=True)
    c = sub.add_parser("close"); c.add_argument("id"); c.add_argument("--units", type=float); c.add_argument("--reason", required=True)
    lg = sub.add_parser("log"); lg.add_argument("text")
    rv = sub.add_parser("review"); rv.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    if config.DRY_RUN:
        print("[DRY RUN: OANDA_TOKEN / OANDA_ACCOUNT_ID not set; Yahoo data, no orders sent]", file=sys.stderr)
    {"status": cmd_status, "scan": cmd_scan, "open": cmd_open, "modify": cmd_modify, "close": cmd_close,
     "log": cmd_log, "review": cmd_review}[args.cmd](args)


if __name__ == "__main__":
    main()
