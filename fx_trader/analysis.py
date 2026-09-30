"""Technical snapshot per pair: D1 trend, H4 structure, support/resistance, price-action triggers.

Output is a fact sheet for the decision-maker; it does not decide trades by itself.
"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone


TF_SECONDS = {"1d": 86400, "4h": 14400, "1h": 3600}


# ---------- data ----------
def _yahoo(pair, interval, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{pair}=X?range={rng}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
            continue
        out.append({"time": datetime.fromtimestamp(t, timezone.utc), "open": q["open"][i],
                    "high": q["high"][i], "low": q["low"][i], "close": q["close"][i]})
    return out


def _to_h4(h1):
    buckets = {}
    for c in h1:
        k = c["time"].replace(hour=c["time"].hour - c["time"].hour % 4, minute=0, second=0, microsecond=0)
        b = buckets.get(k)
        if b is None:
            buckets[k] = dict(c, time=k)
        else:
            b["high"], b["low"], b["close"] = max(b["high"], c["high"]), min(b["low"], c["low"]), c["close"]
    return [buckets[k] for k in sorted(buckets)]


def get_candles(pair, tf, api=None):
    """Closed candles only, oldest first."""
    if api is not None:
        bars = api.candles(pair, tf, 600 if tf == "1d" else 500)
    elif tf == "1d":
        bars = _yahoo(pair, "1d", "2y")
    else:
        bars = _to_h4(_yahoo(pair, "1h", "60d"))
    now = datetime.now(timezone.utc)
    return [c for c in bars if c["time"] + timedelta(seconds=TF_SECONDS[tf]) <= now]


# ---------- indicators ----------
def ema(xs, n):
    k, out = 2 / (n + 1), []
    for x in xs:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def atr(bars, n=14):
    trs = [bars[0]["high"] - bars[0]["low"]]
    for p, c in zip(bars, bars[1:]):
        trs.append(max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"])))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def rsi(closes, n=14):
    gains = losses = 0.0
    for p, c in zip(closes[-n - 1:], closes[-n:]):
        gains += max(c - p, 0)
        losses += max(p - c, 0)
    return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)


def swings(bars, k=3):
    """Fractal swing highs/lows: bar extreme beyond k bars each side."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if bars[i]["high"] == max(b["high"] for b in win):
            highs.append(bars[i]["high"])
        if bars[i]["low"] == min(b["low"] for b in win):
            lows.append(bars[i]["low"])
    return highs, lows


def pattern(bars):
    """Price-action pattern on the last closed bar."""
    p, c = bars[-2], bars[-1]
    rng = c["high"] - c["low"] or 1e-12
    body = abs(c["close"] - c["open"])
    upper = c["high"] - max(c["open"], c["close"])
    lower = min(c["open"], c["close"]) - c["low"]
    if c["close"] > c["open"] and p["close"] < p["open"] and c["close"] >= p["open"] and c["open"] <= p["close"]:
        return "bullish_engulfing"
    if c["close"] < c["open"] and p["close"] > p["open"] and c["close"] <= p["open"] and c["open"] >= p["close"]:
        return "bearish_engulfing"
    if lower >= 2 * body and lower >= 0.6 * rng:
        return "bullish_pin"
    if upper >= 2 * body and upper >= 0.6 * rng:
        return "bearish_pin"
    if c["high"] <= p["high"] and c["low"] >= p["low"]:
        return "inside_bar"
    return None


# ---------- snapshot ----------
def snapshot(pair, api=None):
    d1, h4 = get_candles(pair, "1d", api), get_candles(pair, "4h", api)
    dc, hc = [b["close"] for b in d1], [b["close"] for b in h4]
    px = hc[-1]
    e50, e200 = ema(dc, 50)[-1], ema(dc, 200)[-1]
    mom12 = dc[-1] / dc[-253] - 1 if len(dc) > 253 else None
    score = (1 if dc[-1] > e200 else -1) + (1 if e50 > e200 else -1) + ((1 if mom12 > 0 else -1) if mom12 is not None else 0)
    trend = "up" if score >= 2 else "down" if score <= -2 else "mixed"
    a_d1, a_h4 = atr(d1), atr(h4)
    sh, sl = swings(d1[-180:])
    res = sorted(h for h in sh if h > px)[:2]
    sup = sorted((l for l in sl if l < px), reverse=True)[:2]
    h4e20 = ema(hc, 20)[-1]
    digits = 3 if "JPY" in pair else 5
    r = lambda x: round(x, digits) if x is not None else None
    return {
        "pair": pair, "price": r(px), "h4_bar_closed": h4[-1]["time"].isoformat(),
        "d1_trend": trend, "d1_trend_score": score, "d1_ema50": r(e50), "d1_ema200": r(e200),
        "mom_12m_pct": round(mom12 * 100, 2) if mom12 is not None else None,
        "d1_atr": r(a_d1), "h4_atr": r(a_h4), "d1_rsi14": round(rsi(dc), 1), "h4_rsi14": round(rsi(hc), 1),
        "d1_20d_high": r(max(b["high"] for b in d1[-21:-1])), "d1_20d_low": r(min(b["low"] for b in d1[-21:-1])),
        "h4_ema20": r(h4e20), "h4_dist_ema20_atr": round((px - h4e20) / a_h4, 2),
        "resistance": [r(x) for x in res], "support": [r(x) for x in sup],
        "dist_to_res_atr_d1": round((res[0] - px) / a_d1, 2) if res else None,
        "dist_to_sup_atr_d1": round((px - sup[0]) / a_d1, 2) if sup else None,
        "h4_pattern": pattern(h4), "d1_pattern": pattern(d1),
    }
