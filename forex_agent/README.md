# forex_agent

Research and execution tooling for a rules-based FX strategy, traded on a MetaApi-linked
MT5 **demo** account. Each scheduled run follows `PLAYBOOK.md`.

## What runs live
G10 interest-rate carry (`run.py`):
- Rank 8 currencies by central-bank policy rate (BIS data).
- Long the top 3 and short the bottom 3, each via its USD pair.
- Each leg has a broker-side stop at 3 x daily ATR(14), sized to risk 0.5% of balance.
- Stopped legs stay flat until next month. Legs whose currency leaves the long/short group are closed.
- No new entries within 4h of a high-impact event for either currency, after a 2% daily
  loss, or beyond 6 open legs.

Setup: set `METAAPI_TOKEN` and `METAAPI_ACCOUNT_ID` (optional: `FOREX_SYMBOL_SUFFIX`,
`FOREX_RISK_PCT`, `FOREX_MAX_OPEN`, `FOREX_DAILY_LOSS_PCT`) in the environment.

## Evidence (2026-10-05)
Daily data: Yahoo Finance, 10 pairs, 2006–2026. Yahoo's daily FX "close" has been a
post-open snapshot since 2011, so closes are rebuilt from the next bar's open (see
`data.yahoo_candles`). Policy rates: BIS. Costs: retail spreads, plus swap at the rate
differential minus a 1% broker markup. Rules were designed on 2006–2018 and checked on 2019–2026.

**Single-trade technical setups** (`backtest.py`): 2 entries (EMA pullback with a
price-action trigger; 55-day breakout) × 2 exits (2R target; ATR trail) × 4 fundamental
filters (none; carry; rate divergence; both).
None of the 16 was profitable in both periods. The best in-sample variant (breakout,
2R, no filter: +0.04R/trade, PF 1.05) lost −0.25R/trade out of sample.

**Monthly portfolios** (`portfolio_backtest.py`):

| Strategy | 2006–18 ann. return / Sharpe | 2019–26 ann. return / Sharpe |
|---|---|---|
| Carry, no stops | −0.2% / −0.02 | +3.1% / 0.58 |
| Carry, 3 ATR stops (live rules) | −1.6% / −0.18 | +3.0% / 0.41 |
| 12-month time-series momentum | −1.3% / −0.18 | −0.3% / −0.06 |
| Carry + momentum blend | −1.2% / −0.23 | +1.4% / 0.35 |

Carry was the only effect that has been positive recently, but over the full 20 years
it is roughly break-even. It is traded on demo as a forward test, not as a proven edge.
Live trading with real money needs a forward-test track record first.

Reproduce: `python -m forex_agent.backtest --entry breakout --exit target --fundamental carry`
and `python -m forex_agent.portfolio_backtest`.
