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

# Core sleeve: hold SPY while it is above its 200-day average, else cash.
CORE_SYMBOL = "SPY"
CORE_WEIGHT = 0.60
TREND_SMA = 200

# Dip-buy sleeve: buy an index ETF that is above its 200-day average when RSI(2) < 10;
# sell when it closes above its 5-day average.
DIP_SYMBOLS = ["SPY", "QQQ", "IWM", "DIA"]
DIP_WEIGHT = 0.10          # per ETF
DIP_RSI_ENTRY = 10
DIP_EXIT_SMA = 5

# Execution / safety
REBALANCE_DRIFT = 0.03     # resize a holding only if it drifted this far (fraction of equity) from target
MAX_DRAWDOWN_HALT = 0.20   # liquidate and halt if equity falls 20% below its peak

HERE = os.path.dirname(__file__)
JOURNAL_DIR = os.path.join(HERE, "journal")
STATE_FILE = os.path.join(JOURNAL_DIR, "state.json")
