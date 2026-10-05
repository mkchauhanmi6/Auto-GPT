# Daily run playbook (demo account)

Each scheduled session follows these steps in order. The strategy rules live in
`run.py`; this playbook adds the fundamental and news review around them. Discretion
is limited to the cases listed below, because discretionary overrides are untested.

## 1. Setup
```
git fetch origin claude/gallant-cannon-u3qdm0 && git checkout claude/gallant-cannon-u3qdm0
pip install -q metaapi-cloud-sdk pandas numpy
```
If `METAAPI_TOKEN` or `METAAPI_ACCOUNT_ID` is unset, stop. Report that they must be
added to the environment's variables.

## 2. Account check
`python -m forex_agent.run status`. Confirm the account `type` is a demo (e.g.
`cloud-g2` with a demo server name, or a server containing "Demo"). **If it looks like a
live account, place no trades and report.** Note the balance, equity and open positions.

## 3. Fundamental review (central banks)
`plan --offline` prints the policy rates and their as-of dates. Search the news for
rate decisions by the Fed, ECB, BoE, BoJ, RBA, BoC, SNB and RBNZ since those dates. If
a decision changed a rate and BIS doesn't show it yet, pass it as an override: `--rate JPY=1.5`.
Use only announced decisions, never expectations.

## 4. Risk check (the only discretionary override)
Carry strategies lose most in sudden risk-off crashes (2008, Aug 2024 yen unwind).
Run `close-all --reason "..."` and open nothing new today only if one of these is
happening right now:
- an emergency or unscheduled central-bank action or FX intervention in a traded currency;
- a market-wide panic (e.g. VIX above 35, or USDJPY falling more than 3% in a day).

Write the evidence into the journal. Ordinary news is not a reason to override.

## 5. Plan and execute
```
python -m forex_agent.run plan    [--rate ...]
python -m forex_agent.run execute [--rate ...]
```
Read the plan before executing. Every open has a stop at the broker; there is no target.

## 6. Record
Append one line to `forex_agent/journal/daily.md`:
`date | balance | equity | open legs | actions taken | notes (rate changes, overrides)`.
Then commit `forex_agent/journal/` and push to `claude/gallant-cannon-u3qdm0`.

## 7. Report
End with a short summary: account balance/equity, positions opened or closed and why,
any skipped legs and why, upcoming high-impact events this week.
