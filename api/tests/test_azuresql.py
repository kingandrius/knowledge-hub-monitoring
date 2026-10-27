"""Unit tests for the Azure SQL repository, using a fake pyodbc driver.

The real database is only reachable through its private endpoint, so these
tests check the SQL the repository sends, its parameters, transaction
handling and error mapping. The live insert is verified on 23 October.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from khapi import create_app
from khapi.auth import hash_key
from khapi.config import Settings
from khapi.errors import StorageUnavailableError
from khapi.models import Measurement
from khapi.storage.azuresql import ROWS_PER_STATEMENT, AzureSQLRepository


class FakeDriverError(Exception):
    """Stands in for pyodbc.Error."""


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = -1

    def execute(self, sql, params):
        self.conn.statements.append((sql, list(params)))
        if self.conn.fail_on_execute:
            raise FakeDriverError("[42S02] Invalid object name 'dbo.measurements'")
        if sql.lstrip().startswith("INSERT"):
            rows = len(params) // 6
            self.rowcount = max(rows - self.conn.existing, 0)
        return self

    def fetchall(self):
        return self.conn.result_rows


class FakeConnection:
    def __init__(self, result_rows=None, existing=0, fail_on_execute=False):
        self.statements, self.result_rows = [], result_rows or []
        self.existing, self.fail_on_execute = existing, fail_on_execute
        self.committed = self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def connector(conn=None, failures=()):
    """Return a fake connect() that raises the given errors first."""
    calls = {"n": 0, "args": []}
    pending = list(failures)

    def connect(conn_str, timeout):
        calls["n"] += 1
        calls["args"].append((conn_str, timeout))
        if pending:
            raise pending.pop(0)
        return conn

    return connect, calls


def measurement(minutes_ago=0, metric="cpu"):
    moment = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).replace(microsecond=0)
    return Measurement("monitoring-1", "10.10.10.30", metric, 12.5, "percent", moment)


class AzureSQLRepositoryTests(unittest.TestCase):
    def repo(self, conn, failures=()):
        connect, calls = connector(conn, failures)
        return AzureSQLRepository("Driver=x", connect=connect,
                                  error_type=FakeDriverError, retry_delay=0), calls

    def test_insert_uses_parameters_and_one_transaction(self):
        conn = FakeConnection()
        repo, _ = self.repo(conn)
        result = repo.add_many([measurement(1), measurement(1, "memory")])
        self.assertEqual((result.accepted, result.duplicates), (2, 0))
        sql, params = conn.statements[0]
        self.assertIn("WHERE NOT EXISTS", sql)
        self.assertNotIn("monitoring-1", sql)          # values never in the SQL text
        self.assertEqual(params[:5], ["monitoring-1", "10.10.10.30", "cpu", 12.5, "percent"])
        self.assertIsNone(params[5].tzinfo)            # DATETIME2 gets naive UTC
        self.assertTrue(conn.committed and conn.closed)

    def test_large_batch_is_split_below_parameter_limit(self):
        conn = FakeConnection()
        repo, _ = self.repo(conn)
        batch = [measurement(i) for i in range(500)]
        result = repo.add_many(batch)
        self.assertEqual(result.accepted, 500)
        self.assertEqual(len(conn.statements), 2)
        self.assertTrue(all(len(p) <= 2100 for _, p in conn.statements))
        self.assertEqual(len(conn.statements[0][1]), ROWS_PER_STATEMENT * 6)

    def test_existing_and_in_batch_duplicates_are_counted(self):
        conn = FakeConnection(existing=1)
        repo, _ = self.repo(conn)
        same = measurement(5)
        result = repo.add_many([same, same, measurement(6)])
        self.assertEqual((result.accepted, result.duplicates), (1, 2))

    def test_transient_error_is_retried_once(self):
        conn = FakeConnection()
        resuming = FakeDriverError("[40613] Database is not currently available")
        repo, calls = self.repo(conn, failures=[resuming])
        repo.add_many([measurement()])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(calls["args"][0][1], 30)  # login timeout passed through

    def test_persistent_or_permanent_errors_become_storage_unavailable(self):
        for failures in ([FakeDriverError("[40613] a"), FakeDriverError("[40613] b")],
                         [FakeDriverError("[28000] Login failed")]):
            with self.subTest(failures=failures):
                repo, _ = self.repo(FakeConnection(), failures=failures)
                with self.assertRaises(StorageUnavailableError):
                    repo.add_many([measurement()])

    def test_sql_error_during_execute_becomes_storage_unavailable(self):
        repo, _ = self.repo(FakeConnection(fail_on_execute=True))
        with self.assertRaises(StorageUnavailableError):
            repo.query()

    def test_query_builds_filters_and_maps_rows(self):
        stamp = datetime(2026, 10, 15, 9, 0, 0)
        conn = FakeConnection(result_rows=[
            (7, "monitoring-1", "10.10.10.30", "cpu", 12.5, "percent", stamp, stamp)])
        repo, _ = self.repo(conn)
        rows = repo.query(metric="cpu", start=datetime(2026, 10, 1, tzinfo=timezone.utc),
                          limit=5)
        sql, params = conn.statements[0]
        self.assertIn("TOP (?)", sql)
        self.assertIn("metric = ? AND measured_at >= ?", sql)
        self.assertEqual(params[:2], [5, "cpu"])
        self.assertEqual(rows[0].id, 7)
        self.assertEqual(rows[0].measured_at.tzinfo, timezone.utc)

    def test_summary_maps_aggregates(self):
        stamp = datetime(2026, 10, 15, 9, 0, 0)
        conn = FakeConnection(result_rows=[
            ("monitoring-1", "cpu", 3, 10.0, 5.0, 15.0, stamp, stamp)])
        repo, _ = self.repo(conn)
        result = repo.summary(hostname="monitoring-1")
        self.assertEqual((result[0].count, result[0].average), (3, 10.0))
        self.assertIn("GROUP BY hostname, metric", conn.statements[0][0])

    def test_api_returns_503_with_retry_after_when_database_is_down(self):
        repo, _ = self.repo(FakeConnection(),
                            failures=[FakeDriverError("[08001] TCP Provider: timeout")] * 2)
        settings = Settings(api_key_hashes=(hash_key("k"),), storage_backend="memory",
                            log_level="CRITICAL")
        client = create_app(settings, repository=repo).test_client()
        body = {"hostname": "monitoring-1", "ip_address": "10.10.10.30",
                "measurements": [{"metric": "cpu", "value": 1, "unit": "percent",
                                  "timestamp": measurement().measured_at.isoformat()}]}
        response = client.post("/api/v1/measurements", json=body, headers={"X-API-Key": "k"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertEqual(response.get_json()["error"]["code"], "storage_unavailable")

    def test_empty_connection_string_is_rejected(self):
        with self.assertRaises(ValueError):
            AzureSQLRepository("", connect=lambda *a, **k: None)


if __name__ == "__main__":
    unittest.main()
