"""Settings. Defaults are conservative; override in ``$OPTRADE_HOME/config.json``."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import time
from pathlib import Path


def default_home() -> Path:
    return Path(os.environ.get("OPTRADE_HOME", Path.home() / ".optrade"))


@dataclass
class StrategyConfig:
    candle_minutes: int = 5
    fast_ema: int = 9
    slow_ema: int = 21
    trend_ema: int = 50
    rsi_period: int = 14
    rsi_bull: float = 55.0
    rsi_bear: float = 45.0
    # |score| thresholds (score range is roughly -4.5..+4.5)
    strong_score: float = 2.5
    mild_score: float = 1.5
    # implied vol / 20-day realised vol
    iv_rich_ratio: float = 1.15
    iv_cheap_ratio: float = 0.95
    min_days_to_expiry: int = 1  # never open trades in contracts expiring today
    spread_width_steps: int = 4  # NIFTY: 4 x 50 = 200 points
    condor_short_sd: float = 1.0  # short strikes ~1 std-dev move to expiry away
    condor_wing_steps: int = 4
    allow_iron_condor: bool = True
    allow_naked_long: bool = True
    # exits, in terms of the premium paid / received
    long_sl_pct: float = 0.30
    long_target_pct: float = 0.60
    debit_spread_sl_pct: float = 0.50
    debit_spread_target_pct: float = 0.50  # of max profit
    credit_target_pct: float = 0.50  # of credit received
    credit_sl_mult: float = 1.0  # loss = 1x credit received
    min_confidence: float = 0.55


@dataclass
class RiskConfig:
    capital: float = 500_000.0
    risk_per_trade_pct: float = 2.0  # loss at the stop-loss, as % of capital
    max_loss_per_trade_pct: float = 5.0  # worst case (held to expiry), as % of capital
    max_lots: int = 10
    max_open_positions: int = 2
    daily_loss_limit_pct: float = 3.0
    no_new_entries_after: str = "14:45"
    square_off_time: str = "15:15"
    intraday: bool = True  # square off everything at square_off_time
    entry_cooldown_minutes: int = 30  # per underlying, after an exit

    @property
    def no_new_entries_after_t(self) -> time:
        return time.fromisoformat(self.no_new_entries_after)

    @property
    def square_off_t(self) -> time:
        return time.fromisoformat(self.square_off_time)


@dataclass
class CostConfig:
    """Approximate Indian F&O option charges (discount broker, 2026)."""

    brokerage_per_order: float = 20.0
    stt_sell_pct: float = 0.1  # on sell-side premium
    exchange_txn_pct: float = 0.03503
    sebi_pct: float = 0.0001
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0
    slippage_pct: float = 0.5  # of premium, applied against you on paper fills


@dataclass
class DhanConfig:
    base_url: str = "https://sandbox.dhan.co/v2"
    product_type: str = "INTRADAY"  # or MARGIN for carry-forward


@dataclass
class Settings:
    home: Path = field(default_factory=default_home)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    dhan: DhanConfig = field(default_factory=DhanConfig)

    @property
    def config_file(self) -> Path:
        return self.home / "config.json"

    @classmethod
    def load(cls, home: Path | None = None) -> "Settings":
        s = cls(home=Path(home) if home else default_home())
        if s.config_file.exists():
            raw = json.loads(s.config_file.read_text())
            for section in ("strategy", "risk", "costs", "dhan"):
                obj = getattr(s, section)
                names = {f.name for f in fields(obj)}
                for k, v in (raw.get(section) or {}).items():
                    if k not in names:
                        raise ValueError(f"Unknown setting {section}.{k} in {s.config_file}")
                    setattr(obj, k, v)
        return s

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        data = {k: asdict(getattr(self, k)) for k in ("strategy", "risk", "costs", "dhan")}
        self.config_file.write_text(json.dumps(data, indent=2))
