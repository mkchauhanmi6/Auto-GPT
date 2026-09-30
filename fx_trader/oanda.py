"""OANDA v20 REST client (https://developer.oanda.com/rest-live-v20/introduction/), practice server only.

Returns broker-neutral dicts so the CLI does not depend on OANDA field names.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

PRACTICE = "https://api-fxpractice.oanda.com/v3"  # demo only, by design; there is no live switch
GRANULARITY = {"1d": "D", "4h": "H4", "1h": "H1"}


class BrokerError(RuntimeError):
    pass


def instrument(pair):
    return f"{pair[:3]}_{pair[3:6]}"


def ts(s):
    # OANDA uses RFC3339 with nanoseconds: 2026-09-30T12:00:00.000000000Z
    return datetime.fromisoformat(s[:19] + "+00:00")


class Oanda:
    def __init__(self, token, account_id):
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                        "Accept-Datetime-Format": "RFC3339"}
        self.acct = f"{PRACTICE}/accounts/{account_id}"
        self._inst = {}

    def _req(self, url, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise BrokerError(f"{method} {url.replace(PRACTICE, '')} -> {e.code}: {e.read().decode()[:500]}")

    # ----- reference data -----
    def spec(self, pair):
        if pair not in self._inst:
            i = self._req(f"{self.acct}/instruments?instruments={instrument(pair)}")["instruments"][0]
            self._inst[pair] = {"precision": i["displayPrecision"], "min_units": float(i["minimumTradeSize"]),
                                "units_precision": i["tradeUnitsPrecision"]}
        return self._inst[pair]

    def fmt(self, pair, price):
        return f"{price:.{self.spec(pair)['precision']}f}"

    def quote(self, pair):
        p = self._req(f"{self.acct}/pricing?instruments={instrument(pair)}&includeHomeConversions=true")["prices"][0]
        conv = p.get("quoteHomeConversionFactors", {})
        return {"bid": float(p["bids"][0]["price"]), "ask": float(p["asks"][0]["price"]),
                "tradeable": p.get("tradeable", True),
                # account-currency value of a 1.0 price move per unit, for a losing move
                "loss_factor": float(conv.get("negativeUnits", 1.0))}

    def candles(self, pair, tf, count=500):
        url = f"{PRACTICE}/instruments/{instrument(pair)}/candles?granularity={GRANULARITY[tf]}&count={count}&price=M"
        out = []
        for c in self._req(url)["candles"]:
            if c["complete"]:
                m = c["mid"]
                out.append({"time": ts(c["time"]), "open": float(m["o"]), "high": float(m["h"]),
                            "low": float(m["l"]), "close": float(m["c"])})
        return out

    # ----- account state -----
    def account(self):
        a = self._req(f"{self.acct}/summary")["account"]
        return {"id": a["id"], "alias": a.get("alias", ""), "currency": a["currency"], "balance": float(a["balance"]),
                "equity": float(a["NAV"]), "unrealized": float(a["unrealizedPL"])}

    def positions(self):
        trades = self._req(f"{self.acct}/openTrades")["trades"]
        out = []
        for t in trades:
            pair = t["instrument"].replace("_", "")
            units = float(t["currentUnits"])
            q = self.quote(pair)
            out.append({"id": t["id"], "pair": pair, "side": 1 if units > 0 else -1, "units": abs(units),
                        "open_price": float(t["price"]), "current_price": q["bid"] if units > 0 else q["ask"],
                        "sl": float(t["stopLossOrder"]["price"]) if t.get("stopLossOrder") else None,
                        "tp": float(t["takeProfitOrder"]["price"]) if t.get("takeProfitOrder") else None,
                        "pl": float(t.get("unrealizedPL", 0)), "open_time": t["openTime"][:16],
                        "ours": t.get("clientExtensions", {}).get("tag") == "fxt", "loss_factor": q["loss_factor"]})
        return out

    def closed_trades(self, since):
        trades = self._req(f"{self.acct}/trades?state=CLOSED&count=500")["trades"]
        out = []
        for t in trades:
            ct = ts(t["closeTime"])
            if ct < since:
                continue
            units = float(t["initialUnits"])
            out.append({"id": t["id"], "pair": t["instrument"].replace("_", ""), "side": 1 if units > 0 else -1,
                        "open_price": float(t["price"]), "close_price": float(t.get("averageClosePrice", 0)),
                        "pl": float(t.get("realizedPL", 0)) + float(t.get("financing", 0)), "close_time": ct,
                        "ours": t.get("clientExtensions", {}).get("tag") == "fxt"})
        return out

    # ----- trading -----
    def market_order(self, pair, units, sl, tp, comment):
        body = {"order": {"type": "MARKET", "instrument": instrument(pair), "units": str(int(units)),
                          "timeInForce": "FOK", "positionFill": "DEFAULT",
                          "stopLossOnFill": {"price": self.fmt(pair, sl), "timeInForce": "GTC"},
                          "takeProfitOnFill": {"price": self.fmt(pair, tp), "timeInForce": "GTC"},
                          "tradeClientExtensions": {"tag": "fxt", "comment": comment[:120]}}}
        res = self._req(f"{self.acct}/orders", "POST", body)
        fill = res.get("orderFillTransaction")
        if not fill or "tradeOpened" not in fill:
            raise BrokerError(f"order not filled: {json.dumps(res.get('orderCancelTransaction', res))[:500]}")
        return {"trade_id": fill["tradeOpened"]["tradeID"], "price": float(fill["price"])}

    def modify(self, trade_id, pair, sl=None, tp=None):
        body = {}
        if sl is not None:
            body["stopLoss"] = {"price": self.fmt(pair, sl), "timeInForce": "GTC"}
        if tp is not None:
            body["takeProfit"] = {"price": self.fmt(pair, tp), "timeInForce": "GTC"}
        return self._req(f"{self.acct}/trades/{trade_id}/orders", "PUT", body)

    def close(self, trade_id, units=None):
        res = self._req(f"{self.acct}/trades/{trade_id}/close", "PUT", {"units": str(int(units)) if units else "ALL"})
        fill = res.get("orderFillTransaction", {})
        return {"price": float(fill.get("price", 0)), "pl": float(fill.get("pl", 0))}
