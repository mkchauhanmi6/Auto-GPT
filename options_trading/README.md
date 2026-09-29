# optrade: NSE options signals + automatic paper trading

`optrade` watches NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY and:

1. **Generates option-strategy signals.** It reads the trend (EMAs, RSI, VWAP) and the volatility regime (IV vs 20-day realised vol), then picks a matching strategy with strikes, lots, stop-loss and target.
2. **Paper-trades them automatically** in a local paper account. Fills use bid/ask plus slippage, and brokerage, STT, exchange charges, GST and stamp duty are applied. It exits on stop-loss, target, square-off time, expiry day or the daily loss limit.
3. **Mirrors every order** to places where you can follow along:
   * **Sensibull:** a copy-paste order ticket for Sensibull Virtual Trading, printed, saved to `sensibull_tickets.txt` and optionally sent to your phone on Telegram.
   * **Dhan Sandbox** (optional): every leg is placed automatically as a real API order in Dhan's free paper-trading sandbox.

> **About Sensibull.** Sensibull has no public API for placing virtual (or real) orders, so no tool can press "Place order" in Sensibull for you without scraping its private endpoints. That would be fragile and would likely break Sensibull's terms. `optrade` therefore gives you exact tickets to key into Sensibull in under a minute. For *fully automatic* paper orders through an API, use the built-in paper ledger (always on) and the Dhan Sandbox mirror.

This is a paper-trading tool, and its signals are not investment advice. The rules are a sensible starting point, not a proven edge. Track the paper results (`optrade report`) for several weeks before trusting any of it with money.

## Quick start

```bash
cd options_trading
python -m optrade signal -u NIFTY BANKNIFTY      # current signals, no orders
python -m optrade run -u NIFTY BANKNIFTY         # auto paper-trade every 5 min in market hours
python -m optrade positions --all                # open + closed paper positions
python -m optrade report                         # P&L, win rate, drawdown, per-strategy stats
python -m optrade close all                      # square off everything now
python -m optrade simulate --days 10             # offline dry-run of the whole loop on synthetic data
```

It needs only Python 3.10+ and the standard library (optionally `pip install -e .[kite,dev]`). State is stored in `~/.optrade/`; override that with `--home` or `OPTRADE_HOME`.

Example signal (free Yahoo data, so premiums are estimated):

```
=== SIGNAL eef9e48d | NIFTY | Bull Put Spread | confidence 72% ===
Time 29-Sep 11:16 | Spot 22,669.45 | Expiry 06-Oct-2026 | premiums ESTIMATED
Sensibull: Virtual Trading -> NIFTY option chain / Strategy Builder, expiry 06 Oct, add:
  1. BUY  2 lot(s) = 130 qty  NIFTY 06OCT26 22400 PE  @ ~86.20
  2. SELL 2 lot(s) = 130 qty  NIFTY 06OCT26 22600 PE  @ ~140.05
Net credit ~53.85/unit = Rs 7,000
Max profit Rs 7,000 | Max loss Rs 19,000 | Breakeven 22,546.2
Exit: stop-loss at P&L Rs -7,000 | target Rs 3,500 | square off by 15:15
Why: View: MILD BULLISH (score +1.5), IV RICH; EMA9 above EMA21 (+1); ...
```

## Market data (`--data`)

| source | cost | spot / candles | option premiums |
|---|---|---|---|
| `yahoo` (default) | free | Yahoo Finance (may lag a few minutes) | **estimated** with Black-Scholes from India VIX plus skew. Check the real premium in Sensibull |
| `kite` | Kite Connect subscription | live | **real** LTP, bid/ask, OI and implied vol |
| `sim` | free | synthetic | synthetic (for testing) |

Real expiry dates and lot sizes come from the NSE contract list in Dhan's public scrip master (no login needed, cached daily). If it can't be downloaded, `optrade` falls back to the calendar rule: NIFTY weekly on Tuesday, the other indices on the last Tuesday of the month.

**Kite:** `pip install kiteconnect`, then set `KITE_API_KEY` and `KITE_ACCESS_TOKEN`. The token is refreshed daily through the Kite login flow, and candles need the historical-data permission. The Kite integration only *reads* data and never places orders.

## Strategy logic

**Trend score** (about −4.5 to +4.5):
* EMA9 vs EMA21 (±1), plus ±0.5 for a fresh crossover
* price vs EMA50 (±1)
* RSI ≥ 55 / ≤ 45 (±1)
* price vs session VWAP (±1)

**Volatility regime:** ATM implied vol ÷ 20-day realised vol. At ≥ 1.15 options are RICH (favour selling premium); at ≤ 0.95 they are CHEAP (favour buying).

| trend \ IV | cheap / fair | rich |
|---|---|---|
| strong bull | buy ATM CE | bull put credit spread |
| mild bull | bull call debit spread | bull put credit spread |
| neutral | no trade | iron condor (short strikes about 1 SD away) |
| mild bear | bear put debit spread | bear call credit spread |
| strong bear | buy ATM PE | bear call credit spread |

Every structure is **defined-risk**; it never sells naked options. It never opens contracts that expire the same day.

**Exits:**
* Long options: −30% / +60% of premium.
* Debit spreads: −50% of debit / +50% of max profit.
* Credit spreads and condors: +50% of credit / loss equal to the credit.
* Everything is squared off at 15:15 (intraday mode).

**Risk:**
* Position size is set so that hitting the stop loses at most 2% of capital, and the worst case (held to expiry) loses at most 5%.
* At most 2 open positions, one per underlying.
* No new entries after 14:45.
* 30-minute cooldown on an underlying after an exit.
* Trading halts for the day at a 3% daily loss.

All of these settings live in `~/.optrade/config.json`. Run `python -m optrade init-config` to create it, then edit it. For example, set `"capital": 200000` or `"intraday": false` to hold credit trades overnight until the stop-loss, target or expiry day.

## Dhan Sandbox (automatic API paper orders)

1. Sign up at <https://developer.dhanhq.co>, open the **Sandbox** tab and copy the client ID and access token.
2. Set the environment variables:
   ```bash
   export DHAN_SANDBOX_CLIENT_ID=...
   export DHAN_SANDBOX_ACCESS_TOKEN=...
   ```
3. Check the connection with `python -m optrade dhan-check` (shows sandbox funds).
4. Start trading with `python -m optrade run -u NIFTY --dhan-sandbox`.

Every paper entry and exit is then sent to the sandbox as MARKET orders on `NSE_FNO`. Entries send buy legs first; exits close short legs first. The local ledger stays the source of truth for stop-loss, target and P&L.

For safety, the adapter refuses any host that is not a `sandbox` URL, so it cannot place live orders.

## Phone alerts (for keying trades into Sensibull on mobile)

1. Create a bot with @BotFather and send it a message.
2. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Every entry and exit ticket is then pushed to Telegram as well.

## Tests

```bash
pip install pytest && python -m pytest -q
```

## Limitations

* The Yahoo source estimates option premiums, so real premiums in Sensibull will differ, especially far OTM and near expiry.
* NSE trading holidays are not built into the market-hours check. The scrip master handles holiday-shifted expiries.
* `simulate` runs on random-walk data. It checks that the machinery works, but it is not a backtest of the strategy.
