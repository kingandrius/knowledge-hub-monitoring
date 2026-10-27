"""Knowledge Hub monitoring API (Flask application factory)."""
from __future__ import annotations

__version__ = "1.0.0"

from flask import Flask  # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

from .config import Settings  # noqa: E402
from .errors import register_error_handlers  # noqa: E402
from .logging_setup import configure_logging, register_request_logging  # noqa: E402
from .storage import MeasurementRepository, build_repository  # noqa: E402


def create_app(settings: Settings | None = None,
               repository: MeasurementRepository | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        settings: Explicit settings (tests); defaults to the environment.
        repository: Explicit storage (tests); defaults to the configured one.

    Raises:
        RuntimeError: if no API key hash is configured. The API fails closed
            rather than starting without authentication.
    """
    settings = settings or Settings.from_env()
    if not settings.api_key_hashes:
        raise RuntimeError("KH_API_KEY_HASHES is empty; refusing to start "
                           "without API-key authentication.")
    configure_logging(settings)

    app = Flask(__name__)
    app.config["KH_SETTINGS"] = settings
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length
    app.json.sort_keys = False
    # App Service terminates TLS in front of the app: trust one proxy hop.
    # The on-prem collector has no proxy (KH_PROXY_HOPS=0).
    if settings.proxy_hops > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=settings.proxy_hops,
                                x_proto=settings.proxy_hops)

    app.extensions["kh_repository"] = repository or build_repository(settings)

    register_request_logging(app)
    register_error_handlers(app)

    from .routes import bp
    app.register_blueprint(bp)
    return app
