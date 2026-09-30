"""Minimal MetaApi REST client (https://metaapi.cloud/docs/client/). Stdlib only."""
import json
import urllib.error
import urllib.parse
import urllib.request

OK_CODES = {10008, 10009, 10010}  # PLACED, DONE, DONE_PARTIAL


class MetaApiError(RuntimeError):
    pass


class MetaApi:
    def __init__(self, token, account_id, region="new-york"):
        self.headers = {"auth-token": token, "Content-Type": "application/json", "Accept": "application/json"}
        path = f"/users/current/accounts/{account_id}"
        self.base = f"https://mt-client-api-v1.{region}.agiliumtrade.ai{path}"
        self.market = f"https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai{path}"
        self.provisioning = f"https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai{path}"

    def _req(self, url, method="GET", body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise MetaApiError(f"{method} {url.split('/accounts/')[-1]} -> {e.code}: {e.read().decode()[:400]}")

    def account_state(self):
        return self._req(self.provisioning)

    def deploy(self):
        return self._req(self.provisioning + "/deploy", "POST")

    def account(self):
        return self._req(self.base + "/account-information")

    def positions(self):
        return self._req(self.base + "/positions")

    def orders(self):
        return self._req(self.base + "/orders")

    def price(self, symbol):
        return self._req(f"{self.base}/symbols/{symbol}/current-price")

    def spec(self, symbol):
        return self._req(f"{self.base}/symbols/{symbol}/specification")

    def deals(self, start_iso, end_iso):
        q = lambda s: urllib.parse.quote(s, safe="")
        return self._req(f"{self.base}/history-deals/time/{q(start_iso)}/{q(end_iso)}")

    def candles(self, symbol, timeframe, limit=500):
        url = f"{self.market}/historical-market-data/symbols/{symbol}/timeframes/{timeframe}/candles?limit={limit}"
        return self._req(url, timeout=240)

    def trade(self, body):
        res = self._req(self.base + "/trade", "POST", body)
        if not res or res.get("numericCode") not in OK_CODES:
            raise MetaApiError(f"trade rejected: {res}")
        return res
