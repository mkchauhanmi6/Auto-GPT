"""Strategy parameters. These are the exact rules that were backtested; change them only with a new backtest."""
import os

KEY_FILE = os.path.expanduser("~/.alpaca_paper_keys")  # KEY=VALUE lines, outside the repo; never commit keys


def _key(name):
    if os.environ.get(name):
        return os.environ[name]
    if os.path.exists(KEY_FILE):
        for line in open(KEY_FILE):
            k, _, v = line.strip().partition("=")
            if k == name:
                return v
    return ""


API_KEY = _key("ALPACA_API_KEY_ID")
API_SECRET = _key("ALPACA_API_SECRET_KEY")

# Split: 60% ETF sleeves (core 36% + 4 x 6% dip-buys), 40% stock dip-buys (8 x 5%).
# Core sleeve: hold SPY while it is above its 200-day average, else cash.
CORE_SYMBOL = "SPY"
CORE_WEIGHT = 0.36
TREND_SMA = 200

# Dip-buy sleeve: buy an index ETF that is above its 200-day average when RSI(2) < 10;
# sell when it closes above its 5-day average.
DIP_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA"]
DIP_WEIGHT = 0.06          # per ETF
DIP_RSI_ENTRY = 10
DIP_EXIT_SMA = 5

# Stock sleeve: same dip-buy rule on the large caps in universe.py, only while SPY is above its 200-day
# average; lowest RSI(2) first; at most 2 per sector; no entry if earnings are due within 7 calendar days.
STOCK_WEIGHT = 0.05        # per stock
STOCK_SLOTS = 8
SECTOR_CAP = 2
EARNINGS_BLACKOUT_DAYS = 7

# Execution / safety
REBALANCE_DRIFT = 0.03     # resize a holding only if it drifted this far (fraction of equity) from target
MAX_DRAWDOWN_HALT = 0.20   # liquidate and halt if equity falls 20% below its peak

HERE = os.path.dirname(__file__)
JOURNAL_DIR = os.path.join(HERE, "journal")
STATE_FILE = os.path.join(JOURNAL_DIR, "state.json")
