"""In-memory repository, used by the unit tests."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Iterable

from ..models import Measurement, StoredMeasurement
from .base import InsertResult, MeasurementRepository, MetricSummary


class MemoryRepository(MeasurementRepository):
    """Keeps measurements in a list; data is lost when the process stops."""

    def __init__(self) -> None:
        self._rows: list[StoredMeasurement] = []
        self._keys: set[tuple[str, str, datetime]] = set()
        self._lock = threading.Lock()

    def add_many(self, measurements: Iterable[Measurement]) -> InsertResult:
        accepted = duplicates = 0
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self._lock:
            for m in measurements:
                key = (m.hostname, m.metric, m.measured_at)
                if key in self._keys:
                    duplicates += 1
                    continue
                self._keys.add(key)
                self._rows.append(StoredMeasurement(
                    **vars(m), id=len(self._rows) + 1, received_at=now))
                accepted += 1
        return InsertResult(accepted, duplicates)

    def _filtered(self, hostname, metric, start, end):
        return [
            r for r in self._rows
            if (hostname is None or r.hostname == hostname)
            and (metric is None or r.metric == metric)
            and (start is None or r.measured_at >= start)
            and (end is None or r.measured_at <= end)
        ]

    def query(self, *, hostname=None, metric=None, start=None, end=None,
              limit=100) -> list[StoredMeasurement]:
        rows = sorted(self._filtered(hostname, metric, start, end),
                      key=lambda r: (r.measured_at, r.id), reverse=True)
        return rows[:limit]

    def summary(self, *, hostname=None, metric=None, start=None,
                end=None) -> list[MetricSummary]:
        groups: dict[tuple[str, str], list[StoredMeasurement]] = {}
        for r in self._filtered(hostname, metric, start, end):
            groups.setdefault((r.hostname, r.metric), []).append(r)
        result = []
        for (host, name), rows in sorted(groups.items()):
            values = [r.value for r in rows]
            times = [r.measured_at for r in rows]
            result.append(MetricSummary(
                hostname=host, metric=name, count=len(values),
                average=sum(values) / len(values), minimum=min(values),
                maximum=max(values), first=min(times), last=max(times)))
        return result
