"""Settings and hard risk limits. The limits here are enforced in code on every order."""
import os

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP"]
OANDA_TOKEN = os.environ.get("OANDA_TOKEN", "")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")  # e.g. 101-004-1234567-001 (practice account)
DRY_RUN = os.environ.get("FX_DRY_RUN") == "1" or not (OANDA_TOKEN and OANDA_ACCOUNT_ID)

RISK_PER_TRADE = 0.01     # fraction of balance lost if the stop is hit
MAX_OPEN_RISK = 0.03      # sum of risk across open positions
MAX_POSITIONS = 3
MAX_PER_CURRENCY = 2      # positions long (or short) the same currency
DAILY_LOSS_HALT = 0.03    # no new entries once today's P&L <= -3% of balance
MIN_REWARD_RISK = 1.5
MAX_SPREAD_ATR = 0.15     # skip if spread > 15% of H4 ATR
NEWS_BEFORE_MIN = 60      # no entries this long before a high-impact event
NEWS_AFTER_MIN = 30       # ...or this long after it

JOURNAL_DIR = os.path.join(os.path.dirname(__file__), "journal")
