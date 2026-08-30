# Contributing

Bug reports, patches, and validation notes are welcome.

## Workflow

1. Fork and branch from `main` (`fix/…`, `feature/…`, `docs/…`).
2. Keep the change focused; add or update tests and docs.
3. Run the local checks:

   ```bash
   ruff check backend scripts run_*.py tests
   pytest --cov=backend --cov-report=term-missing
   ```

4. Open a PR describing motivation, implementation, limitations, and how you
   validated the change. Reference any relevant literature.

For substantial changes, open an issue first so the scope can be discussed.

## Reproducibility

Any change touching the analytics or synthetic feed should preserve
reproducibility under a fixed seed. Include a before/after run of:

```bash
python scripts/generate_sample_data.py --ticks-per-symbol 1000 --seed 42
python run_profiler.py --ticks 15000 --seed 42
```

## Security

Report security issues privately per [`SECURITY.md`](SECURITY.md) rather than
in a public issue.
