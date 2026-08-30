"""Parquet-based tick storage, partitioned by symbol / date / time-bucket.

Directory layout::

    data/ticks/<symbol>/<YYYY-MM-DD>/<HHMM>.parquet

Design notes
------------
The previous implementation read-then-rewrote the whole parquet file on every
flush (`pq.read_table(path) → concat → pq.write_table(path)`), which made
flush time grow linearly with file size — untenable for a long trading
session.

This version keeps a `pyarrow.parquet.ParquetWriter` open per bucket
(append-in-place, snappy-compressed). Rolling to a new bucket closes the
previous writer cleanly; `close()` flushes and closes all open writers and
is idempotent.

Concurrency
-----------
All mutation is guarded by a single `threading.Lock`. Every disk-writing
method is safe to invoke from `asyncio.to_thread(...)` (which is how the
Streamer calls it — so ingestion never blocks the event loop).
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..models import OrderBookSnapshot

log = logging.getLogger(__name__)


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


_BucketKey = tuple[str, str, str]  # (symbol, YYYY-MM-DD, HHMM)


class TickStore:
    """Append-only parquet store with an open writer per active bucket."""

    def __init__(self, root: Path, roll_minutes: int = 15, flush_every: int = 500):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.roll_minutes = roll_minutes
        self.flush_every = flush_every

        self._buffers: dict[_BucketKey, list[dict]] = defaultdict(list)
        self._writers: dict[_BucketKey, pq.ParquetWriter] = {}
        self._schema: pa.Schema | None = None
        self._lock = threading.Lock()

    def _bucket_key(self, s: OrderBookSnapshot) -> _BucketKey:
        day = s.ts.strftime("%Y-%m-%d")
        minute_bucket = (s.ts.minute // self.roll_minutes) * self.roll_minutes
        bucket = f"{s.ts.strftime('%H')}{minute_bucket:02d}"
        return (s.symbol, day, bucket)

    def _path(self, key: _BucketKey) -> Path:
        symbol, day, bucket = key
        return self.root / symbol / day / f"{bucket}.parquet"

    def append(self, snap: OrderBookSnapshot) -> None:
        key = self._bucket_key(snap)
        with self._lock:
            self._buffers[key].append(_snapshot_to_row(snap))
            if len(self._buffers[key]) >= self.flush_every:
                self._flush_key_locked(key)

    def append_many(self, snaps: Iterable[OrderBookSnapshot]) -> None:
        for s in snaps:
            self.append(s)

    def _flush_key_locked(self, key: _BucketKey) -> None:
        rows = self._buffers.pop(key, [])
        if not rows:
            return
        df = pd.DataFrame(rows)
        table = pa.Table.from_pandas(df, preserve_index=False)

        # Lock in the schema on the very first write so every RowGroup in
        # every bucket shares it.
        if self._schema is None:
            self._schema = table.schema

        writer = self._writers.get(key)
        if writer is None:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            # pyarrow's ParquetWriter can't open in append mode, so a fresh
            # writer means a fresh file. On a process restart the bucket
            # file may already exist — read it once and re-emit it as the
            # first row group of the new writer so history is preserved.
            existing_prefix: pa.Table | None = None
            if path.exists():
                try:
                    existing_prefix = pq.read_table(path)
                    log.info(
                        "TickStore: reopening bucket %s (%d existing rows)",
                        path.name, existing_prefix.num_rows,
                    )
                except Exception:
                    log.exception("TickStore: failed to read existing %s", path)
                    existing_prefix = None
            writer = pq.ParquetWriter(path, self._schema, compression="snappy")
            if existing_prefix is not None:
                writer.write_table(existing_prefix.cast(self._schema, safe=False))
            self._writers[key] = writer

        writer.write_table(table.cast(self._schema, safe=False))

    def flush(self) -> None:
        """Flush any buffered rows but keep writers open."""
        with self._lock:
            for key in list(self._buffers.keys()):
                self._flush_key_locked(key)

    def close(self) -> None:
        """Flush and close every writer. Idempotent — safe on shutdown."""
        with self._lock:
            for key in list(self._buffers.keys()):
                self._flush_key_locked(key)
            for key, w in list(self._writers.items()):
                try:
                    w.close()
                except Exception:
                    log.exception("TickStore: writer close failed for %s", key)
                self._writers.pop(key, None)

    def read_day(self, symbol: str, day: str) -> pd.DataFrame:
        daydir = self.root / symbol / day
        if not daydir.exists():
            return pd.DataFrame()
        # Buckets that are still being written need a flush before their
        # newest rows are visible on disk.
        self.flush()
        frames = [pd.read_parquet(p) for p in sorted(daydir.glob("*.parquet"))]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def __enter__(self) -> "TickStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
