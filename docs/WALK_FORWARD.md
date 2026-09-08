# Walk-forward evaluation

The walk-forward evaluator tests whether OFI strategy profitability survives
chronological holdouts and deliberately harsh transaction costs. It is an
engineering framework for out-of-sample research, not evidence of a live edge.

## Method

For each symbol, the evaluator:

1. divides observations into four ordered test blocks;
2. expands the training history before each block;
3. selects entry threshold, exit threshold, and lookback only on that history;
4. applies the selected parameters to the next untouched block; and
5. charges **10 bps per side (20 bps round trip)** to every candidate by default.

Each test block starts flat and builds its own rolling signal history. No
position, P&L, or future observation crosses a fold boundary.

## Stability gate

An evaluation passes only when both conditions hold:

- aggregate and median fold net P&L are positive after costs; and
- at least 75% of folds are profitable, with fold P&L coefficient of variation
  no greater than 1.5.

The JSON output names every failure reason. This prevents one unusually strong
period from hiding unstable performance elsewhere.

## Run

```bash
python scripts/generate_sample_data.py --ticks-per-symbol 1000 --seed 42
python run_walk_forward.py
```

Outputs:

- `data/walk_forward_folds.csv` — parameters and net results for every fold;
- `data/walk_forward_summary.json` — symbol-level profitability and stability gate.

Use `--cost-bps`, `--splits`, `--input`, `--output`, and `--summary` to make
assumptions explicit. Generated results are ignored by Git and reproducible
from the declared input, seed, and command.

## Interpretation boundary

Passing on synthetic data validates chronology, accounting, and reporting. A
claim of predictive value requires licensed exchange data, session-aware
splits, realistic taxes and fees, latency assumptions, and independent review.
