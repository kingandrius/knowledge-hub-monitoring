"""HTTP endpoints of the Knowledge Hub monitoring API (version 1)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request

from . import __version__
from .auth import require_api_key
from .errors import error_response
from .models import (HOSTNAME_RE, METRIC_RE, MeasurementBatch, ValidationError,
                     format_timestamp, parse_timestamp)

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
log = logging.getLogger("khapi.api")

MAX_QUERY_LIMIT = 1000


def _repo():
    return current_app.extensions["kh_repository"]


def _settings():
    return current_app.config["KH_SETTINGS"]


def _query_filters() -> dict:
    """Read and validate the shared query-string filters.

    Raises:
        ValidationError: for a malformed host, metric, time or range.
    """
    errors, filters = [], {}
    hostname = request.args.get("hostname")
    if hostname is not None:
        if HOSTNAME_RE.match(hostname):
            filters["hostname"] = hostname.lower()
        else:
            errors.append({"field": "hostname", "issue": "invalid host name"})
    metric = request.args.get("metric")
    if metric is not None:
        if METRIC_RE.match(metric):
            filters["metric"] = metric
        else:
            errors.append({"field": "metric", "issue": "invalid metric name"})
    for name in ("start", "end"):
        raw = request.args.get(name)
        if raw is not None:
            try:
                filters[name] = parse_timestamp(raw)
            except ValueError as exc:
                errors.append({"field": name, "issue": str(exc)})
    if "start" in filters and "end" in filters and filters["start"] > filters["end"]:
        errors.append({"field": "start", "issue": "must not be after end"})
    if errors:
        raise ValidationError(errors)
    return filters


@bp.errorhandler(ValidationError)
def _handle_validation(exc: ValidationError):
    log.warning("validation_failed path=%s errors=%d", request.path, len(exc.details))
    return error_response(400, "validation_error",
                          "The request does not match the API contract.",
                          exc.details)


@bp.get("/health")
def health():
    """Liveness probe for App Service and Azure Monitor.

    Deliberately does not touch the database: a probe every minute would
    keep the serverless database awake and use up its free vCore allowance.
    """
    return jsonify(status="ok", version=__version__,
                   time=format_timestamp(datetime.now(timezone.utc)))


@bp.post("/measurements")
@require_api_key
def create_measurements():
    """Store a batch of measurements from one monitored host (201 Created)."""
    if not request.is_json:
        return error_response(415, "unsupported_media_type",
                              "Content-Type must be application/json.")
    payload = request.get_json(silent=True)
    if payload is None:
        return error_response(400, "invalid_json", "Request body is not valid JSON.")
    settings = _settings()
    batch = MeasurementBatch.from_json(payload, max_items=settings.max_batch_size,
                                       max_age_days=settings.max_age_days)
    result = _repo().add_many(batch.measurements)
    log.info("measurements_stored host=%s accepted=%d duplicates=%d",
             batch.hostname, result.accepted, result.duplicates)
    response = jsonify(accepted=result.accepted, duplicates=result.duplicates,
                       request_id=g.request_id)
    response.status_code = 201
    return response


@bp.get("/measurements")
@require_api_key
def list_measurements():
    """Return stored measurements, newest first (CS1 ``report`` mode)."""
    filters = _query_filters()
    raw_limit = request.args.get("limit", "100")
    if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= MAX_QUERY_LIMIT:
        raise ValidationError([{"field": "limit",
                                "issue": f"must be an integer 1-{MAX_QUERY_LIMIT}"}])
    rows = _repo().query(**filters, limit=int(raw_limit))
    return jsonify(count=len(rows), items=[r.to_dict() for r in rows])


@bp.get("/measurements/summary")
@require_api_key
def summarise_measurements():
    """Return count, average, minimum and maximum per host and metric."""
    filters = _query_filters()
    items = [
        {"hostname": s.hostname, "metric": s.metric, "count": s.count,
         "average": round(s.average, 3), "minimum": s.minimum,
         "maximum": s.maximum, "first": format_timestamp(s.first),
         "last": format_timestamp(s.last)}
        for s in _repo().summary(**filters)
    ]
    return jsonify(items=items)
