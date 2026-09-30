# fx_trader: Claude-run MT5 demo trading via MetaApi

Claude trades an **MT5 demo account** through [MetaApi](https://metaapi.cloud), a cloud REST bridge to MetaTrader.
A scheduled Claude session runs every few hours on weekdays. Each run:
1. Checks the account.
2. Manages open trades.
3. Reads charts and news.
4. Maybe places one trade.
5. Commits a journal to `fx_trader/journal/`.

The process is in [PLAYBOOK.md](PLAYBOOK.md). The hard risk limits are in [config.py](config.py) and enforced in code.

## Setup (once)
1. **MT5 demo account.** In MT5 choose *File → Open an Account* and create a demo with any broker. Note the login, the **master** password and the server name.
2. **MetaApi.** Sign up at metaapi.cloud and add the demo account (platform MT5, login, password, server). Copy the **account ID** and the **region** it was deployed to. Check MetaApi's current pricing and free tier.
3. **API token.** Generate one at metaapi.cloud. It can trade every account in that MetaApi profile, so **add only the demo account there**.
4. **Claude environment variables.** Open the cloud environment menu in the session title bar, then *Edit*, and add:
   - `METAAPI_TOKEN`
   - `METAAPI_ACCOUNT_ID`
   - `METAAPI_REGION` (e.g. `new-york`, `london`, `singapore`)
   - `FX_SYMBOL_SUFFIX` (only if your broker names symbols like `EURUSD.m`)

   Never paste the token into chat.
5. Start a new session. Check the connection with `python3 -m fx_trader status`.

Without the variables, every command runs in **dry-run** mode: Yahoo data, and no orders are sent.

## Commands
| Command | Does |
|---|---|
| `status` | account, today's P&L, positions with R-multiple and remaining risk, next 36h of high-impact news |
| `scan [PAIRS]` | technical snapshot: D1 trend score, EMAs, 12m momentum, ATR, RSI, S/R, H4/D1 price-action pattern |
| `open PAIR buy/sell --sl --tp --reason` | risk-gated market order sized at 1% risk |
| `modify ID --sl --tp --reason` | move stop/target (stops can't be widened) |
| `close ID [--volume] --reason` | full or partial close |
| `log "text"` | append to today's markdown log |
| `review --days N` | closed-trade stats |

## Why the rules are what they are
Backtest, Yahoo daily data 2006–2026, 10 pairs, costs included:

| Strategy | 2007–2015 | 2016–2026 |
|---|---|---|
| 55/20-day breakout (Turtle) | profitable (strong 2008, 2015) | lost most years |
| 12-month momentum | +1.3%/yr | +0.9%/yr |
| 3-month momentum | +3.2%/yr | −1.2%/yr |
| Pullback-in-trend | +1.1%/yr | −1.2%/yr |
| Trend + 20d breakout + 3×ATR trail | +0.3%/yr | −2.1%/yr |

No mechanical rule set showed a dependable edge after 2015. Trend direction is kept as a filter, since it's the only element with long-run support. The discretionary layer (price action at value, fundamentals, news avoidance) is what this demo is testing. The journal is there so the result can be measured honestly.
