"""Knowledge Hub monitoring web frontend (CS3 Task 1).

Pages follow the use cases in docs/cs3/use-cases.md:
    /            UC2 current status per host
    /history     UC3 history + averages
    /containers  UC4 container status
    /health      liveness probe for Docker and Container Apps
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, render_template, request

from .api_client import ApiClient, ApiError, latest_per_host, since

CONTAINER_PREFIX = "container_"
PERIODS = {"1": "Last hour", "24": "Last 24 hours", "168": "Last 7 days",
           "720": "Last 30 days"}


def create_app(client: ApiClient | None = None) -> Flask:
    """Create the app. Tests pass a fake client; otherwise use .env."""
    load_dotenv()
    app = Flask(__name__)
    if client is None:
        url, key = os.getenv("KH_API_URL", ""), os.getenv("KH_API_KEY", "")
        if not url or not key:
            raise RuntimeError("Set KH_API_URL and KH_API_KEY (see .env.example).")
        client = ApiClient(url, key)

    @app.errorhandler(ApiError)
    def api_down(exc):
        return render_template("error.html", message=str(exc)), 503

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def overview():
        items = client.measurements(start=since(24), limit=1000)
        hosts = latest_per_host(
            [i for i in items if not i["metric"].startswith(CONTAINER_PREFIX)])
        return render_template("overview.html", hosts=hosts)

    @app.get("/history")
    def history():
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
        return render_template("history.html", rows=rows, summary=summary,
                               periods=PERIODS, period=period,
                               hostname=filters.get("hostname", ""),
                               metric=filters.get("metric", ""))

    @app.get("/containers")
    def containers():
        items = client.measurements(start=since(1), limit=1000)
        found = latest_per_host(
            [i for i in items if i["metric"].startswith(CONTAINER_PREFIX)])
        return render_template("containers.html", containers=found)

    return app
