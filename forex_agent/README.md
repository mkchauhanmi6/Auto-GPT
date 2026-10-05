# forex_agent

Research and execution tooling for a rules-based FX strategy (MetaApi / MT5).

## Backtest findings (2026-10-05)

Daily bars from Yahoo Finance, 10 major pairs, 2006–2026. Results are in R (1R = the
amount risked per trade), after typical retail spreads. Fills are conservative:
next-bar open entry, the stop is assumed hit first on an ambiguous bar, and gaps fill at
the open. Rules were designed on 2006–2018 and tested unseen on 2019–2026.

| Variant | 2006–18 avg R | 2006–18 PF | 2019–26 avg R | 2019–26 PF | 2019–26 max DD |
|---|---|---|---|---|---|
| EMA pullback + price-action trigger, 2R target | +0.02 | 1.02 | −0.39 | 0.51 | −39R |
| EMA pullback + price-action trigger, ATR trail | +0.01 | 1.02 | −0.45 | 0.41 | −46R |
| 55-day breakout (trend filter), 2R target | +0.14 | 1.22 | −0.29 | 0.63 | −54R |
| 55-day breakout (trend filter), ATR trail | +0.13 | 1.24 | −0.33 | 0.49 | −56R |
| Random direction, 2R target (control) | −0.06 | 0.92 | +0.06 | 1.08 | −23R |

Conclusion: none of these textbook trend or price-action setups had an edge on FX majors
from 2019 to 2026. They should not be traded with real money. Caveat: Yahoo FX daily
OHLC is imperfect. Re-run on broker candles before drawing final conclusions.

Reproduce: `python -m forex_agent.backtest --entry breakout --exit trail [--baseline]`
