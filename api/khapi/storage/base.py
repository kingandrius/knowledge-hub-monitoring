"""Storage interface shared by the memory, SQLite and Azure SQL back ends.

The routes only talk to this interface, so moving from the local SQLite file
(Task 3) to Azure SQL Database (Task 4) does not change any route code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from ..models import Measurement, StoredMeasurement


@dataclass(frozen=True)
class InsertResult:
    """Outcome of storing a batch.

    Attributes:
        accepted: Rows newly written.
        duplicates: Rows skipped because the same host, metric and timestamp
            already exist (a retried batch is therefore harmless).
    """

    accepted: int
    duplicates: int


@dataclass(frozen=True)
class MetricSummary:
    """Aggregates for one host and metric over the requested period."""

    hostname: str
    metric: str
    count: int
    average: float
    minimum: float
    maximum: float
    first: datetime
    last: datetime


class MeasurementRepository(ABC):
    """Abstract store for measurements."""

    @abstractmethod
    def add_many(self, measurements: Iterable[Measurement]) -> InsertResult:
        """Store measurements, skipping exact duplicates."""

    @abstractmethod
    def query(self, *, hostname: str | None = None, metric: str | None = None,
              start: datetime | None = None, end: datetime | None = None,
              limit: int = 100) -> list[StoredMeasurement]:
        """Return measurements, newest first, filtered by the arguments."""

    @abstractmethod
    def summary(self, *, hostname: str | None = None, metric: str | None = None,
                start: datetime | None = None,
                end: datetime | None = None) -> list[MetricSummary]:
        """Return count/avg/min/max per host and metric (CS1 ``--average``)."""
