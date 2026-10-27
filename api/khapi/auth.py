"""API-key authentication for the protected endpoints.

Clients send the key in the ``X-API-Key`` header over HTTPS. The server keeps
only SHA-256 hashes of valid keys, so reading the App Settings does not reveal
a usable key, and compares them in constant time.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from functools import wraps
from typing import Callable

from flask import current_app, request

from .errors import error_response

API_KEY_HEADER = "X-API-Key"
log = logging.getLogger("khapi.auth")


def hash_key(key: str) -> str:
    """Return the SHA-256 hex digest that is stored for an API key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def is_valid_key(key: str | None, allowed_hashes: tuple[str, ...]) -> bool:
    """Check a presented key against the configured hashes in constant time."""
    if not key:
        return False
    digest = hash_key(key)
    # Compare against every hash (no early exit) to keep timing uniform.
    matches = [hmac.compare_digest(digest, allowed) for allowed in allowed_hashes]
    return any(matches)


def require_api_key(view: Callable) -> Callable:
    """Decorator: reject the request with 401 unless a valid key is sent."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        settings = current_app.config["KH_SETTINGS"]
        if not is_valid_key(request.headers.get(API_KEY_HEADER),
                            settings.api_key_hashes):
            log.warning("auth_failed client=%s path=%s",
                        request.remote_addr, request.path)
            response = error_response(
                401, "unauthorized", f"Missing or invalid {API_KEY_HEADER} header."
            )
            response.headers["WWW-Authenticate"] = f'ApiKey header="{API_KEY_HEADER}"'
            return response
        return view(*args, **kwargs)

    return wrapper
