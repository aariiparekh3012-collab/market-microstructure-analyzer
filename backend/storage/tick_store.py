"""Parquet-based tick storage, partitioned by date and symbol.

Directory layout: data/ticks/<symbol>/<YYYY-MM-DD>/<HHMM>.parquet
"""
from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..models import OrderBookSnapshot


def _snapshot_to_row(s: OrderBookSnapshot) -> dict:
    row: dict = {"ts": s.ts, "symbol": s.symbol, "ltp": s.ltp, "ltq": s.ltq, "volume": s.volume}
    for i in range(5):
        b = s.bids[i] if i < len(s.bids) else None
        a = s.asks[i] if i < len(s.asks) else None
        row[f"bid_px_{i+1}"] = b.price if b else None
        row[f"bid_qty_{i+1}"] = b.qty if b else None
        row[f"ask_px_{i+1}"] = a.price if a else None
        row[f"ask_qty_{i+1}"] = a.qty if a else None
    return row


class TickStore:
    def __init__(self, root: Path, roll_minutes: int = 15, flush_every: int = 500):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.roll_minutes = roll_minutes
        self.flush_every = flush_every
        self._buffers: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        self._lock = threading.Lock()

    def _bucket_key(self, s: OrderBookSnapshot) -> tuple[str, str, str]:
        day = s.ts.strftime("%Y-%m-%d")
        minute_bucket = (s.ts.minute // self.roll_minutes) * self.roll_minutes
        bucket = f"{s.ts.strftime('%H')}{minute_bucket:02d}"
        return (s.symbol, day, bucket)

    def append(self, snap: OrderBookSnapshot) -> None:
        key = self._bucket_key(snap)
        with self._lock:
            self._buffers[key].append(_snapshot_to_row(snap))
            if len(self._buffers[key]) >= self.flush_every:
                self._flush_key(key)

    def append_many(self, snaps: Iterable[OrderBookSnapshot]) -> None:
        for s in snaps:
            self.append(s)

    def _flush_key(self, key: tuple[str, str, str]) -> None:
        rows = self._buffers.pop(key, [])
        if not rows:
            return
        symbol, day, bucket = key
        outdir = self.root / symbol / day
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"{bucket}.parquet"
        df = pd.DataFrame(rows)
        table = pa.Table.from_pandas(df, preserve_index=False)
        if path.exists():
            existing = pq.read_table(path)
            table = pa.concat_tables([existing, table])
        pq.write_table(table, path, compression="snappy")

    def flush(self) -> None:
        with self._lock:
            for key in list(self._buffers.keys()):
                self._flush_key(key)

    def read_day(self, symbol: str, day: str) -> pd.DataFrame:
        daydir = self.root / symbol / day
        if not daydir.exists():
            return pd.DataFrame()
        frames = [pd.read_parquet(p) for p in sorted(daydir.glob("*.parquet"))]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
