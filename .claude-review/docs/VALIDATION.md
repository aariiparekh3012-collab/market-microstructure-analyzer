# Validation and Reproducibility Guide

This document explains what was changed in the first engineering repair pass,
why each change was required, and how to verify the resulting system.

## What changed and why

| Area | Previous behaviour | Change | Why it now works |
| --- | --- | --- | --- |
| Engine construction | Failed on the first symbol because `window` was not a valid anomaly-detector argument | Passed separate spread, volume, and OFI window parameters | Symbol analytics can now be constructed and warmed up |
| Timestamp handling | Analytics requested `snapshot.timestamp`, but the model defines `snapshot.ts` | Internal code consistently uses `ts`; JSON output remains `timestamp` | The model and analytics agree without changing the public payload name |
| Tick classification | Returned words and was sometimes passed a full snapshot | Accepts a price and returns `+1`, `-1`, or `0` | Signed volumes and returns use valid numeric arithmetic |
| OFI windows | Scanned the full retained history three times per tick | Maintains one queue and running sum per horizon | Expired observations are removed once and updates do not grow with session history |
| Anomaly statistics | Recomputed mean and variance by scanning each window | Maintains rolling sum and squared sum | Spread, volume, and OFI z-scores update consistently with bounded work |
| Volume profile API | Requested a missing engine method and mismatched delta property | Added the accessor and standardised on `cumulative_delta` | The REST endpoint can retrieve existing symbol state safely |
| Advanced estimators | Missing fields could cause arithmetic errors | Added guards and warm-up behaviour | Partial or malformed snapshots return no estimate instead of crashing |
| Trade/quote metric | Was labelled classical Hasbrouck information share | Renamed as a descriptive variance diagnostic | The public claim now matches the implemented mathematics |
| Synthetic source | Used global randomness and wall-clock timestamps | Uses a local seed, optional fixed clock, and explicit tick bound | Repeated research commands produce the same records |
| Backtester | Trade logs omitted entry costs and annualised arbitrary synthetic ticks | Reconciles both costs, uses initial capital, and reports an unannualised trade statistic | P&L, equity, and drawdown calculations are internally consistent |
| Execution simulator | Labelled last-fill movement as market impact | Reports last-fill slippage and documents replay VWAP look-ahead | Output no longer claims causal impact the simulator does not model |
| Profiler | Timed five modules while describing nine | Times all nine implemented modules | Module breakdown and total timing cover the same analytics path |
| Frontend | Read `m.ts` while the backend emitted `timestamp` | Uses the backend field and chooses secure WebSockets on HTTPS | Chart points receive timestamps and deployed HTTPS pages use `wss://` |

## Clean validation sequence

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

ruff check backend scripts run_backtest.py run_execution_sim.py run_profiler.py tests
pytest --cov=backend --cov-report=term-missing

python scripts/generate_sample_data.py --ticks-per-symbol 1000 --seed 42
python run_backtest.py
python run_execution_sim.py
python run_profiler.py --ticks 15000 --seed 42
```

For the frontend:

```bash
cd frontend
npm install
npm run build
```

## Expected validation outcome

- Lint completes without findings.
- Fifteen backend tests pass.
- Backend test coverage is at least the CI floor of 80%.
- The deterministic generator creates equal tick counts for all five symbols.
- Backtest trade P&L reconciles to final equity less initial capital.
- Execution simulations produce finite fills for TWAP and replay VWAP.
- The profiler processes the requested number of ticks and writes raw timings
  plus a JSON summary.
- The frontend produces a Vite production bundle.

Exact latency and trading outputs depend on the machine and configuration.
Synthetic performance is not empirical evidence about NSE securities.

## Evidence boundary

The following are engineering validations:

- automated tests;
- lint and coverage results;
- deterministic regeneration;
- successful backend and frontend builds; and
- internally consistent output files.

They do not validate predictive power, execution profitability, exchange-feed
correctness, or the classical econometric interpretation of adapted tick-level
metrics. Those require licensed real data, domain review, and out-of-sample
empirical testing.

## Legacy reports

`Market_Microstructure_Analyzer_Report.pdf` and
`research/Microstructure_Theory_and_Implementation.pdf` were generated before
this repair. They contain outdated implementation and benchmark statements and
should be treated as historical artifacts until regenerated from a validated
release.
