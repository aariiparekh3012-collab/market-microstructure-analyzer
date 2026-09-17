#!/usr/bin/env python3
"""Run harsh-cost walk-forward evaluation across all symbols."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from backend.analytics.walk_forward import WalkForwardConfig, run_walk_forward

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ROOT = (PROJECT_ROOT / "data").resolve()


def _safe_output_path(value: str) -> Path:
    """Resolve an output path and confine writes to the project's data directory."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = DATA_ROOT / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(DATA_ROOT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"output path must stay inside {DATA_ROOT}"
        ) from exc
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=PROJECT_ROOT / "data" / "metrics.csv")
    parser.add_argument(
        "--output",
        type=_safe_output_path,
        default="walk_forward_folds.csv",
        help="CSV path relative to the project data directory",
    )
    parser.add_argument(
        "--summary",
        type=_safe_output_path,
        default="walk_forward_summary.json",
        help="JSON path relative to the project data directory",
    )
    parser.add_argument("--splits", type=int, default=4)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(
            f"Missing {args.input}. Run: python scripts/generate_sample_data.py"
        )

    frame = pd.read_csv(args.input)
    config = WalkForwardConfig(
        n_splits=args.splits,
        transaction_cost_bps=args.cost_bps,
    )
    fold_rows: list[dict[str, object]] = []
    summaries: dict[str, object] = {
        "method": "expanding-window walk-forward",
        "transaction_cost_bps_per_side": args.cost_bps,
        "symbols": {},
    }

    for symbol, symbol_frame in frame.groupby("symbol", sort=True):
        result = run_walk_forward(symbol_frame.reset_index(drop=True), config=config)
        for fold in result.folds:
            fold_rows.append({"symbol": symbol, **fold.summary_dict()})
        summaries["symbols"][symbol] = result.summary_dict()
        status = "PASS" if result.passed else "FAIL"
        print(
            f"{symbol:<12} {status:<4} net={result.total_test_net_pnl:>9.2f} "
            f"profitable_folds={result.profitable_fold_ratio:.0%} "
            f"cv={result.pnl_coefficient_of_variation:.2f}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fold_rows).to_csv(args.output, index=False)
    args.summary.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    print(f"Fold results saved to {args.output}")
    print(f"Summary saved to {args.summary}")


if __name__ == "__main__":
    main()
