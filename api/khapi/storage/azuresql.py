"""Azure SQL Database repository (Task 4: store data in the PaaS database).

Connects with pyodbc and ODBC Driver 18 through the private endpoint. On App
Service the connection string uses ``Authentication=ActiveDirectoryMsi``, so
the API logs in with its managed identity and no password exists anywhere.
The table is created once by an administrator (``sql/schema.sql``); the
identity only has db_datareader and db_datawriter.
"""
from __future__ import annotations

import logging
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence

from ..errors import StorageUnavailableError
from ..models import Measurement, StoredMeasurement
from .base import InsertResult, MeasurementRepository, MetricSummary

log = logging.getLogger("khapi.storage.azuresql")

# SQL Server accepts at most 2,100 parameters per statement; 6 per row.
ROWS_PER_STATEMENT = 300

# Errors worth one retry: serverless database resuming (40613), service busy
# (40197, 40501, 49918-49920), login timeout / connection failure (HYT00, 08S01, 08001).
TRANSIENT_MARKERS = ("40613", "40197", "40501", "49918", "49919", "49920",
                     "HYT00", "08S01", "08001")

INSERT_SQL = """
INSERT INTO dbo.measurements
    (hostname, ip_address, metric, value, unit, measured_at)
SELECT v.hostname, v.ip_address, v.metric, v.value, v.unit, v.measured_at
FROM (VALUES {rows}) AS v (hostname, ip_address, metric, value, unit, measured_at)
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.measurements AS m
    WHERE m.hostname = v.hostname AND m.metric = v.metric
      AND m.measured_at = v.measured_at);
"""
ROW_PLACEHOLDER = ("(CAST(? AS NVARCHAR(253)), CAST(? AS VARCHAR(45)), "
                   "CAST(? AS VARCHAR(50)), CAST(? AS FLOAT), "
                   "CAST(? AS VARCHAR(16)), CAST(? AS DATETIME2(0)))")


def _naive_utc(value: datetime) -> datetime:
    """DATETIME2 has no time zone: store UTC without tzinfo."""
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc)


def _chunks(rows: Sequence, size: int):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


class AzureSQLRepository(MeasurementRepository):
    """Stores measurements in ``dbo.measurements`` in Azure SQL Database.

    Args:
        connection_string: ODBC connection string (KH_SQL_CONNECTION_STRING).
        connect: Function that opens a DB-API connection; defaults to
            ``pyodbc.connect``. Tests pass a fake.
        error_type: Exception class raised by the driver (``pyodbc.Error``).
        retry_delay: Seconds to wait before the single retry of a transient
            connection error.
    """

    def __init__(self, connection_string: str,
                 connect: Callable | None = None,
                 error_type: type[Exception] | None = None,
                 retry_delay: float = 10.0) -> None:
        if not connection_string:
            raise ValueError("KH_SQL_CONNECTION_STRING is empty.")
        if connect is None:
            import pyodbc  # imported here so tests and SQLite mode need no driver
            connect, error_type = pyodbc.connect, pyodbc.Error
        self._connection_string = connection_string
        self._connect_fn = connect
        self._error = error_type or Exception
        self._retry_delay = retry_delay

    # ------------------------------------------------------------ helpers
    def _connect(self):
        """Open a connection, retrying once if the database is resuming."""
        for attempt in (1, 2):
            try:
                return self._connect_fn(self._connection_string, timeout=30)
            except self._error as exc:
                transient = any(code in str(exc) for code in TRANSIENT_MARKERS)
                log.warning("sql_connect_failed attempt=%d transient=%s error=%s",
                            attempt, transient, str(exc)[:200])
                if attempt == 1 and transient:
                    time.sleep(self._retry_delay)
                    continue
                raise StorageUnavailableError(str(exc)) from exc
        raise StorageUnavailableError("unreachable")  # pragma: no cover

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
            params.append(_naive_utc(start))
        if end is not None:
            clauses.append("measured_at <= ?")
            params.append(_naive_utc(end))
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def _fetch(self, sql: str, params: list) -> list:
        try:
            with closing(self._connect()) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return cursor.fetchall()
        except self._error as exc:
            raise StorageUnavailableError(str(exc)) from exc

    # ---------------------------------------------------------- interface
    def add_many(self, measurements: Iterable[Measurement]) -> InsertResult:
        # Drop duplicates inside the batch itself first.
        incoming = list(measurements)
        unique: dict[tuple, Measurement] = {}
        for m in incoming:
            unique.setdefault((m.hostname, m.metric, m.measured_at), m)
        rows = list(unique.values())
        in_batch_duplicates = len(incoming) - len(rows)

        accepted = 0
        try:
            with closing(self._connect()) as conn:
                cursor = conn.cursor()
                for chunk in _chunks(rows, ROWS_PER_STATEMENT):
                    sql = INSERT_SQL.format(rows=", ".join([ROW_PLACEHOLDER] * len(chunk)))
                    params = [value for m in chunk for value in (
                        m.hostname, m.ip_address, m.metric, m.value, m.unit,
                        _naive_utc(m.measured_at))]
                    cursor.execute(sql, params)
                    accepted += max(cursor.rowcount, 0)
                conn.commit()  # one transaction per batch
        except self._error as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return InsertResult(accepted=accepted,
                            duplicates=len(rows) - accepted + in_batch_duplicates)

    def query(self, *, hostname=None, metric=None, start=None, end=None,
              limit=100) -> list[StoredMeasurement]:
        where, params = self._where(hostname, metric, start, end)
        sql = ("SELECT TOP (?) id, hostname, ip_address, metric, value, unit,"
               " measured_at, received_at FROM dbo.measurements" + where +
               " ORDER BY measured_at DESC, id DESC")
        rows = self._fetch(sql, [limit, *params])
        return [
            StoredMeasurement(
                hostname=r[1], ip_address=r[2], metric=r[3], value=float(r[4]),
                unit=r[5], measured_at=_aware(r[6]), id=int(r[0]),
                received_at=_aware(r[7]))
            for r in rows
        ]

    def summary(self, *, hostname=None, metric=None, start=None,
                end=None) -> list[MetricSummary]:
        where, params = self._where(hostname, metric, start, end)
        sql = ("SELECT hostname, metric, COUNT(*), AVG(value), MIN(value),"
               " MAX(value), MIN(measured_at), MAX(measured_at)"
               " FROM dbo.measurements" + where +
               " GROUP BY hostname, metric ORDER BY hostname, metric")
        rows = self._fetch(sql, params)
        return [
            MetricSummary(hostname=r[0], metric=r[1], count=int(r[2]),
                          average=float(r[3]), minimum=float(r[4]),
                          maximum=float(r[5]), first=_aware(r[6]),
                          last=_aware(r[7]))
            for r in rows
        ]
