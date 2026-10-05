"""Risk limits and instrument settings. The trade CLI enforces these as hard guardrails:
no command can exceed them, whatever the analysis says."""
import os

PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
    "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP",
]

# Broker symbols sometimes carry a suffix (e.g. "EURUSD.m", "EURUSDm"). Set via env.
SYMBOL_SUFFIX = os.environ.get("FOREX_SYMBOL_SUFFIX", "")

RISK = {
    # % of balance lost if the stop is hit, per trade.
    "risk_per_trade_pct": float(os.environ.get("FOREX_RISK_PCT", "0.5")),
    "max_open_trades": int(os.environ.get("FOREX_MAX_OPEN", "3")),
    # No new trades once today's realized + floating loss reaches this % of balance.
    "daily_loss_limit_pct": float(os.environ.get("FOREX_DAILY_LOSS_PCT", "2.0")),
    # Max trades sharing the same currency in the same direction (correlation cap).
    "max_same_currency_exposure": 2,
    # Skip new entries if a high-impact event for either currency is this close.
    "news_blackout_hours": 4,
}

# Magic number tags this agent's trades so manual trades are never touched.
MAGIC = 20261005

# Typical retail spreads (pips) used for backtest costs.
_SPREAD_PIPS = {"EURUSD": 1.0, "GBPUSD": 1.5, "USDJPY": 1.2, "AUDUSD": 1.3, "USDCAD": 1.8,
                "USDCHF": 1.7, "NZDUSD": 1.8, "EURJPY": 2.0, "GBPJPY": 3.0, "EURGBP": 1.5}


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


def spread_price(pair: str) -> float:
    return _SPREAD_PIPS.get(pair, 2.0) * pip_size(pair)


def broker_symbol(pair: str) -> str:
    return pair + SYMBOL_SUFFIX
