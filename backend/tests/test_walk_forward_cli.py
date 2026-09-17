"""Security regression tests for the walk-forward command-line interface."""

from __future__ import annotations

import argparse

import pytest

from run_walk_forward import DATA_ROOT, _safe_output_path


def test_output_path_is_resolved_below_data_directory():
    assert _safe_output_path("ci/results.csv") == DATA_ROOT / "ci" / "results.csv"


@pytest.mark.parametrize(
    "value",
    [
        "../outside.csv",
        "../../outside.json",
        "/tmp/outside.csv",
    ],
)
def test_output_path_rejects_escape_attempts(value: str):
    with pytest.raises(argparse.ArgumentTypeError, match="must stay inside"):
        _safe_output_path(value)
