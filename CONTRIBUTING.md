# Contributing

Thank you for your interest in improving the Real-Time Market Microstructure
Analyzer.

## Ways to contribute

You can contribute by:

- reporting a reproducible bug;
- proposing a research, analytics, documentation, or user-interface improvement;
- improving tests, examples, or performance measurements;
- reviewing an open issue or pull request; or
- suggesting references or validation procedures relevant to market
  microstructure research.

For a substantial change, please open an issue before implementation so that
the scope and design can be discussed.

## Development workflow

1. Fork the repository and create a focused branch from `main`.
2. Make one logically coherent change.
3. Add or update tests and documentation.
4. Run the relevant checks locally.
5. Open a pull request describing the motivation, implementation, limitations,
   and validation performed.

Suggested branch names include `fix/short-description`,
`feature/short-description`, and `docs/short-description`.

## Local checks

Install the backend development dependencies and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check .
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

For frontend changes, run:

```bash
cd frontend
npm install
npm run build
```

Run the deterministic sample workflow when a change affects research outputs:

```bash
python scripts/generate_sample_data.py --ticks-per-symbol 1000 --seed 42
python run_backtest.py
python run_execution_sim.py
python run_profiler.py --ticks 15000 --seed 42
```

## Research and documentation standards

- Distinguish synthetic, replayed, and live-market data.
- Do not describe synthetic or backtested results as evidence of a deployable
  trading strategy.
- State assumptions, parameter choices, data provenance, and known limitations.
- Add references for implemented estimators or methodological claims.
- Preserve deterministic seeds in reproducibility examples.
- Avoid including brokerage credentials, API keys, personal information, or
  proprietary market data.

## Pull-request review

The project maintainer reviews all pull requests and has final authority over
what is merged into the official repository. A contributor may modify their own
fork, but no contribution changes the official project until it is accepted
and merged by the maintainer.

## Contribution licence

By submitting a contribution, you confirm that:

1. you have the legal right to submit it;
2. it does not knowingly include confidential or improperly licensed material;
   and
3. you agree that it will be licensed under AGPL-3.0-only, the same licence as
   the project.

## Conduct and support

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Use the
issue templates for bugs and feature proposals, and see [SUPPORT.md](SUPPORT.md)
for support boundaries.

