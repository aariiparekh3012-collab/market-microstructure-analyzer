"""Download daily OHLCV for configured symbols using yfinance.
Usage: python scripts/fetch_historical.py --years 2
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yfinance as yf

from backend.config import settings


def fetch(symbol: str, years: int, outdir: Path) -> None:
    yf_symbol = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    df = yf.download(yf_symbol, period=f"{years}y", auto_adjust=False, progress=False)
    if df.empty:
        print(f"[warn] no data for {yf_symbol}")
        return
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{symbol}.parquet"
    df.to_parquet(path)
    print(f"[ok] {yf_symbol} -> {path} ({len(df)} rows)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--out", default="./data/historical")
    args = ap.parse_args()
    outdir = Path(args.out)
    for sym in settings.symbol_list:
        fetch(sym, args.years, outdir)


if __name__ == "__main__":
    main()
