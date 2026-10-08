# Run playbook: London breakout forward test (demo run as a FundedNext 2-Step)

The carry strategy is retired (see README). The demo account is traded as if it were a
FundedNext Stellar 2-Step challenge, to measure real fills and the edge before buying one.

## Schedule (UTC, weekdays)
- 07:02 place the day's orders; 08:02-12:02 manage fills; 20:02 time exit; 21:02 fallback.
- Every run is the same command; it does whatever the clock requires:
```
python -m forex_agent.breakout run
```

## Each run
1. Environment: `METAAPI_TOKEN`, `METAAPI_ACCOUNT_ID` set (else source the private
   metaapi.env in the scratchpad; never print or commit it). Export
   `PROP_PROFILE=fundednext_2step PROP_INITIAL_BALANCE=10000 PROP_EA_APPROVED=1`.
2. Run `python -m forex_agent.breakout run`. Read `actions`, `guard` and `ledger`.
3. On a MetaApi timeout ("not connected to broker"), retry once. If it still fails, report it:
   pending orders carry a broker-side stop and expire at 12:00, but a 20:00 exit can be
   missed; the 21:02 run closes anything left before the rollover.
4. Never trade outside `breakout.py`, never loosen `prop.py` or `breakout.py` limits, never
   touch positions or orders without the LB comment and this agent's magic number. No
   discretionary entries or exits.

## Record
- `journal/trades.jsonl` (fills with slippage, skips, closes) and `journal/fn_ledger.json`
  (virtual challenge: phase, commission-adjusted P&L, trading days, results) are written by
  the runner. Commit and push them after the 20:02 run, and whenever a challenge phase
  passes or fails.
- Notify the user on a challenge pass or fail, a guard block, or a failure. Otherwise reply
  in 1-2 lines.

## FundedNext costs being emulated
- Commission: demo charges none, the ledger deducts $7/lot round trip (worst case quoted).
- Swap: none expected, every trade closes by 20:00 UTC before the rollover.
- Spread and slippage: real, from the demo. Slippage = fill vs the range level, per trade.
