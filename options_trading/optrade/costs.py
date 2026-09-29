"""Transaction charges for NSE option orders."""

from __future__ import annotations

from .config import CostConfig
from .models import Side


def order_charges(side: Side, price: float, quantity: int, cfg: CostConfig) -> float:
    """Approximate all-in charges (brokerage + statutory) for one executed option order."""
    turnover = price * quantity
    brokerage = min(cfg.brokerage_per_order, 0.03 * turnover) if turnover > 0 else 0.0
    exchange = turnover * cfg.exchange_txn_pct / 100
    sebi = turnover * cfg.sebi_pct / 100
    stt = turnover * cfg.stt_sell_pct / 100 if side is Side.SELL else 0.0
    stamp = turnover * cfg.stamp_buy_pct / 100 if side is Side.BUY else 0.0
    gst = (brokerage + exchange + sebi) * cfg.gst_pct / 100
    return round(brokerage + exchange + sebi + stt + stamp + gst, 2)
