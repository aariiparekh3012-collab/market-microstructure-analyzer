from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backend.analytics.backtester import OFIStrategy
from backend.analytics.walk_forward import WalkForwardConfig, run_walk_forward


def _frame(rows: int = 600) -> pd.DataFrame:
    tick = np.arange(rows)
    return pd.DataFrame(
        {
            "midprice": 100 + tick * 0.01 + np.sin(tick / 9),
            "ofi_300s": np.sin(tick / 5) * 10,
        }
    )


def _candidate() -> tuple[OFIStrategy, ...]:
    return (
        OFIStrategy(
            lookback=50,
            entry_threshold=1.0,
            exit_threshold=0.4,
            transaction_cost_bps=0.0,
        ),
    )


def test_walk_forward_uses_ordered_non_overlapping_test_folds():
    result = run_walk_forward(_frame(), _candidate(), WalkForwardConfig(n_splits=4))
    for previous, current in zip(result.folds, result.folds[1:], strict=False):
        assert previous.test_end == current.test_start
        assert current.train_end == current.test_start
        assert previous.train_end < current.train_end


def test_walk_forward_overrides_candidate_cost_with_harsh_cost():
    result = run_walk_forward(
        _frame(), _candidate(), WalkForwardConfig(n_splits=3, transaction_cost_bps=10.0)
    )
    assert {fold.transaction_cost_bps for fold in result.folds} == {10.0}


def test_higher_cost_cannot_improve_same_candidate_test_pnl():
    low = run_walk_forward(
        _frame(), _candidate(), WalkForwardConfig(n_splits=3, transaction_cost_bps=0.0)
    )
    high = run_walk_forward(
        _frame(), _candidate(), WalkForwardConfig(n_splits=3, transaction_cost_bps=20.0)
    )
    assert high.total_test_net_pnl <= low.total_test_net_pnl


def test_walk_forward_exposes_stability_failure_reasons():
    result = run_walk_forward(
        _frame(),
        _candidate(),
        WalkForwardConfig(
            n_splits=3,
            transaction_cost_bps=100.0,
            min_profitable_fold_ratio=1.0,
            max_pnl_coefficient_of_variation=0.0,
        ),
    )
    assert result.passed is False
    assert result.failure_reasons
    assert result.summary_dict()["stable_across_periods"] is False


def test_zero_mean_pnl_summary_is_strict_json():
    frame = _frame()
    frame["midprice"] = 100.0
    result = run_walk_forward(
        frame, _candidate(), WalkForwardConfig(n_splits=3, transaction_cost_bps=0.0)
    )
    summary = result.summary_dict()
    assert summary["pnl_coefficient_of_variation"] is None
    json.dumps(summary, allow_nan=False)


@pytest.mark.parametrize(
    ("rows", "splits"),
    [(200, 3), (600, 1)],
)
def test_walk_forward_rejects_invalid_sample_or_split_count(rows: int, splits: int):
    with pytest.raises(ValueError):
        run_walk_forward(_frame(rows), _candidate(), WalkForwardConfig(n_splits=splits))
