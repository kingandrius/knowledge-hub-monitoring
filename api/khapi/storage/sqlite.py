"""Local SQLite repository (Task 3: store POSTed data in a local database).

The schema matches the Azure SQL table that Task 4 will use, so the switch to
the PaaS database only changes the connection, not the data model.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Iterable

from ..errors import StorageUnavailableError
from ..models import Measurement, StoredMeasurement
from .base import InsertResult, MeasurementRepository, MetricSummary

SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname    TEXT    NOT NULL,
    ip_address  TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT,
    measured_at TEXT    NOT NULL,   -- UTC, ISO 8601
    received_at TEXT    NOT NULL,   -- UTC, ISO 8601
    UNIQUE (hostname, metric, measured_at)
);
CREATE INDEX IF NOT EXISTS ix_measurements_host_metric_time
    ON measurements (hostname, metric, measured_at);
"""

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _to_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(_ISO)


def _from_text(value: str) -> datetime:
    return datetime.strptime(value, _ISO).replace(tzinfo=timezone.utc)


class SQLiteRepository(MeasurementRepository):
    """Stores measurements in one SQLite file; one connection per call."""

    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.path, timeout=5)
        except sqlite3.Error as exc:
            raise StorageUnavailableError(str(exc)) from exc
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _where(hostname, metric, start, end) -> tuple[str, list]:
        clauses, params = [], []
        if hostname is not None:
            clauses.append("hostname = ?")
            params.append(hostname)
        if metric is not None:
            clauses.append("metric = ?")
            params.append(metric)
        if start is not None:
            clauses.append("measured_at >= ?")
            params.append(_to_text(start))
        if end is not None:
            clauses.append("measured_at <= ?")
            params.append(_to_text(end))
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def add_many(self, measurements: Iterable[Measurement]) -> InsertResult:
        rows = list(measurements)
        received = _to_text(datetime.now(timezone.utc))
        try:
            with closing(self._connect()) as conn, conn:  # one transaction
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO measurements (hostname, ip_address,"
                    " metric, value, unit, measured_at, received_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(m.hostname, m.ip_address, m.metric, m.value, m.unit,
                      _to_text(m.measured_at), received) for m in rows],
                )
                accepted = conn.total_changes - before
        except sqlite3.Error as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return InsertResult(accepted=accepted, duplicates=len(rows) - accepted)

    def query(self, *, hostname=None, metric=None, start=None, end=None,
              limit=100) -> list[StoredMeasurement]:
        where, params = self._where(hostname, metric, start, end)
        sql = ("SELECT * FROM measurements" + where +
               " ORDER BY measured_at DESC, id DESC LIMIT ?")
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, [*params, limit]).fetchall()
        except sqlite3.Error as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return [
            StoredMeasurement(
                hostname=r["hostname"], ip_address=r["ip_address"],
                metric=r["metric"], value=r["value"], unit=r["unit"],
                measured_at=_from_text(r["measured_at"]), id=r["id"],
                received_at=_from_text(r["received_at"]))
            for r in rows
        ]

    def summary(self, *, hostname=None, metric=None, start=None,
                end=None) -> list[MetricSummary]:
        where, params = self._where(hostname, metric, start, end)
        sql = ("SELECT hostname, metric, COUNT(*) AS n, AVG(value) AS avg_v,"
               " MIN(value) AS min_v, MAX(value) AS max_v,"
               " MIN(measured_at) AS first_t, MAX(measured_at) AS last_t"
               " FROM measurements" + where +
               " GROUP BY hostname, metric ORDER BY hostname, metric")
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return [
            MetricSummary(
                hostname=r["hostname"], metric=r["metric"], count=r["n"],
                average=r["avg_v"], minimum=r["min_v"], maximum=r["max_v"],
                first=_from_text(r["first_t"]), last=_from_text(r["last_t"]))
            for r in rows
        ]
