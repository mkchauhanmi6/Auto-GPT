# Trading playbook (read at the start of every scheduled run)

Demo account only. This is a forward test of discretionary trend + price-action + fundamentals.
Backtests of mechanical versions of these rules (2016–2026, 10 pairs) showed **no statistical edge**,
so the goal is to find out, with a clean journal, whether judgment adds anything. Follow the rules;
"when in doubt, skip" is always a valid decision.

Run everything from the repo root: `python3 -m fx_trader <cmd>`.

## 1. Health check
1. `git pull` on the working branch, then `python3 -m fx_trader status`.
2. If it prints `DRY RUN` or a MetaApi error: log it (`log "..."`), commit, and stop. Never work around it.
3. If the account is not deployed, `status` deploys it; wait ~1 min and rerun.

## 2. Manage open positions first
Positions tagged `[manual]` were opened by the owner. Report them, never touch them.
For each of our positions:
- **Never widen a stop.** The code refuses it.
- **At +1R:** move the stop to entry (breakeven).
- **At +2R or more:** trail the stop behind the most recent H4 swing low (longs) or high (shorts). Keep it at least 1× H4 ATR from price.
- **Close early** only for a written reason:
  - D1 closes beyond the invalidation level stated at entry.
  - The fundamental thesis breaks, e.g. a surprise central-bank pivot or a big data miss against the position.
  - A tier-1 event for either currency is due within 2 hours while the position is below +0.5R and the event risk is lopsided. In this case, closing half is also acceptable.
- A small open loss alone is **not** a reason to close. The stop defines the risk.
- **Friday runs after 17:00 UTC:** close any position whose stop is not yet at breakeven or better (weekend gap risk).

## 3. Look for new trades (all must be true, at most one new trade per run)
1. **Trend (D1):** `d1_trend_score` is ±2 or ±3. Trade only in that direction.
2. **Location:** price has pulled back to value:
   - `h4_dist_ema20_atr` is between −1 and +1, or price is retesting a broken D1 level.
   - Not stretched: D1 RSI below 70 for longs, above 30 for shorts.
3. **Trigger (price action):** a *closed* H4 candle in the trade direction: engulfing, pin bar, or a break of the last H4 swing.
4. **Room:** the next opposing D1 support/resistance is at least 2× the stop distance away. Target ≥2R; the code rejects anything below 1.5R.
5. **Fundamentals:** search the last 48h of news for both currencies:
   - central-bank guidance and rate-path pricing
   - CPI, jobs and GDP surprises
   - risk sentiment
   Skip if the trade fights a clear rate-differential or policy-divergence story. Note the sources in the log.
6. **Calendar:** no tier-1 event for either currency in the next 4h. The code blocks 60 min before and 30 min after.
7. **Stop:**
   - Place it beyond the swing that invalidates the setup, plus 0.25× H4 ATR. It is usually 1–2× H4 ATR.
   - **Take-profit:** just before the next major level.
   - Write the D1 invalidation level in the reason.

Command:
```
python3 -m fx_trader open EURUSD sell --sl 1.14200 --tp 1.12000 \
  --reason "D1 down 3/3; H4 bearish engulfing at EMA20 1.1368; ECB dovish vs Fed hold; invalid D1 close > 1.1420"
```
The code sizes the position at 1% risk and refuses the order if any hard limit fails:
- at most 3 positions and 3% total open risk
- at most 2 positions long (or short) the same currency
- trading halts for the day at a −3% daily loss
- news blackout
- spread limit
- no entries Friday after 18:00 UTC or at weekends

Don't try to work around a refusal.

## 4. Log and save
1. Run `python3 -m fx_trader log "<summary>"` with:
   - account and positions
   - a one-line view per pair: trend, location, trigger yes/no
   - fundamental notes with sources
   - every action taken and why, or "no trade: <reason>"
2. `git add fx_trader/journal && git commit -m "fx_trader: run <UTC time>" && git push -u origin <branch>`.

## 5. Weekly review (last run of Friday)
1. Run `python3 -m fx_trader review --days 7` and `--days 90`, and put the numbers in the log.
2. After 30 closed trades, report the average R to the owner plainly. If it is negative, say that the approach has not shown an edge.
