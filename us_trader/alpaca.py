"""Alpaca REST client, paper trading only (https://docs.alpaca.markets/reference). Stdlib only."""
import json
import urllib.error
import urllib.request

PAPER = "https://paper-api.alpaca.markets/v2"  # paper only, by design; there is no live switch
DATA = "https://data.alpaca.markets/v2"


class AlpacaError(RuntimeError):
    pass


class Alpaca:
    def __init__(self, key, secret):
        if not (key and secret):
            raise AlpacaError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not set")
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Content-Type": "application/json"}

    def _req(self, url, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise AlpacaError(f"{method} {url.split('.markets')[-1]} -> {e.code}: {e.read().decode()[:400]}")

    def account(self):
        return self._req(f"{PAPER}/account")

    def clock(self):
        return self._req(f"{PAPER}/clock")

    def positions(self):
        return {p["symbol"]: p for p in self._req(f"{PAPER}/positions")}

    def portfolio_history(self):
        return self._req(f"{PAPER}/account/portfolio/history?period=1A&timeframe=1D")

    def open_orders(self):
        return self._req(f"{PAPER}/orders?status=open")

    def latest_price(self, symbol):
        return float(self._req(f"{DATA}/stocks/{symbol}/trades/latest?feed=iex")["trade"]["p"])

    def market_order(self, symbol, side, qty, client_id):
        return self._req(f"{PAPER}/orders", "POST", {
            "symbol": symbol, "side": side, "type": "market", "time_in_force": "day",
            "qty": f"{qty:.4f}".rstrip("0").rstrip("."), "client_order_id": client_id})

    def close_position(self, symbol):
        return self._req(f"{PAPER}/positions/{symbol}", "DELETE")
