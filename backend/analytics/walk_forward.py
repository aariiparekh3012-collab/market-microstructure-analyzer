"""Chronological walk-forward evaluation for the OFI strategy.

Each fold selects strategy parameters using only the expanding training
history, then evaluates the selected parameters on the next untouched block.
Transaction costs are overridden by the evaluator so every candidate faces
the same conservative assumption.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import isfinite
from statistics import mean, median, stdev

import pandas as pd

from backend.analytics.backtester import OFIStrategy, run_backtest


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    n_splits: int = 4
    transaction_cost_bps: float = 10.0
    min_profitable_fold_ratio: float = 0.75
    max_pnl_coefficient_of_variation: float = 1.5

    def validate(self) -> None:
        if self.n_splits < 2:
            raise ValueError("n_splits must be at least 2")
        if self.transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps cannot be negative")
        if not 0 <= self.min_profitable_fold_ratio <= 1:
            raise ValueError("min_profitable_fold_ratio must be between 0 and 1")
        if self.max_pnl_coefficient_of_variation < 0:
            raise ValueError("max_pnl_coefficient_of_variation cannot be negative")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    entry_threshold: float
    exit_threshold: float
    lookback: int
    transaction_cost_bps: float
    train_net_pnl: float
    test_net_pnl: float
    test_trades: int
    test_win_rate: float
    test_profit_factor: float
    test_max_drawdown: float

    def summary_dict(self) -> dict[str, int | float]:
        values = asdict(self)
        return {
            key: round(value, 6) if isinstance(value, float) else value
            for key, value in values.items()
        }


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    folds: tuple[WalkForwardFold, ...]
    total_test_net_pnl: float
    median_fold_net_pnl: float
    worst_fold_net_pnl: float
    profitable_fold_ratio: float
    pnl_coefficient_of_variation: float
    profitable_after_costs: bool
    stable_across_periods: bool
    passed: bool
    failure_reasons: tuple[str, ...]

    def summary_dict(self) -> dict[str, object]:
        return {
            "folds": len(self.folds),
            "total_test_net_pnl": round(self.total_test_net_pnl, 6),
            "median_fold_net_pnl": round(self.median_fold_net_pnl, 6),
            "worst_fold_net_pnl": round(self.worst_fold_net_pnl, 6),
            "profitable_fold_ratio": round(self.profitable_fold_ratio, 6),
            "pnl_coefficient_of_variation": (
                round(self.pnl_coefficient_of_variation, 6)
                if isfinite(self.pnl_coefficient_of_variation)
                else None
            ),
            "profitable_after_costs": self.profitable_after_costs,
            "stable_across_periods": self.stable_across_periods,
            "passed": self.passed,
            "failure_reasons": list(self.failure_reasons),
        }


def default_candidates() -> tuple[OFIStrategy, ...]:
    """Small, declared search space to keep selection reproducible."""
    return tuple(
        OFIStrategy(entry_threshold=entry, exit_threshold=exit_, lookback=lookback)
        for entry in (1.25, 1.5, 2.0)
        for exit_ in (0.2, 0.4)
        for lookback in (50, 100)
    )


def _selection_score(strategy: OFIStrategy, frame: pd.DataFrame) -> tuple[float, ...]:
    result = run_backtest(frame, strategy)
    return (
        result.total_pnl,
        result.sharpe_ratio,
        -result.max_drawdown,
        -float(strategy.lookback),
    )


def run_walk_forward(
    df: pd.DataFrame,
    candidates: tuple[OFIStrategy, ...] | None = None,
    config: WalkForwardConfig | None = None,
) -> WalkForwardResult:
    """Select on expanding history and evaluate on chronological holdouts."""
    config = config or WalkForwardConfig()
    config.validate()
    candidates = candidates or default_candidates()
    if not candidates:
        raise ValueError("at least one strategy candidate is required")

    required = {"midprice"}.union(candidate.ofi_column for candidate in candidates)
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"walk-forward data is missing columns: {sorted(missing)}")

    test_size = len(df) // (config.n_splits + 1)
    max_lookback = max(candidate.lookback for candidate in candidates)
    if test_size <= max_lookback:
        minimum = (config.n_splits + 1) * (max_lookback + 1)
        raise ValueError(
            f"insufficient rows for {config.n_splits} folds and lookback "
            f"{max_lookback}; need at least {minimum}"
        )

    first_test = len(df) - config.n_splits * test_size
    folds: list[WalkForwardFold] = []
    for fold_index in range(config.n_splits):
        test_start = first_test + fold_index * test_size
        test_end = test_start + test_size
        train = df.iloc[:test_start].reset_index(drop=True)
        test = df.iloc[test_start:test_end].reset_index(drop=True)

        costed_candidates = tuple(
            replace(candidate, transaction_cost_bps=config.transaction_cost_bps)
            for candidate in candidates
        )
        selected = max(costed_candidates, key=lambda item: _selection_score(item, train))
        train_result = run_backtest(train, selected)
        test_result = run_backtest(test, selected)
        folds.append(
            WalkForwardFold(
                fold=fold_index + 1,
                train_start=0,
                train_end=test_start,
                test_start=test_start,
                test_end=test_end,
                entry_threshold=selected.entry_threshold,
                exit_threshold=selected.exit_threshold,
                lookback=selected.lookback,
                transaction_cost_bps=selected.transaction_cost_bps,
                train_net_pnl=train_result.total_pnl,
                test_net_pnl=test_result.total_pnl,
                test_trades=test_result.num_trades,
                test_win_rate=test_result.win_rate,
                test_profit_factor=test_result.profit_factor,
                test_max_drawdown=test_result.max_drawdown,
            )
        )

    pnls = [fold.test_net_pnl for fold in folds]
    total_pnl = sum(pnls)
    mean_pnl = mean(pnls)
    pnl_cv = stdev(pnls) / abs(mean_pnl) if len(pnls) > 1 and mean_pnl else float("inf")
    profitable_ratio = sum(pnl > 0 for pnl in pnls) / len(pnls)
    profitable = total_pnl > 0 and median(pnls) > 0
    stable = (
        profitable_ratio >= config.min_profitable_fold_ratio
        and pnl_cv <= config.max_pnl_coefficient_of_variation
    )

    failures: list[str] = []
    if not profitable:
        failures.append("not_profitable_after_costs")
    if profitable_ratio < config.min_profitable_fold_ratio:
        failures.append("inconsistent_fold_profitability")
    if pnl_cv > config.max_pnl_coefficient_of_variation:
        failures.append("excessive_pnl_dispersion")

    return WalkForwardResult(
        folds=tuple(folds),
        total_test_net_pnl=total_pnl,
        median_fold_net_pnl=median(pnls),
        worst_fold_net_pnl=min(pnls),
        profitable_fold_ratio=profitable_ratio,
        pnl_coefficient_of_variation=pnl_cv,
        profitable_after_costs=profitable,
        stable_across_periods=stable,
        passed=profitable and stable,
        failure_reasons=tuple(failures),
    )
