"""Unit tests for the monitoring frontend (fake API client, no network)."""
import unittest

from khfront import create_app
from khfront.api_client import ApiError, latest_per_host

ROWS = [  # newest first, as the API returns them
    {"hostname": "staffpc1", "metric": "cpu", "value": 20.0, "unit": "percent",
     "timestamp": "2026-11-12T10:05:00Z"},
    {"hostname": "staffpc1", "metric": "cpu", "value": 99.0, "unit": "percent",
     "timestamp": "2026-11-12T10:00:00Z"},
    {"hostname": "staffpc1", "metric": "memory", "value": 55.5, "unit": "percent",
     "timestamp": "2026-11-12T10:05:00Z"},
    {"hostname": "kh-api", "metric": "container_up", "value": 1, "unit": None,
     "timestamp": "2026-11-12T10:05:00Z"},
    {"hostname": "kh-api", "metric": "container_cpu", "value": 1.5, "unit": "percent",
     "timestamp": "2026-11-12T10:05:00Z"},
]


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def measurements(self, **params):
        self.calls.append(params)
        if self.fail:
            raise ApiError("The monitoring API cannot be reached.")
        return ROWS

    def summary(self, **params):
        return [{"hostname": "staffpc1", "metric": "cpu", "count": 2,
                 "average": 59.5, "minimum": 20.0, "maximum": 99.0}]


class FrontendTests(unittest.TestCase):
    def client(self, fake=None):
        self.fake = fake or FakeClient()
        return create_app(client=self.fake).test_client()

    def test_latest_keeps_newest_value(self):
        hosts = latest_per_host(ROWS)
        self.assertEqual(hosts["staffpc1"]["cpu"], 20.0)
        self.assertEqual(hosts["staffpc1"]["memory"], 55.5)

    def test_overview_shows_hosts_not_containers(self):
        page = self.client().get("/").get_data(as_text=True)
        self.assertIn("staffpc1", page)
        self.assertNotIn("kh-api", page)

    def test_containers_page_shows_only_containers(self):
        page = self.client().get("/containers").get_data(as_text=True)
        self.assertIn("kh-api", page)
        self.assertIn("running", page)
        self.assertNotIn("staffpc1", page)

    def test_history_passes_filters_to_api(self):
        response = self.client().get("/history?hostname=staffpc1&metric=cpu&period=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.fake.calls[0]["hostname"], "staffpc1")
        self.assertEqual(self.fake.calls[0]["metric"], "cpu")
        self.assertIn("59.5", response.get_data(as_text=True))

    def test_history_ignores_unknown_period(self):
        response = self.client().get("/history?period=9999")
        self.assertEqual(response.status_code, 200)

    def test_api_down_gives_503_page(self):
        response = self.client(FakeClient(fail=True)).get("/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("cannot be reached", response.get_data(as_text=True))

    def test_health(self):
        self.assertEqual(self.client().get("/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
