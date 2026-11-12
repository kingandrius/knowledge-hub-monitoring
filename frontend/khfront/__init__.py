"""Knowledge Hub monitoring web frontend (CS3 Task 1 + 2).

Pages follow the use cases in docs/cs3/use-cases.md:
    /                UC2 current status per host
    /history         UC3 history + averages
    /containers      UC4 container status
    /auth/redirect   UC1 sign-in callback from Entra ID (identity library)
    /auth/logout     UC5 sign out
    /health          liveness probe for Docker and Container Apps (no sign-in)

Every page except /health requires an Entra ID sign-in. Which users may
sign in is decided in Entra ID itself (enterprise app: assignment required,
group grp-kh-monitoring-users), not in this code.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, render_template, request

from .api_client import ApiClient, ApiError, latest_per_host, since

CONTAINER_PREFIX = "container_"
PERIODS = {"1": "Last hour", "24": "Last 24 hours", "168": "Last 7 days",
           "720": "Last 30 days"}
AUTH_SETTINGS = ("KH_TENANT_ID", "KH_CLIENT_ID", "KH_CLIENT_SECRET",
                 "KH_REDIRECT_URI")


def _entra_auth():
    """Build the Entra ID sign-in (identity library) from the environment."""
    missing = [name for name in AUTH_SETTINGS if not os.getenv(name)]
    if missing:
        # Fail closed: never start without sign-in.
        raise RuntimeError(f"Missing settings: {', '.join(missing)} (see .env.example).")
    from identity.flask import Auth
    return Auth(
        None,
        authority=f"https://login.microsoftonline.com/{os.environ['KH_TENANT_ID']}",
        client_id=os.environ["KH_CLIENT_ID"],
        client_credential=os.environ["KH_CLIENT_SECRET"],
        redirect_uri=os.environ["KH_REDIRECT_URI"],
    )


def create_app(client: ApiClient | None = None, auth=None) -> Flask:
    """Create the app. Tests pass a fake client and auth; otherwise use .env."""
    load_dotenv()
    app = Flask(__name__)
    if client is None:
        url, key = os.getenv("KH_API_URL", ""), os.getenv("KH_API_KEY", "")
        if not url or not key:
            raise RuntimeError("Set KH_API_URL and KH_API_KEY (see .env.example).")
        client = ApiClient(url, key)

    # Sessions are stored server-side (Flask-Session); the browser only gets
    # a session ID cookie, never the Entra tokens.
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("KH_COOKIE_SECURE", "1") == "1"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    auth = auth or _entra_auth()
    auth.init_app(app)

    def page(template, context, **values):
        user = context["user"]
        return render_template(template, user=user.get("name")
                               or user.get("preferred_username"), **values)

    @app.errorhandler(ApiError)
    def api_down(exc):
        return render_template("error.html", message=str(exc)), 503

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    @auth.login_required
    def overview(*, context):
        items = client.measurements(start=since(24), limit=1000)
        hosts = latest_per_host(
            [i for i in items if not i["metric"].startswith(CONTAINER_PREFIX)])
        return page("overview.html", context, hosts=hosts)

    @app.get("/history")
    @auth.login_required
    def history(*, context):
        period = request.args.get("period", "24")
        if period not in PERIODS:
            period = "24"
        filters = {"start": since(int(period))}
        for name in ("hostname", "metric"):
            value = request.args.get(name, "").strip()
            if value:
                filters[name] = value
        rows = client.measurements(**filters, limit=200)
        summary = client.summary(**filters)
        return page("history.html", context, rows=rows, summary=summary,
                    periods=PERIODS, period=period,
                    hostname=filters.get("hostname", ""),
                    metric=filters.get("metric", ""))

    @app.get("/containers")
    @auth.login_required
    def containers(*, context):
        items = client.measurements(start=since(1), limit=1000)
        found = latest_per_host(
            [i for i in items if i["metric"].startswith(CONTAINER_PREFIX)])
        return page("containers.html", context, containers=found)

    return app
