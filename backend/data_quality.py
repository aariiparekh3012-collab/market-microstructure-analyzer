"""Conservative market-data validation and append-only quarantine storage."""

from __future__ import annotations

import json
import math
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from numbers import Integral, Real
from pathlib import Path

from .models import BookLevel, OrderBookSnapshot


@dataclass(slots=True)
class DataQualityStats:
    received: int = 0
    rejected: int = 0
    repaired: int = 0
    quarantine_writes: int = 0
    quarantine_write_failures: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validation decision plus the conservatively normalized snapshot."""

    snapshot: OrderBookSnapshot
    original_symbol: object
    rejection_reasons: tuple[str, ...] = ()
    repaired_fields: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.rejection_reasons


class DataQualityGate:
    """Validate snapshots without guessing or silently repairing market data.

    The only automatic repair is trimming and uppercasing the symbol. Structural,
    numerical, sequencing, and book-integrity failures are rejected.
    """

    def __init__(self, allowed_symbols: list[str] | None = None) -> None:
        self.allowed_symbols = set(allowed_symbols or [])
        self.stats = DataQualityStats()
        self._last_ts: dict[str, datetime] = {}
        self._last_volume: dict[str, int] = {}

    def validate(self, snap: OrderBookSnapshot) -> ValidationResult:
        self.stats.received += 1
        original_symbol = snap.symbol
        symbol_is_text = isinstance(original_symbol, str)
        normalized_symbol = original_symbol.strip().upper() if symbol_is_text else ""
        repaired = (
            ("symbol",)
            if symbol_is_text and normalized_symbol != original_symbol
            else ()
        )
        normalized = replace(snap, symbol=normalized_symbol)

        reasons: list[str] = []
        if not symbol_is_text:
            reasons.append("invalid_symbol")
        elif not normalized_symbol:
            reasons.append("empty_symbol")
        elif self.allowed_symbols and normalized_symbol not in self.allowed_symbols:
            reasons.append("unknown_symbol")

        if not isinstance(snap.ts, datetime) or snap.ts.utcoffset() is None:
            reasons.append("invalid_timestamp")

        reasons.extend(_validate_book(snap.bids, snap.asks))

        if snap.ltp is not None and not _positive_real(snap.ltp):
            reasons.append("invalid_ltp")
        if snap.ltq is not None and not _nonnegative_int(snap.ltq):
            reasons.append("invalid_ltq")
        if snap.volume is not None and not _nonnegative_int(snap.volume):
            reasons.append("invalid_volume")

        last_ts = self._last_ts.get(normalized_symbol)
        if isinstance(snap.ts, datetime) and snap.ts.utcoffset() is not None:
            if last_ts is not None and snap.ts <= last_ts:
                reasons.append("non_monotonic_timestamp")

            last_volume = self._last_volume.get(normalized_symbol)
            same_session = last_ts is not None and snap.ts.date() == last_ts.date()
            if (
                same_session
                and last_volume is not None
                and _nonnegative_int(snap.volume)
                and snap.volume < last_volume
            ):
                reasons.append("cumulative_volume_decrease")

        # Preserve deterministic ordering while avoiding duplicate reason labels.
        unique_reasons = tuple(dict.fromkeys(reasons))
        result = ValidationResult(
            snapshot=normalized,
            original_symbol=original_symbol,
            rejection_reasons=unique_reasons,
            repaired_fields=repaired,
        )

        if unique_reasons:
            self.stats.rejected += 1
            for reason in unique_reasons:
                self.stats.rejected_by_reason[reason] += 1
            return result

        if repaired:
            self.stats.repaired += 1
        self._last_ts[normalized_symbol] = snap.ts
        if snap.volume is not None:
            self._last_volume[normalized_symbol] = snap.volume
        return result

    def record_quarantine_write(self, *, success: bool) -> None:
        if success:
            self.stats.quarantine_writes += 1
        else:
            self.stats.quarantine_write_failures += 1

    def summary(self) -> dict:
        return {
            "received": self.stats.received,
            "accepted": self.stats.received - self.stats.rejected,
            "rejected": self.stats.rejected,
            "repaired": self.stats.repaired,
            "quarantine_writes": self.stats.quarantine_writes,
            "quarantine_write_failures": self.stats.quarantine_write_failures,
            "rejected_by_reason": dict(sorted(self.stats.rejected_by_reason.items())),
            "repair_policy": "symbol_trim_and_uppercase_only",
        }


class QuarantineStore:
    """Thread-safe JSONL sink for snapshots rejected by the quality gate."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, result: ValidationResult) -> None:
        record = {
            "quarantined_at": datetime.now(UTC).isoformat(),
            "rejection_reasons": list(result.rejection_reasons),
            "repaired_fields": list(result.repaired_fields),
            "original_symbol": result.original_symbol,
            "snapshot": asdict(result.snapshot),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")


def _validate_book(bids: object, asks: object) -> list[str]:
    reasons: list[str] = []
    if not isinstance(bids, list) or not isinstance(asks, list):
        return ["invalid_book_depth"]
    if not bids or not asks or len(bids) > 5 or len(asks) > 5:
        reasons.append("invalid_book_depth")

    if any(not _valid_level(level) for level in [*bids, *asks]):
        reasons.append("invalid_book_level")
        return reasons

    if any(left.price <= right.price for left, right in zip(bids, bids[1:], strict=False)):
        reasons.append("bids_not_descending")
    if any(left.price >= right.price for left, right in zip(asks, asks[1:], strict=False)):
        reasons.append("asks_not_ascending")
    if bids and asks and bids[0].price >= asks[0].price:
        reasons.append("crossed_or_locked_book")
    return reasons


def _valid_level(level: BookLevel) -> bool:
    return isinstance(level, BookLevel) and (
        _positive_real(level.price)
        and _positive_int(level.qty)
        and _nonnegative_int(level.orders)
    )


def _positive_real(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _positive_int(value: object) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool) and value >= 0
