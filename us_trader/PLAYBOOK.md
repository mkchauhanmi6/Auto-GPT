# Daily run playbook (scheduled weekdays 15:45 New York time)

The account is Alpaca **paper**. The rules in `config.py` are the exact backtested rules:
- **Core sleeve:** hold 60% SPY while SPY is above its 200-day average; otherwise hold cash.
- **Dip-buy sleeve:** put 10% each into SPY, QQQ, IWM and DIA. Buy an ETF when it is above its 200-day average and its 2-day RSI is below 10. Sell when it closes above its 5-day average.
- **Safety:** if equity falls 20% below its peak, liquidate everything and halt.

Backtest results, 2016–2026:
- Combined: +9.3%/yr, Sharpe 1.08, worst drawdown −13%.
- Buy-and-hold SPY: +15.1%/yr, Sharpe 0.88, worst drawdown −34%.
- Dip-buys: won 70% of the time, with an average win of +1.2% and an average loss of −1.6%.

The job is to **follow the rules, not improve them on the fly.**

Run from the repo root with Python 3. The only dependency is the standard library.

1. **Health:** run `python3 -m us_trader status`.
   - If the keys are missing or Alpaca errors: log it, commit, and stop.
   - If the market is closed (holiday or early close): log "market closed", commit, and stop.
2. **Plan:** run `python3 -m us_trader run`. This shows the signals and orders without sending anything.
3. **News check**, only if the plan contains a new dip-buy entry:
   - Search today's market news: Fed, CPI or jobs data, major geopolitical shocks, trading halts, and for IWM, small-cap and credit stress.
   - The rules already buy fear; bad news alone is **not** a reason to skip.
   - Veto only if something is structurally broken or pending within hours: an exchange outage, a circuit breaker already tripped today, or a crisis still unfolding (e.g. a bank failure the same day).
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
7. **Fridays:** run `python3 -m us_trader review` and include it in the log. After 30 dip-buy round trips, compare the results with the backtest numbers above and say plainly whether they hold.
