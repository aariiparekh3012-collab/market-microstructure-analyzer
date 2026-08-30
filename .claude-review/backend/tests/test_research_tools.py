from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.analytics.backtester import OFIStrategy, run_backtest
from backend.analytics.execution_sim import TWAPExecutor, VWAPExecutor, simulate


def test_backtester_rejects_missing_columns():
    df = pd.DataFrame({"midprice": [100.0]})
    strategy = OFIStrategy()
    with pytest.raises(ValueError, match="missing columns"):
        run_backtest(df, strategy)


def test_backtester_trade_log_reconciles_to_total_pnl():
    frame = pd.DataFrame(
        {
            "midprice": [100, 100, 101, 102, 102, 101, 100, 99, 99, 100],
            "ofi_300s": [0, 0, 5, 6, 0, 0, -5, -6, 0, 0],
        }
    )
    strategy = OFIStrategy(
        lookback=3,
        entry_threshold=1.0,
        exit_threshold=0.4,
        transaction_cost_bps=1.0,
    )
    result = run_backtest(frame, strategy)
    assert result.num_trades > 0
    assert result.equity_curve[-1] == pytest.approx(
        strategy.initial_capital + result.total_pnl
    )
    assert sum(trade["pnl"] for trade in result.trades) == pytest.approx(
        result.total_pnl, abs=1e-3
    )


def _book_frame(rows: int = 20) -> pd.DataFrame:
    data = []
    for tick in range(rows):
        row = {"ltp": 100.05 + tick * 0.01, "ltq": 50 + tick, "volume": 1000 + tick * 50}
        for level in range(1, 6):
            row[f"bid_px_{level}"] = 100.0 - 0.05 * (level - 1) + tick * 0.01
            row[f"ask_px_{level}"] = 100.1 + 0.05 * (level - 1) + tick * 0.01
            row[f"bid_qty_{level}"] = 100
            row[f"ask_qty_{level}"] = 100
        data.append(row)
    return pd.DataFrame(data)


@pytest.mark.parametrize("executor_cls", [TWAPExecutor, VWAPExecutor])
def test_execution_simulator_fills_and_reports_finite_prices(executor_cls):
    result = simulate(_book_frame(), executor_cls(target_qty=500, num_slices=5))
    assert sum(fill[1] for fill in result.fills) == 500
    assert np.isfinite(result.avg_fill_price)
    assert result.last_fill_slippage_bps == result.market_impact_bps


def test_execution_simulator_rejects_empty_data():
    empty_data = pd.DataFrame()
    executor = TWAPExecutor(target_qty=100)

    with pytest.raises(ValueError, match="at least one tick"):
        simulate(empty_data, executor)
