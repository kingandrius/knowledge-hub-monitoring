"""Tests for the collector -> Azure relay (run from api/: python -m unittest -v)."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest import mock

import requests

from khapi import create_app
from khapi.auth import hash_key
from khapi.config import Settings
from khapi.models import Measurement
from khapi.storage import SQLiteRepository
from tools import relay

AZURE = "https://app-kh-api-test.azurewebsites.net/api/v1"


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.headers = {"X-Request-ID": "req-1"}
        self.text = str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.posts = []

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def ok(n):
    return FakeResponse(201, {"accepted": n, "duplicates": 0})


def measurement(host, ip, minutes_ago=0, days_ago=0, metric="cpu"):
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago, days=days_ago)
            ).replace(microsecond=0)
    return Measurement(hostname=host, ip_address=ip, metric=metric, value=12.5,
                       unit="percent", measured_at=when)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "collector.db")
        self.repo = SQLiteRepository(self.path)
        self.conn = relay.connect(self.path)
        self.addCleanup(self.conn.close)

    def waiting(self):
        return self.conn.execute("SELECT COUNT(*) FROM measurements"
                                 " WHERE forwarded_at IS NULL").fetchone()[0]

    def test_forwarded_column_is_added_and_collector_still_inserts(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(measurements)")}
        self.assertIn("forwarded_at", cols)
        result = self.repo.add_many([measurement("staffpc1", "10.10.20.100")])
        self.assertEqual(result.accepted, 1)
        self.assertEqual(self.waiting(), 1)

    def test_one_request_per_host_keeps_original_identity(self):
        self.repo.add_many([measurement("staffpc1", "10.10.20.100", m) for m in (1, 2)])
        self.repo.add_many([measurement("webserver", "10.10.40.10", 1)])
        session = FakeSession(ok(2), ok(1))
        self.assertEqual(relay.relay(self.conn, AZURE, session), relay.EXIT_OK)
        hosts = [(body["hostname"], body["ip_address"], len(body["measurements"]))
                 for _, body in session.posts]
        self.assertEqual(hosts, [("staffpc1", "10.10.20.100", 2),
                                 ("webserver", "10.10.40.10", 1)])
        self.assertEqual(self.waiting(), 0)
        item = session.posts[0][1]["measurements"][0]
        self.assertEqual(set(item), {"metric", "value", "unit", "timestamp"})
        self.assertTrue(item["timestamp"].endswith("Z"))

    def test_batches_are_limited(self):
        self.repo.add_many([measurement("staffpc1", "10.10.20.100", m) for m in range(5)])
        session = FakeSession(ok(2), ok(2), ok(1))
        relay.relay(self.conn, AZURE, session, batch_size=2)
        self.assertEqual([len(b["measurements"]) for _, b in session.posts], [2, 2, 1])

    def test_unreachable_or_503_keeps_rows(self):
        self.repo.add_many([measurement("staffpc1", "10.10.20.100")])
        for answer in (requests.ConnectionError(), FakeResponse(503)):
            code = relay.relay(self.conn, AZURE, FakeSession(answer))
            self.assertEqual(code, relay.EXIT_TEMPORARY)
            self.assertEqual(self.waiting(), 1)

    def test_401_and_400_stop_without_marking(self):
        self.repo.add_many([measurement("staffpc1", "10.10.20.100")])
        self.assertEqual(relay.relay(self.conn, AZURE, FakeSession(FakeResponse(401))),
                         relay.EXIT_CONFIG)
        self.assertEqual(relay.relay(self.conn, AZURE, FakeSession(FakeResponse(400))),
                         relay.EXIT_REJECTED)
        self.assertEqual(self.waiting(), 1)

    def test_rows_too_old_for_azure_stay_local(self):
        self.repo.add_many([measurement("staffpc1", "10.10.20.100", days_ago=29, minutes_ago=60)])
        session = FakeSession()
        self.assertEqual(relay.relay(self.conn, AZURE, session), relay.EXIT_OK)
        self.assertEqual(session.posts, [])

    def test_dry_run_sends_nothing(self):
        self.repo.add_many([measurement("staffpc1", "10.10.20.100")])
        session = FakeSession()
        relay.relay(self.conn, AZURE, session, dry_run=True)
        self.assertEqual((session.posts, self.waiting()), ([], 1))

    def test_status_counts_per_host(self):
        self.repo.add_many([measurement("a", "10.10.10.10", 1), measurement("a", "10.10.10.10", 2),
                            measurement("b", "10.10.10.20", 1)])
        relay.mark_forwarded(self.conn, [1])
        rows = {r["hostname"]: (r["total"], r["waiting"]) for r in relay.status(self.conn)}
        self.assertEqual(rows, {"a": (2, 1), "b": (1, 1)})

    def test_settings_require_https_and_key(self):
        env = {"KH_SQLITE_PATH": self.path, "KH_RELAY_URL": "http://x/api/v1",
               "KH_RELAY_KEY": "k"}
        with mock.patch.dict(os.environ, env), mock.patch.object(relay, "load_dotenv"):
            with self.assertRaises(relay.ConfigError):
                relay.load_settings()
        env["KH_RELAY_URL"] = AZURE
        env["KH_RELAY_KEY"] = ""
        with mock.patch.dict(os.environ, env), mock.patch.object(relay, "load_dotenv"):
            with self.assertRaises(relay.ConfigError):
                relay.load_settings()


class CollectorProxyTests(unittest.TestCase):
    def make(self, hops):
        settings = Settings(api_key_hashes=(hash_key("k"),), storage_backend="memory",
                            proxy_hops=hops)
        return create_app(settings).test_client()

    def test_collector_ignores_forwarded_for(self):
        seen = {}
        for hops in (0, 1):
            client = self.make(hops)
            app = client.application

            @app.get("/whoami")
            def whoami():
                from flask import request
                seen[hops] = request.remote_addr
                return "ok"

            client.get("/whoami", headers={"X-Forwarded-For": "6.6.6.6"},
                       environ_base={"REMOTE_ADDR": "10.10.20.100"})
        self.assertEqual(seen, {0: "10.10.20.100", 1: "6.6.6.6"})


if __name__ == "__main__":
    unittest.main()
