"""Uniform JSON error responses and Flask error handlers."""
from __future__ import annotations

import logging
from typing import Any

from flask import Flask, Response, g, jsonify
from werkzeug.exceptions import HTTPException

log = logging.getLogger("khapi.errors")


class StorageUnavailableError(Exception):
    """The database cannot be reached (paused, network down, throttled).

    Mapped to HTTP 503 with ``Retry-After`` so the monitoring script keeps the
    batch in its local buffer and retries later.
    """


def error_response(status: int, code: str, message: str,
                   details: list[dict[str, Any]] | None = None) -> Response:
    """Build the standard error body used by every endpoint."""
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    body["request_id"] = g.get("request_id", "")
    response = jsonify(body)
    response.status_code = status
    return response


_HTTP_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    415: "unsupported_media_type",
}


def register_error_handlers(app: Flask) -> None:
    """Return JSON instead of Flask's default HTML error pages."""

    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        code = _HTTP_CODES.get(exc.code or 500, "http_error")
        return error_response(exc.code or 500, code, exc.description or code)

    @app.errorhandler(StorageUnavailableError)
    def handle_storage(exc: StorageUnavailableError):
        log.error("storage_unavailable error=%s", exc)
        response = error_response(
            503, "storage_unavailable",
            "The database is temporarily unavailable. Retry later.")
        response.headers["Retry-After"] = "60"
        return response

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        log.exception("unhandled_error")
        return error_response(500, "internal_error", "Unexpected server error.")
