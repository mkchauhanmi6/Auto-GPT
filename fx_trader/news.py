"""High-impact economic calendar (ForexFactory weekly feed)."""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

FEEDS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
         "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]
CACHE = "/tmp/fx_trader_calendar.json"


def events(impact=("High",)):
    if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 3600:
        raw = json.load(open(CACHE))
    else:
        raw = []
        for url in FEEDS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    raw += json.load(r)
            except Exception:
                pass  # next week's feed is often not published yet
        if not raw:
            raise RuntimeError("economic calendar unavailable")
        json.dump(raw, open(CACHE, "w"))
    out = []
    for e in raw:
        if e.get("impact") in impact:
            out.append({"time": datetime.fromisoformat(e["date"]).astimezone(timezone.utc), "currency": e["country"],
                        "title": e["title"], "forecast": e.get("forecast"), "previous": e.get("previous")})
    return sorted(out, key=lambda e: e["time"])


def blocking(pair, before_min, after_min, now=None):
    """High-impact events for either currency of the pair inside the blackout window."""
    now = now or datetime.now(timezone.utc)
    cur = {pair[:3], pair[3:6]}
    return [e for e in events() if e["currency"] in cur
            and now - timedelta(minutes=after_min) <= e["time"] <= now + timedelta(minutes=before_min)]


def upcoming(hours=48, now=None):
    now = now or datetime.now(timezone.utc)
    return [e for e in events() if now - timedelta(hours=1) <= e["time"] <= now + timedelta(hours=hours)]
