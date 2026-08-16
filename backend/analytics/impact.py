"""Market-impact estimators: Kyle's Lambda, Amihud illiquidity ratio."""
from __future__ import annotations

import numpy as np
import pandas as pd


def kyle_lambda(prices: pd.Series, signed_volume: pd.Series) -> float | None:
    dp = prices.diff().dropna()
    sv = signed_volume.reindex(dp.index).fillna(0)
    if len(dp) < 30 or sv.var() == 0:
        return None
    cov = float(np.cov(dp.values, sv.values, ddof=0)[0, 1])
    var = float(np.var(sv.values, ddof=0))
    return cov / var if var else None


def amihud_illiquidity(returns: pd.Series, rupee_volume: pd.Series) -> float | None:
    df = pd.concat([returns.abs(), rupee_volume], axis=1).dropna()
    df.columns = ["absret", "rv"]
    df = df[df["rv"] > 0]
    if df.empty:
        return None
    return float((df["absret"] / df["rv"]).mean())
