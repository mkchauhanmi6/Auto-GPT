"""Daily indicators from Yahoo history plus today's live price."""
import json
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def daily_closes(symbol):
    """[(date, close)] oldest first, split-adjusted, excluding today's partial bar."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    today = datetime.now(NY).date()
    out = []
    for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        d = datetime.fromtimestamp(t, timezone.utc).astimezone(NY).date()
        if c is not None and d < today:
            out.append((d, c))
    return out


def sma(xs, n):
    return sum(xs[-n:]) / n


def rsi(xs, n=2):
    """Wilder RSI over the full series."""
    up = down = None
    for p, c in zip(xs, xs[1:]):
        g, l = max(c - p, 0.0), max(p - c, 0.0)
        up = g if up is None else up + (g - up) / n
        down = l if down is None else down + (l - down) / n
    return 100.0 if down == 0 else 100 - 100 / (1 + up / down)


def indicators(symbol, live_price=None):
    """Indicators as of today's close (live_price stands in for it) or the last close."""
    hist = daily_closes(symbol)
    closes = [c for _, c in hist] + ([live_price] if live_price else [])
    px = closes[-1]
    return {"symbol": symbol, "price": round(px, 2), "last_close_date": str(hist[-1][0]),
            "sma200": round(sma(closes, 200), 2), "sma5": round(sma(closes, 5), 2),
            "rsi2": round(rsi(closes[-120:]), 1), "above_sma200": px > sma(closes, 200)}
