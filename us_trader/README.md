# us_trader: Claude-run US ETF paper trading on Alpaca

A scheduled Claude session runs every weekday at 15:45 New York time. It applies two backtested rule sets to an Alpaca **paper** account, checks the news before new entries, and commits a journal to `us_trader/journal/`. The code only talks to Alpaca's paper server.

## Strategy and evidence
Backtest on daily adjusted data, 3 bps cost per trade, cash earning T-bill returns:

| | 2001–2015 | 2016–2026 |
|---|---|---|
| Buy & hold SPY | +5.0%/yr, Sharpe 0.34, maxDD −55% | +15.1%/yr, Sharpe 0.88, maxDD −34% |
| SPY 200-day trend filter | +4.7%, 0.49, −21% | +12.0%, 1.02, −19% |
| Sector momentum top-3 + trend | +7.3%, 0.56, −26% | +11.2%, 0.82, −20% |
| RSI(2) dip-buy, SPY/QQQ/IWM/DIA | +3.3%, 0.61, −15% (in market 20% of the time) | +4.9%, 0.88, −10% |
| **Used here: 60% SPY-trend + 4×10% dip-buy** | **+4.3%, 0.60, −14%** | **+9.3%, 1.08, −13%** |

How to read this:
- **Win rates:** dip-buys won 73% of trades in 2001–2015 and 70% in 2016–2026. The median hold was 5 days.
- **Return versus risk:** nothing here beat simply holding SPY on raw return since 2016. What this setup buys is much smaller drawdowns and a better return per unit of risk.
- **Honest test:** both rule sets held up on 2016–2026 data, which the rules were not designed on.

## Setup
Add `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` (paper keys) as environment variables in the Claude cloud environment settings.

## Commands
- `status`: account, positions, sleeve state and today's signals.
- `run`: plan only. Add `--execute` to send orders; this is refused while the market is closed. `--veto SYM --reason ...` skips a new dip-buy entry.
- `log "text"`: append to today's markdown log.
- `review`: dip-buy round trips compared with the backtest.
