"""Small client for the Knowledge Hub monitoring API (read-only).

The frontend never touches the database: it reads everything through the
existing API with its own API key. The key stays on the server; the browser
only ever talks to the frontend.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests


class ApiError(Exception):
    """The API could not be reached or did not answer 200."""


class ApiClient:
    """Calls GET /measurements and GET /measurements/summary."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 5) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        try:
            response = requests.get(f"{self.base_url}{path}", params=params,
                                    headers={"X-API-Key": self.api_key},
                                    timeout=self.timeout)
        except requests.RequestException as exc:
            raise ApiError("The monitoring API cannot be reached.") from exc
        if response.status_code != 200:
            raise ApiError(f"The monitoring API answered {response.status_code}.")
        return response.json()

    def measurements(self, **params) -> list[dict]:
        """Measurements, newest first."""
        return self._get("/measurements", params)["items"]

    def summary(self, **params) -> list[dict]:
        """Count, average, minimum and maximum per host and metric."""
        return self._get("/measurements/summary", params)["items"]


def since(hours: int) -> str:
    """ISO 8601 UTC timestamp for 'now minus N hours' (API filter format)."""
    moment = datetime.now(timezone.utc) - timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def latest_per_host(items: list[dict]) -> dict[str, dict]:
    """Group newest-first rows as {host: {"time": ..., metric: value}}.

    Only the first (newest) value per host and metric is kept.
    """
    hosts: dict[str, dict] = {}
    for item in items:
        host = hosts.setdefault(item["hostname"], {"time": item["timestamp"]})
        host.setdefault(item["metric"], item["value"])
    return dict(sorted(hosts.items()))
