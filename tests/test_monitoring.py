"""Unit tests for the CS2 send/report features of monitoring.py."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import requests

import monitoring


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.headers = {"X-Request-ID": "test-req"}
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    """Records POSTs and answers with the queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(self, url, params, timeout):
        self.posts.append((url, params))
        return self.responses.pop(0)


ENV = {"KH_API_URL": "https://app-kh-api-test.azurewebsites.net/api/v1",
       "KH_API_KEY": "secret", "KH_HOSTNAME": "monitoring-1",
       "KH_HOST_IP": "10.10.10.30"}


def local(minutes_ago=0, days_ago=0):
    moment = datetime.now() - timedelta(minutes=minutes_ago, days=days_ago)
    return moment.strftime(monitoring.TIME_FORMAT)


class MonitoringSendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "monitoring.db")
        patcher = mock.patch.object(monitoring, "DATABASE_FILE", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        monitoring.create_database()

    def add_rows(self, rows):
        with sqlite3.connect(self.db) as conn:
            conn.executemany(
                "INSERT INTO measurements (timestamp, metric, value) VALUES (?, ?, ?)", rows)

    def unsent_count(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM measurements WHERE sent_at IS NULL").fetchone()[0]

    def test_cs1_database_gets_sent_at_column(self):
        legacy = os.path.join(self.tmp.name, "legacy.db")
        with sqlite3.connect(legacy) as conn:
            conn.execute("CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                         " timestamp TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL)")
            conn.execute("INSERT INTO measurements (timestamp, metric, value)"
                         " VALUES ('2026-09-17 10:56:10', 'cpu', 13.8)")
        with mock.patch.object(monitoring, "DATABASE_FILE", legacy):
            monitoring.create_database()
            monitoring.create_database()  # second run must not fail
        with sqlite3.connect(legacy) as conn:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(measurements)")]
            self.assertIn("sent_at", columns)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0], 1)

    def test_payload_matches_api_contract(self):
        rows = [{"id": 1, "timestamp": local(5), "metric": "cpu", "value": 13.8}]
        payload = monitoring.build_payload(rows, "monitoring-1", "10.10.10.30")
        item = payload["measurements"][0]
        self.assertEqual(set(payload), {"hostname", "ip_address", "measurements"})
        self.assertEqual(item["unit"], "percent")
        self.assertTrue(item["timestamp"].endswith("Z"))

    def test_local_time_is_converted_to_utc(self):
        stamp = "2026-10-15 11:00:00"
        expected = datetime.strptime(stamp, monitoring.TIME_FORMAT).astimezone()
        self.assertEqual(monitoring.to_utc_iso(stamp),
                         expected.astimezone(monitoring.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"))

    def test_successful_send_marks_rows_in_batches(self):
        self.add_rows([(local(i), "cpu", 10.0) for i in range(5)])
        session = FakeSession(FakeResponse(201, {"accepted": 2, "duplicates": 0}),
                              FakeResponse(201, {"accepted": 2, "duplicates": 0}),
                              FakeResponse(201, {"accepted": 1, "duplicates": 0}))
        code = monitoring.send_measurements(batch_size=2, session=session)
        self.assertEqual(code, monitoring.EXIT_OK)
        self.assertEqual(len(session.posts), 3)
        self.assertTrue(session.posts[0][0].endswith("/api/v1/measurements"))
        self.assertEqual(self.unsent_count(), 0)

    def test_unreachable_api_keeps_rows(self):
        self.add_rows([(local(1), "cpu", 10.0)])
        session = FakeSession(requests.ConnectionError("tunnel down"))
        self.assertEqual(monitoring.send_measurements(session=session),
                         monitoring.EXIT_TEMPORARY)
        self.assertEqual(self.unsent_count(), 1)

    def test_503_keeps_rows_and_401_stops(self):
        self.add_rows([(local(1), "cpu", 10.0)])
        for status, code in ((503, monitoring.EXIT_TEMPORARY), (401, monitoring.EXIT_CONFIG),
                             (400, monitoring.EXIT_REJECTED)):
            with self.subTest(status=status):
                session = FakeSession(FakeResponse(status, {"error": {"message": "x"}}))
                self.assertEqual(monitoring.send_measurements(session=session), code)
                self.assertEqual(self.unsent_count(), 1)

    def test_rows_older_than_api_limit_are_not_sent(self):
        self.add_rows([(local(days_ago=40), "cpu", 1.0), (local(1), "cpu", 2.0)])
        session = FakeSession(FakeResponse(201, {"accepted": 1, "duplicates": 0}))
        monitoring.send_measurements(session=session)
        self.assertEqual(len(session.posts[0][1]["measurements"]), 1)
        self.assertEqual(self.unsent_count(), 1)  # the old row stays local

    def test_dry_run_sends_nothing(self):
        self.add_rows([(local(1), "cpu", 10.0)])
        session = FakeSession()
        self.assertEqual(monitoring.send_measurements(dry_run=True, session=session),
                         monitoring.EXIT_OK)
        self.assertEqual(session.posts, [])
        self.assertEqual(self.unsent_count(), 1)

    def test_plain_http_is_refused_except_localhost(self):
        with mock.patch.dict(os.environ, {"KH_API_URL": "http://10.20.16.4/api/v1"}):
            self.assertEqual(monitoring.send_measurements(session=FakeSession()),
                             monitoring.EXIT_CONFIG)
        with mock.patch.dict(os.environ, {"KH_API_URL": "http://127.0.0.1:5000/api/v1"}):
            self.assertEqual(monitoring.load_api_settings()[0],
                             "http://127.0.0.1:5000/api/v1")

    def test_ca_bundle_is_used_for_verification(self):
        bundle = os.path.join(self.tmp.name, "collector.crt")
        with open(bundle, "w") as handle:
            handle.write("dummy")
        with mock.patch.dict(os.environ, {"KH_CA_BUNDLE": bundle}):
            monitoring.load_api_settings()
            self.assertEqual(monitoring.create_session("k").verify, bundle)
        self.assertIs(monitoring.create_session("k").verify, True)

    def test_missing_ca_bundle_is_a_config_error(self):
        with mock.patch.dict(os.environ, {"KH_CA_BUNDLE": "/nope/collector.crt"}):
            with self.assertRaises(monitoring.ConfigError):
                monitoring.load_api_settings()

    def test_missing_settings_give_config_error(self):
        with mock.patch.dict(os.environ, {"KH_API_KEY": ""}), \
                mock.patch.object(monitoring, "load_dotenv"):
            self.assertEqual(monitoring.send_measurements(session=FakeSession()),
                             monitoring.EXIT_CONFIG)

    def test_cloud_report_converts_items(self):
        body = {"count": 1, "items": [{"id": 1, "hostname": "monitoring-1",
                                       "ip_address": "10.10.10.30", "metric": "cpu",
                                       "value": 12.5, "unit": "percent",
                                       "timestamp": "2026-10-15T09:00:00Z"}]}
        session = FakeSession(FakeResponse(200, body))
        rows = monitoring.read_cloud_measurements(metrics=["cpu"], session=session)
        self.assertEqual(rows[0]["metric"], "cpu")
        self.assertEqual(session.posts[0][1]["hostname"], "monitoring-1")


if __name__ == "__main__":
    unittest.main()
