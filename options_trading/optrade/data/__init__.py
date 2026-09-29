"""Market data sources."""

from __future__ import annotations

from pathlib import Path

from .base import MarketData
from .scripmaster import ScripMaster
from .simulated import SimulatedMarketData

__all__ = ["MarketData", "ScripMaster", "SimulatedMarketData", "make_data_source"]


def make_data_source(kind: str, home: Path, underlyings: list[str], seed: int | None = 7) -> MarketData:
    kind = kind.lower()
    if kind in ("sim", "simulated"):
        return SimulatedMarketData(underlyings, seed=seed)
    if kind == "yahoo":
        from .yahoo import YahooMarketData

        return YahooMarketData(scrip_master=ScripMaster(home / "cache"))
    if kind == "kite":
        from .kite import KiteMarketData

        return KiteMarketData()
    raise ValueError(f"Unknown data source {kind!r} (choose sim, yahoo or kite)")
