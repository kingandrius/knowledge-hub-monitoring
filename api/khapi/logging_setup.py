"""Request and error logging.

Every request produces one line with a request ID, method, path, status and
duration. Logs go to stdout, which App Service forwards to the log stream and
to Azure Monitor (Log Analytics); locally a rotating file can be added.
API keys and request bodies are never logged.
"""
from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, g, request

from .config import Settings

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"


class RequestIdFilter(logging.Filter):
    """Add the current request ID (or '-') to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.request_id = g.get("request_id", "-")
        except RuntimeError:  # outside a request context
            record.request_id = "-"
        return True


def configure_logging(settings: Settings) -> None:
    """Attach stdout (and optional file) handlers to the ``khapi`` logger."""
    logger = logging.getLogger("khapi")
    logger.setLevel(settings.log_level)
    logger.handlers.clear()
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.log_file:
        Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(settings.log_file,
                                            maxBytes=1_000_000, backupCount=5))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())
        logger.addHandler(handler)


def register_request_logging(app: Flask) -> None:
    """Assign a request ID, time the request and log one access line."""
    access_log = logging.getLogger("khapi.access")

    @app.before_request
    def _start() -> None:
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        g.started = time.perf_counter()

    @app.after_request
    def _finish(response):
        duration_ms = (time.perf_counter() - g.get("started", time.perf_counter())) * 1000
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        access_log.log(level, "%s %s status=%d duration_ms=%.1f client=%s",
                       request.method, request.path, response.status_code,
                       duration_ms, request.remote_addr)
        response.headers["X-Request-ID"] = g.get("request_id", "")
        return response
