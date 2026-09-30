"""Upcoming earnings dates from Nasdaq's public earnings calendar."""
import json
import urllib.request
from datetime import date, timedelta


def reporting_within(days, today=None):
    """Symbols reporting from today through today+days. Raises if the calendar can't be read."""
    today = today or date.today()
    out = set()
    for k in range(days + 1):
        d = today + timedelta(days=k)
        if d.weekday() >= 5:
            continue
        req = urllib.request.Request(f"https://api.nasdaq.com/api/calendar/earnings?date={d}",
                                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = (json.load(r).get("data") or {}).get("rows") or []
        out.update(row["symbol"] for row in rows)
    return out
