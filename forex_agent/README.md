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

## FundedNext safety (`prop.py`)
Enable with `PROP_PROFILE` (`fundednext_2step` | `fundednext_1step` | `fundednext_lite`) and
`PROP_INITIAL_BALANCE`. Rules come from the FundedNext help centre (checked 2026-10-05).

| Rule | FundedNext | What this code does |
|---|---|---|
| Daily loss (equity incl. floating, swaps; from initial balance; resets 00:00 server GMT+2/+3) | 5% / 3% / 4% | Worst case (all stops hit, +10% slippage) may use at most half of it (`PROP_BUFFER=0.5`). Day boundary taken as 21:00 UTC (the earlier one) |
| Max loss (static floor at initial × (1 − Y%)) | 10% / 6% / 8% | Same half-room rule. If a firm floor could be breached, the riskiest legs are closed |
| Emergency | breach = account lost | Close all agent positions once 75% of either limit is used |
| Risk per trade | — | min(0.5%, half the daily limit ÷ 6 legs): 0.42% on 2-Step, 0.25% on 1-Step, 0.33% on Lite |
| EAs/bots | only < $50k on MT4/MT5, with approval | At $50k+ the runner places nothing and prints manual instructions. Below that it needs `PROP_EA_APPROVED=1` |
| News reward share (funded Stellar) | 40% of profit kept within ±5 min of high-impact news | No opens or ranking closes from 5 min before to 15 min after; no opens within 4h before |
| Gambling (margin ≥ 70%, all-in) | forbidden | No new trades above 30% margin use |
| Stops | — | Every agent trade has a broker-side stop. Any position without one blocks new entries |
| Weekend/overnight holding | allowed | Allowed; swaps count toward the daily loss and are included via equity |
| Account sharing / third-party management | forbidden | Only the account owner may run this on their own account |

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
