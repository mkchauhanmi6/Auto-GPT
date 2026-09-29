"""Mirror paper trades to the DhanHQ Sandbox (a free, API-driven paper-trading account).

Unlike Sensibull, Dhan offers a sandbox order API, so orders are placed
automatically. Get sandbox credentials at https://developer.dhanhq.co
(DevPortal -> Sandbox tab) and set ``DHAN_SANDBOX_CLIENT_ID`` and
``DHAN_SANDBOX_ACCESS_TOKEN``.

Safety: this adapter refuses to talk to anything but a *sandbox* host, so it
can never place a live order by accident.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urlparse

from ..data.scripmaster import ScripMaster
from ..models import Leg, Side, Signal
from .base import OrderMirror
from .position import Position

log = logging.getLogger(__name__)


class DhanSandboxMirror(OrderMirror):
    name = "dhan_sandbox"

    def __init__(
        self,
        scrip_master: ScripMaster,
        base_url: str = "https://sandbox.dhan.co/v2",
        product_type: str = "INTRADAY",
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ):
        host = urlparse(base_url).hostname or ""
        if "sandbox" not in host:
            raise ValueError(f"Refusing non-sandbox Dhan host {host!r}: this tool only paper trades")
        self.base_url = base_url.rstrip("/")
        self.product_type = product_type
        self.client_id = client_id or os.environ.get("DHAN_SANDBOX_CLIENT_ID")
        self.access_token = access_token or os.environ.get("DHAN_SANDBOX_ACCESS_TOKEN")
        if not self.client_id or not self.access_token:
            raise RuntimeError("Set DHAN_SANDBOX_CLIENT_ID and DHAN_SANDBOX_ACCESS_TOKEN")
        self.scrip_master = scrip_master

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "access-token": self.access_token,
                "client-id": self.client_id,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                text = resp.read().decode()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Dhan {method} {path} -> HTTP {e.code}: {e.read().decode()[:300]}") from e
        return json.loads(text) if text else {}

    def funds(self) -> dict:
        return self._request("GET", "/fundlimit")

    def order_body(self, leg: Leg, side: Side, tag: str) -> dict:
        sec_id = self.scrip_master.security_id(leg.contract)
        if not sec_id:
            raise ValueError(f"{leg.contract.symbol} not found in Dhan scrip master")
        return {
            "dhanClientId": self.client_id,
            "correlationId": tag[:25],
            "transactionType": side.value,
            "exchangeSegment": "NSE_FNO",
            "productType": self.product_type,
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": sec_id,
            "quantity": leg.quantity,
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
        }

    def _place(self, leg: Leg, side: Side, tag: str) -> dict:
        try:
            resp = self._request("POST", "/orders", self.order_body(leg, side, tag))
            log.info("Dhan sandbox %s %s x%d -> %s", side.value, leg.contract.symbol, leg.quantity, resp)
            return {"symbol": leg.contract.symbol, "side": side.value, "response": resp}
        except Exception as e:  # noqa: BLE001 - record and continue; the paper ledger stays authoritative
            log.error("Dhan sandbox order failed for %s: %s", leg.contract.symbol, e)
            return {"symbol": leg.contract.symbol, "side": side.value, "error": str(e)}

    def on_entry(self, position: Position, signal: Signal) -> None:
        # buy legs first so the sandbox grants the spread margin benefit
        legs = sorted(position.legs, key=lambda leg: leg.side is not Side.BUY)
        results = [self._place(leg, leg.side, f"{position.id}-in{i}") for i, leg in enumerate(legs)]
        position.mirror_refs.setdefault(self.name, {})["entry"] = results

    def on_exit(self, position: Position) -> None:
        # close shorts first, then longs
        legs = sorted(position.legs, key=lambda leg: leg.side is Side.BUY)
        results = [
            self._place(leg, leg.side.opposite(), f"{position.id}-out{i}") for i, leg in enumerate(legs)
        ]
        position.mirror_refs.setdefault(self.name, {})["exit"] = results
