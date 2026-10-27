"""Data models for monitoring measurements and their validation rules.

A measurement mirrors one row of the CS1 ``measurements`` table (timestamp,
metric, value), extended with the host that produced it so that data from
several monitored systems can share one central database.
"""
from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

METRIC_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
UNIT_RE = re.compile(r"^[a-z]{1,16}$")
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
MAX_CLOCK_SKEW = timedelta(minutes=5)


class ValidationError(Exception):
    """Raised when a request body does not match the API contract.

    Attributes:
        details: One ``{"field": ..., "issue": ...}`` dict per problem, so a
            client can fix every error in one go.
    """

    def __init__(self, details: list[dict[str, str]]):
        super().__init__(f"{len(details)} validation error(s)")
        self.details = details


def parse_timestamp(raw: Any) -> datetime:
    """Parse an ISO 8601 timestamp that carries a time zone; return UTC.

    Raises:
        ValueError: if the value is not a string, not ISO 8601, or naive.
    """
    if not isinstance(raw, str):
        raise ValueError("must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("must include a time zone, e.g. 'Z' or '+02:00'")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def format_timestamp(value: datetime) -> str:
    """Format a UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Measurement:
    """One metric value measured on one host at one moment."""

    hostname: str
    ip_address: str
    metric: str
    value: float
    unit: str | None
    measured_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON representation used in API responses."""
        return {
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "timestamp": format_timestamp(self.measured_at),
        }


@dataclass(frozen=True)
class StoredMeasurement(Measurement):
    """A measurement as read back from storage."""

    id: int = 0
    received_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {"id": self.id, **super().to_dict()}
        if self.received_at is not None:
            data["received_at"] = format_timestamp(self.received_at)
        return data


@dataclass(frozen=True)
class MeasurementBatch:
    """The body of ``POST /api/v1/measurements``: one host, many values."""

    hostname: str
    ip_address: str
    measurements: tuple[Measurement, ...]

    @classmethod
    def from_json(
        cls,
        payload: Any,
        *,
        max_items: int = 500,
        max_age_days: int = 30,
        now: datetime | None = None,
    ) -> "MeasurementBatch":
        """Validate a decoded JSON body and build a batch.

        All problems are collected before raising, so the 400 response lists
        every invalid field at once.

        Raises:
            ValidationError: if any field breaks the contract.
        """
        now = now or datetime.now(timezone.utc)
        errors: list[dict[str, str]] = []

        def fail(field: str, issue: str) -> None:
            errors.append({"field": field, "issue": issue})

        if not isinstance(payload, dict):
            raise ValidationError([{"field": "$", "issue": "must be a JSON object"}])

        unknown = set(payload) - {"hostname", "ip_address", "measurements"}
        for key in sorted(unknown):
            fail(key, "unknown field")

        hostname = payload.get("hostname")
        if not isinstance(hostname, str) or not HOSTNAME_RE.match(hostname):
            fail("hostname", "required; must be a valid host name (RFC 1123)")

        ip_raw = payload.get("ip_address")
        ip_text = ""
        try:
            ip_text = str(ipaddress.ip_address(ip_raw))
        except ValueError:
            fail("ip_address", "required; must be a valid IPv4 or IPv6 address")

        items = payload.get("measurements")
        if not isinstance(items, list) or not items:
            fail("measurements", "required; must be a non-empty array")
            items = []
        elif len(items) > max_items:
            fail("measurements", f"must contain at most {max_items} items")
            items = []

        parsed: list[Measurement] = []
        oldest = now - timedelta(days=max_age_days)
        for index, item in enumerate(items):
            prefix = f"measurements[{index}]"
            if not isinstance(item, dict):
                fail(prefix, "must be an object")
                continue
            for key in sorted(set(item) - {"metric", "value", "unit", "timestamp"}):
                fail(f"{prefix}.{key}", "unknown field")

            metric = item.get("metric")
            if not isinstance(metric, str) or not METRIC_RE.match(metric):
                fail(f"{prefix}.metric", "required; lower-case letters, digits, '_' (max 50)")

            value = item.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                fail(f"{prefix}.value", "required; must be a finite number")
                value = None

            unit = item.get("unit")
            if unit is not None and (not isinstance(unit, str) or not UNIT_RE.match(unit)):
                fail(f"{prefix}.unit", "must be lower-case letters (max 16)")
            if unit == "percent" and value is not None and not 0 <= value <= 100:
                fail(f"{prefix}.value", "percent values must be between 0 and 100")

            measured_at = None
            try:
                measured_at = parse_timestamp(item.get("timestamp"))
            except ValueError as exc:
                fail(f"{prefix}.timestamp", f"required; {exc}")
            else:
                if measured_at > now + MAX_CLOCK_SKEW:
                    fail(f"{prefix}.timestamp", "lies in the future")
                elif measured_at < oldest:
                    fail(f"{prefix}.timestamp", f"older than {max_age_days} days")

            if not errors:
                parsed.append(
                    Measurement(
                        hostname=hostname.lower(),
                        ip_address=ip_text,
                        metric=metric,
                        value=float(value),
                        unit=unit,
                        measured_at=measured_at,
                    )
                )

        if errors:
            raise ValidationError(errors)
        return cls(hostname=hostname.lower(), ip_address=ip_text,
                   measurements=tuple(parsed))
