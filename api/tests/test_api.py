"""Unit tests for the monitoring API (run: python -m unittest -v)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from khapi import create_app
from khapi.auth import hash_key, is_valid_key
from khapi.config import Settings
from khapi.models import MeasurementBatch, ValidationError
from khapi.storage import MemoryRepository, SQLiteRepository

API_KEY = "test-key-123"
HEADERS = {"X-API-Key": API_KEY}


def ts(minutes_ago: int = 0) -> str:
    """Return a UTC timestamp string some minutes in the past."""
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def batch(**overrides) -> dict:
    """A valid request body; keyword arguments replace top-level fields."""
    body = {
        "hostname": "monitoring-1",
        "ip_address": "10.10.10.30",
        "measurements": [
            {"metric": "cpu_usage", "value": 12.5, "unit": "percent", "timestamp": ts(10)},
            {"metric": "memory_usage", "value": 48.0, "unit": "percent", "timestamp": ts(10)},
        ],
    }
    body.update(overrides)
    return body


def make_settings(**overrides) -> Settings:
    values = {"api_key_hashes": (hash_key(API_KEY),), "storage_backend": "memory",
              "log_level": "CRITICAL"}
    values.update(overrides)
    return Settings(**values)


class AuthTests(unittest.TestCase):
    def test_valid_and_invalid_keys(self):
        hashes = (hash_key("a"), hash_key("b"))
        self.assertTrue(is_valid_key("b", hashes))
        self.assertFalse(is_valid_key("c", hashes))
        self.assertFalse(is_valid_key(None, hashes))
        self.assertFalse(is_valid_key("", hashes))

    def test_app_refuses_to_start_without_keys(self):
        with self.assertRaises(RuntimeError):
            create_app(make_settings(api_key_hashes=()))


class ModelTests(unittest.TestCase):
    def test_valid_batch_is_normalised(self):
        parsed = MeasurementBatch.from_json(batch(hostname="Monitoring-1"))
        self.assertEqual(parsed.hostname, "monitoring-1")
        self.assertEqual(len(parsed.measurements), 2)
        self.assertEqual(parsed.measurements[0].measured_at.tzinfo, timezone.utc)

    def test_all_errors_are_reported_together(self):
        body = batch(ip_address="999.1.1.1", measurements=[
            {"metric": "CPU!", "value": "high", "timestamp": "yesterday"}])
        with self.assertRaises(ValidationError) as ctx:
            MeasurementBatch.from_json(body)
        fields = {d["field"] for d in ctx.exception.details}
        self.assertEqual(fields, {"ip_address", "measurements[0].metric",
                                  "measurements[0].value",
                                  "measurements[0].timestamp"})

    def test_rejects_bool_nan_and_out_of_range_percent(self):
        for value in (True, float("nan"), 101):
            body = batch(measurements=[{"metric": "cpu_usage", "value": value,
                                        "unit": "percent", "timestamp": ts()}])
            with self.subTest(value=value), self.assertRaises(ValidationError):
                MeasurementBatch.from_json(body)

    def test_rejects_naive_future_and_old_timestamps(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        for stamp in ("2026-10-15T10:00:00", future, ts(60 * 24 * 40)):
            body = batch(measurements=[{"metric": "cpu_usage", "value": 1,
                                        "timestamp": stamp}])
            with self.subTest(stamp=stamp), self.assertRaises(ValidationError):
                MeasurementBatch.from_json(body)

    def test_rejects_oversized_batch_and_unknown_fields(self):
        item = {"metric": "cpu_usage", "value": 1, "timestamp": ts()}
        with self.assertRaises(ValidationError):
            MeasurementBatch.from_json(batch(measurements=[item] * 3), max_items=2)
        with self.assertRaises(ValidationError):
            MeasurementBatch.from_json(batch(extra="x"))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(make_settings(), repository=MemoryRepository())
        self.client = self.app.test_client()

    def post(self, body, headers=HEADERS):
        return self.client.post("/api/v1/measurements", json=body, headers=headers)

    def test_health_needs_no_key(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        self.assertTrue(response.headers["X-Request-ID"])

    def test_missing_or_wrong_key_gives_401(self):
        for headers in ({}, {"X-API-Key": "wrong"}):
            with self.subTest(headers=headers):
                response = self.post(batch(), headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.get_json()["error"]["code"], "unauthorized")
                self.assertIn("WWW-Authenticate", response.headers)

    def test_get_endpoints_need_key(self):
        for path in ("/api/v1/measurements", "/api/v1/measurements/summary"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_valid_post_gives_201_and_retry_is_idempotent(self):
        body = batch()
        first = self.post(body)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.get_json()["accepted"], 2)
        second = self.post(body)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.get_json(),
                         {"accepted": 0, "duplicates": 2,
                          "request_id": second.get_json()["request_id"]})

    def test_invalid_body_gives_400_with_details(self):
        response = self.post(batch(hostname="bad host!"))
        self.assertEqual(response.status_code, 400)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], "validation_error")
        self.assertEqual(error["details"][0]["field"], "hostname")

    def test_malformed_json_gives_400_and_wrong_type_415(self):
        bad_json = self.client.post("/api/v1/measurements", data="{not json",
                                    content_type="application/json", headers=HEADERS)
        self.assertEqual(bad_json.status_code, 400)
        not_json = self.client.post("/api/v1/measurements", data="a=b", headers=HEADERS)
        self.assertEqual(not_json.status_code, 415)

    def test_body_too_large_gives_413(self):
        app = create_app(make_settings(max_content_length=100),
                         repository=MemoryRepository())
        response = app.test_client().post(
            "/api/v1/measurements", data=json.dumps(batch()),
            content_type="application/json", headers=HEADERS)
        self.assertEqual(response.status_code, 413)

    def test_unknown_route_and_method_give_json_errors(self):
        self.assertEqual(self.client.get("/api/v1/nope").status_code, 404)
        response = self.client.delete("/api/v1/measurements", headers=HEADERS)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.get_json()["error"]["code"], "method_not_allowed")

    def test_query_filters_and_summary(self):
        self.post(batch())
        rows = self.client.get("/api/v1/measurements?metric=cpu_usage",
                               headers=HEADERS).get_json()
        self.assertEqual(rows["count"], 1)
        self.assertEqual(rows["items"][0]["value"], 12.5)
        summary = self.client.get("/api/v1/measurements/summary?hostname=monitoring-1",
                                  headers=HEADERS).get_json()
        self.assertEqual({s["metric"] for s in summary["items"]},
                         {"cpu_usage", "memory_usage"})

    def test_bad_query_parameters_give_400(self):
        for query in ("limit=0", "limit=abc", "start=yesterday",
                      f"start={ts(1)}&end={ts(10)}"):
            with self.subTest(query=query):
                response = self.client.get(f"/api/v1/measurements?{query}",
                                           headers=HEADERS)
                self.assertEqual(response.status_code, 400)


class SQLiteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SQLiteRepository(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_insert_query_summary_and_duplicates(self):
        parsed = MeasurementBatch.from_json(batch())
        result = self.repo.add_many(parsed.measurements)
        self.assertEqual((result.accepted, result.duplicates), (2, 0))
        again = self.repo.add_many(parsed.measurements)
        self.assertEqual((again.accepted, again.duplicates), (0, 2))
        rows = self.repo.query(metric="memory_usage")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].ip_address, "10.10.10.30")
        summary = self.repo.summary()
        self.assertEqual([s.count for s in summary], [1, 1])

    def test_post_through_api_lands_in_sqlite(self):
        app = create_app(make_settings(), repository=self.repo)
        response = app.test_client().post("/api/v1/measurements", json=batch(),
                                          headers=HEADERS)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.repo.query()), 2)


if __name__ == "__main__":
    unittest.main()
