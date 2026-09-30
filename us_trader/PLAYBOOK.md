# Daily run playbook (scheduled weekdays 15:45 New York time)

The account is Alpaca **paper**. The rules in `config.py` are the exact backtested rules. The owner chose a 60/40 split between ETFs and stocks.
- **Core sleeve (36%):** hold SPY while SPY is above its 200-day average; otherwise hold cash.
- **ETF dip-buys (4 × 6%):** SPY, QQQ, IWM and DIA. Buy when the ETF is above its 200-day average and its 2-day RSI is below 10. Sell when it closes above its 5-day average.
- **Stock dip-buys (8 × 5%):** the same rule on about 100 large caps (`universe.py`). New entries only while SPY is above its 200-day average. Lowest RSI first, at most 2 per sector, and no entry if earnings are due within 7 days.
- **Safety:** if equity falls 20% below its peak, liquidate everything and halt.

Backtest results, 2016–2026:
- 60/40 system: +8.8%/yr, Sharpe 0.98, worst drawdown −13%.
- ETF-only version: +9.3%/yr, Sharpe 1.08, worst drawdown −13%.
- Buy-and-hold SPY: +15.1%/yr, Sharpe 0.88, worst drawdown −34%.
- ETF dip-buys: won 70% of the time, with an average win of +1.2% and an average loss of −1.6%.
- Stock dip-buys: won 65% of the time, averaging +0.2% per trade (average win +1.7%, average loss −2.6%, worst trade −36%). These numbers are **overstated**: the stock list is today's large caps, so companies that failed are missing from the test.

The job is to **follow the rules, not improve them on the fly.**

Run from the repo root with Python 3. The only dependency is the standard library.

1. **Health:** run `python3 -m us_trader status`.
   - If the keys are missing or Alpaca errors: log it, commit, and stop.
   - If the market is closed (holiday or early close): log "market closed", commit, and stop.
2. **Plan:** run `python3 -m us_trader run`. This shows the signals and orders without sending anything.
3. **News check**, only if the plan contains a new ETF or stock entry:
   - Search today's market news: Fed, CPI or jobs data, major geopolitical shocks, trading halts, and for IWM, small-cap and credit stress.
   - For each new stock entry, also search that company's news from the last few days.
   - The rules already buy fear; bad news alone is **not** a reason to skip.
   - Veto only if something is structurally broken or pending within hours: an exchange outage, a circuit breaker already tripped today, or a crisis still unfolding (e.g. a bank failure the same day).
   - For a single stock, veto only for an event that changes the company itself: fraud or accounting allegations, a trading halt, a pending acquisition, bankruptcy risk, or a guidance cut or major regulatory action announced today. An ordinary price drop is what the rule buys.
   - Vetoes are untested and are journaled separately, so they can be judged later.
   - To veto: `run --execute --veto IWM --reason "<cause + source>"`.
   - Exits and the core sleeve can't be vetoed.
4. **Execute:** run `python3 -m us_trader run --execute`. This sends market orders. Sells go first.
5. **Log:** run `python3 -m us_trader log "<summary>"`. The summary covers:
   - equity and today's change
   - the signal table
   - orders sent
   - news notes with sources
   - any veto and why
6. **Save:** commit `us_trader/journal/` and push to the working branch.
7. **Fridays:** run `python3 -m us_trader review` and include it in the log. It reports ETF and stock dip-buys separately. After 30 round trips in either sleeve, compare that sleeve with its backtest numbers above and say plainly whether they hold. For stocks, remember the backtest is overstated.

## Hourly monitor (weekdays 9:45–14:45 New York time)
The rules decide entries and exits on the closing price, so the hourly checks **never buy and never make rule exits**. Those happen only in the 15:45 run.
1. Run `python3 -m us_trader monitor`. It shows equity, each position's move today and since entry, a preview of what the 15:45 run would do at current prices, and ALERTS.
2. If there are no alerts, don't log or commit. Reply with a one-line status.
3. For each alert, search that name's news from today:
   - **Ordinary selling** (market-wide drop, sector rotation, no company news): hold. The rules expect drawdowns.
   - **A company-altering event for a single stock** (fraud or accounting allegations, trading halt, bankruptcy risk, a guidance cut or major regulatory action announced today, a pending acquisition): run `python3 -m us_trader close SYM --reason "<event + source>"`. The index ETFs can't be closed this way.
   - Log what you found and what you did, then commit and push the journal.
4. If the account is within 5 points of the 20% drawdown halt, tell the owner in the reply.
