# Changelog

All notable changes to this project are documented here.

## Unreleased — 2026-08-22

### Fixed

- Repaired the first-snapshot engine crash caused by an invalid anomaly-detector
  constructor argument.
- Standardised analytics code on `OrderBookSnapshot.ts` while keeping the JSON
  API field named `timestamp`.
- Changed tick-rule output from string labels to numeric signs so signed-volume
  and signed-return arithmetic is valid.
- Corrected cumulative-delta property access and added the missing engine
  volume-profile accessor used by the API.
- Added missing-value guards to the Kyle, Amihud, Roll, and trade/quote variance
  estimators.
- Corrected backtest transaction-cost reconciliation and added initial capital
  for meaningful drawdown percentages.
- Replaced inappropriate tick-frequency Sharpe annualisation with an explicitly
  unannualised completed-trade return statistic.
- Corrected frontend metric timestamps and selected `ws://` or `wss://` based on
  the page protocol.
- Repaired profiler/source constructor and stream-signature mismatches.

### Changed

- Replaced linear OFI history scans with per-window queues and running sums.
- Replaced repeated anomaly-window scans with running first and second moments.
- Renamed the non-classical “Hasbrouck Information Share” calculation to a
  descriptive trade/quote variance diagnostic.
- Renamed simulated “market impact” to last-fill slippage; a compatibility
  property remains for callers of the original prototype.
- Made the synthetic source seedable, bounded, mean-reverting, and reproducible
  against a fixed clock when requested.
- Extended the profiler to measure all nine analytics modules.
- Split core, development, research, and live-integration dependencies.

### Removed

- Removed the unused `backend/analytics/impact.py` batch helper, which duplicated
  the active streaming Kyle and Amihud implementations and had no callers.

### Added

- Deterministic sample-data generator for `ticks.csv`, `metrics.csv`, and
  `anomalies.csv`.
- Fifteen automated tests covering core analytics, the full engine warm-up,
  reproducibility, backtesting, execution simulation, and an API/WebSocket smoke
  path.
- GitHub Actions workflow for linting, Python 3.11/3.12 tests, coverage,
  reproducibility smoke checks, and the frontend build.
- Reproducibility and validation guide in `docs/VALIDATION.md`.

### Known limitations

- Angel One SmartAPI ingestion remains an explicit stub.
- Synthetic results validate software behaviour, not market predictability.
- The legacy PDF reports predate this repair and must be regenerated before
  their numerical or implementation claims are cited.
