"""Relay: forward measurements from the on-prem collector to the Azure API.

Runs on Monitoring-1 (10.10.10.30). The agents on every host send their
measurements to the collector (this API with the SQLite back end); this
script then forwards everything that has not been forwarded yet to the
Azure API, one host per request, in batches of at most 500.

Why a relay instead of every host sending to Azure directly:
  * cost: the serverless Azure SQL database is woken once per relay run
    (every 4 h), not once per host;
  * security: only Monitoring-1 holds the Azure API key and needs a path
    through the VPN to the API private endpoint;
  * resilience: while the tunnel is down, data keeps collecting on-prem.

A row is marked as forwarded only after Azure answers 201 Created; resending
is harmless because the API ignores duplicates (host + metric + timestamp).

Settings (environment or the .env file in the working directory):
    KH_SQLITE_PATH   collector database (the same value the collector uses)
    KH_RELAY_URL     Azure API base URL, https://.../api/v1
    KH_RELAY_KEY     Azure API key (password manager), never a collector key
    KH_RELAY_CA_BUNDLE  optional certificate file to trust

Usage (from the api/ directory):
    python tools/relay.py [--dry-run] [--batch-size N] [--status]

Exit codes (for cron): 0 OK, 1 temporary failure (retry later),
2 configuration or authentication error, 3 data rejected by the API.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ISO = "%Y-%m-%dT%H:%M:%SZ"
MAX_BATCH_SIZE = 500
MAX_AGE_DAYS = 29          # Azure refuses measurements older than 30 days
REQUEST_TIMEOUT = (5, 90)  # a paused serverless database needs ~1 minute

EXIT_OK, EXIT_TEMPORARY, EXIT_CONFIG, EXIT_REJECTED = 0, 1, 2, 3


class ConfigError(Exception):
    """Raised when a relay setting is missing or unsafe."""


def log(message: str, error: bool = False) -> None:
    """Print one time-stamped line (cron appends it to relay.log)."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", file=sys.stderr if error else sys.stdout)


def load_settings() -> tuple[str, str, str, str]:
    """Return (database path, Azure URL, Azure key, CA bundle).

    Raises:
        ConfigError: if a setting is missing, the URL is not HTTPS, or a
            file does not exist.
    """
    load_dotenv()
    db_path = os.getenv("KH_SQLITE_PATH", "instance/khapi.db")
    url = os.getenv("KH_RELAY_URL", "").rstrip("/")
    key = os.getenv("KH_RELAY_KEY", "")
    ca_bundle = os.getenv("KH_RELAY_CA_BUNDLE", "")

    if not url or not key:
        raise ConfigError("KH_RELAY_URL and KH_RELAY_KEY must be set.")
    if urlparse(url).scheme != "https":
        raise ConfigError("KH_RELAY_URL must use https://.")
    if not os.path.isfile(db_path):
        raise ConfigError(f"collector database not found: {db_path}")
    if ca_bundle and not os.path.isfile(ca_bundle):
        raise ConfigError(f"KH_RELAY_CA_BUNDLE file not found: {ca_bundle}")
    return db_path, url, key, ca_bundle


def connect(db_path: str) -> sqlite3.Connection:
    """Open the collector database and add the forwarded_at column once.

    The collector's own schema does not know this column; its INSERT names
    its columns explicitly, so the extra column does not affect it.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(measurements)")}
    if "forwarded_at" not in columns:
        with conn:
            conn.execute("ALTER TABLE measurements ADD COLUMN forwarded_at TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_measurements_forwarded"
                         " ON measurements (forwarded_at)")
    return conn


def oldest_allowed(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=MAX_AGE_DAYS)).strftime(ISO)


def next_batch(conn: sqlite3.Connection, batch_size: int) -> list[sqlite3.Row]:
    """Return up to batch_size unforwarded rows of ONE host (oldest host first).

    The API takes one hostname and IP address per request, so rows are
    grouped by both.
    """
    oldest = oldest_allowed()
    first = conn.execute(
        "SELECT hostname, ip_address FROM measurements"
        " WHERE forwarded_at IS NULL AND measured_at >= ?"
        " ORDER BY id LIMIT 1", (oldest,)).fetchone()
    if first is None:
        return []
    return conn.execute(
        "SELECT id, hostname, ip_address, metric, value, unit, measured_at"
        " FROM measurements"
        " WHERE forwarded_at IS NULL AND measured_at >= ?"
        " AND hostname = ? AND ip_address = ?"
        " ORDER BY id LIMIT ?",
        (oldest, first["hostname"], first["ip_address"], batch_size)).fetchall()


def build_payload(rows: list[sqlite3.Row]) -> dict:
    """Build the POST body; host identity is kept from the original agent."""
    items = []
    for row in rows:
        item = {"metric": row["metric"], "value": float(row["value"]),
                "timestamp": row["measured_at"]}
        if row["unit"] is not None:
            item["unit"] = row["unit"]
        items.append(item)
    return {"hostname": rows[0]["hostname"], "ip_address": rows[0]["ip_address"],
            "measurements": items}


def mark_forwarded(conn: sqlite3.Connection, ids: list[int]) -> None:
    stamp = datetime.now(timezone.utc).strftime(ISO)
    with conn:
        conn.executemany("UPDATE measurements SET forwarded_at = ? WHERE id = ?",
                         [(stamp, row_id) for row_id in ids])


def status(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per host: rows stored, rows still waiting, newest measurement."""
    return conn.execute(
        "SELECT hostname, COUNT(*) AS total,"
        " SUM(CASE WHEN forwarded_at IS NULL THEN 1 ELSE 0 END) AS waiting,"
        " MAX(measured_at) AS newest"
        " FROM measurements GROUP BY hostname ORDER BY hostname").fetchall()


class PinnedSession(requests.Session):
    """Session whose ``verify`` setting always wins.

    Plain requests lets a REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE environment
    variable override ``session.verify``, which would silently replace the
    pinned collector certificate with another bundle.
    """

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        if verify is None:
            verify = self.verify
        return super().merge_environment_settings(url, proxies, stream, verify, cert)


def create_session(api_key: str, ca_bundle: str = "") -> requests.Session:
    """HTTP session with the key and back-off retries (POST is idempotent)."""
    retry = Retry(total=3, connect=3, read=1, status=2, backoff_factor=2,
                  status_forcelist=[502, 503, 504],
                  allowed_methods=frozenset(["POST"]),
                  respect_retry_after_header=True, raise_on_status=False)
    session = PinnedSession()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"X-API-Key": api_key, "Accept": "application/json"})
    if ca_bundle:
        session.verify = ca_bundle
    return session


def describe_error(response) -> str:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return response.text[:200]
    details = "; ".join(f"{d.get('field')}: {d.get('issue')}"
                        for d in error.get("details", []))
    return f"{error.get('message', '')} {details}".strip()


def relay(conn: sqlite3.Connection, url: str, session, batch_size: int = MAX_BATCH_SIZE,
          dry_run: bool = False) -> int:
    """Forward all waiting rows. Returns an exit code."""
    sent = duplicates = requests_made = 0
    hosts: set[str] = set()

    too_old = conn.execute(
        "SELECT COUNT(*) FROM measurements WHERE forwarded_at IS NULL"
        " AND measured_at < ?", (oldest_allowed(),)).fetchone()[0]
    if too_old:
        log(f"Note: {too_old} row(s) older than {MAX_AGE_DAYS} days stay on-prem only.")

    while True:
        rows = next_batch(conn, batch_size)
        if not rows:
            break
        payload = build_payload(rows)

        if dry_run:
            log(f"Dry run: would send {len(rows)} row(s) of {payload['hostname']}"
                f" ({payload['ip_address']}) to {url}/measurements")
            return EXIT_OK

        try:
            response = session.post(f"{url}/measurements", json=payload,
                                    timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            log(f"Error: Azure API not reachable ({type(exc).__name__}); "
                f"rows kept for the next run.", error=True)
            return EXIT_TEMPORARY

        request_id = response.headers.get("X-Request-ID", "-")
        if response.status_code == 201:
            body = response.json()
            mark_forwarded(conn, [row["id"] for row in rows])
            sent += body.get("accepted", 0)
            duplicates += body.get("duplicates", 0)
            requests_made += 1
            hosts.add(payload["hostname"])
            continue
        if response.status_code == 401:
            log("Error: Azure rejected the relay key (401). Check KH_RELAY_KEY.",
                error=True)
            return EXIT_CONFIG
        if response.status_code in (400, 413, 415):
            log(f"Error: Azure rejected a batch of {payload['hostname']} "
                f"({response.status_code}, request {request_id}): "
                f"{describe_error(response)}", error=True)
            return EXIT_REJECTED
        log(f"Error: Azure answered {response.status_code} (request {request_id}); "
            f"rows kept for the next run.", error=True)
        return EXIT_TEMPORARY

    log(f"Forwarded {sent} row(s) from {len(hosts)} host(s) in {requests_made} "
        f"request(s) ({duplicates} already in Azure).")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forward collector data to Azure")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the first batch without contacting Azure")
    parser.add_argument("--batch-size", type=int, default=MAX_BATCH_SIZE)
    parser.add_argument("--status", action="store_true",
                        help="show rows per host and how many are waiting")
    args = parser.parse_args(argv)

    if args.status:
        load_dotenv()
        db_path = os.getenv("KH_SQLITE_PATH", "instance/khapi.db")
        if not os.path.isfile(db_path):
            log(f"Error: collector database not found: {db_path}", error=True)
            return EXIT_CONFIG
        with closing(connect(db_path)) as conn:
            print(f"{'host':<20}{'rows':>8}{'waiting':>9}  newest (UTC)")
            for row in status(conn):
                print(f"{row['hostname']:<20}{row['total']:>8}{row['waiting']:>9}"
                      f"  {row['newest']}")
        return EXIT_OK

    if not 1 <= args.batch_size <= MAX_BATCH_SIZE:
        log(f"Error: --batch-size must be between 1 and {MAX_BATCH_SIZE}.", error=True)
        return EXIT_CONFIG
    try:
        db_path, url, key, ca_bundle = load_settings()
    except ConfigError as exc:
        log(f"Error: {exc}", error=True)
        return EXIT_CONFIG

    with closing(connect(db_path)) as conn:
        return relay(conn, url, create_session(key, ca_bundle),
                     args.batch_size, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
