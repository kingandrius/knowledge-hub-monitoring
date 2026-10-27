"""Knowledge Hub monitoring application (CS1 Task 11, extended in CS2).

Modes:
    measure  Measure CPU, memory and disk usage and store them locally.
    report   Show stored measurements (local database, or the cloud API
             with --cloud).
    send     Upload measurements that have not been sent yet to the
             Knowledge Hub API in batches (CS2, REQ-S2P2-06).

The local SQLite database stays the buffer: a measurement is only marked as
sent after the API confirms it with 201 Created, so nothing is lost while the
VPN, the API or the database is unavailable.

Settings come from environment variables or a .env file (never hard-coded):
    KH_API_URL   e.g. https://app-kh-api-xxxx.azurewebsites.net/api/v1
    KH_API_KEY   API key from the password manager
    KH_HOSTNAME, KH_HOST_IP   optional overrides of this host's identity
    KH_CA_BUNDLE optional certificate file to trust, e.g. the collector's
                 self-signed certificate on Monitoring-1

Exit codes (for cron): 0 OK, 1 temporary failure (retry later),
2 configuration or authentication error, 3 data rejected by the API.
"""
import argparse
import os
import socket
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import psutil
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# Configuration
DATABASE_FILE = "monitoring.db"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
RECOGNIZED_METRICS = ["cpu", "memory", "disk"]
METRIC_UNIT = "percent"

# Upload settings (must match the API contract, Design section 11)
MAX_BATCH_SIZE = 500
MAX_AGE_DAYS = 29          # the API refuses measurements older than 30 days
REQUEST_TIMEOUT = (5, 90)  # connect, read: a paused database needs ~1 minute

EXIT_OK = 0
EXIT_TEMPORARY = 1
EXIT_CONFIG = 2
EXIT_REJECTED = 3


class ConfigError(Exception):
    """Raised when the API settings are missing or unsafe."""


def create_database():
    """Create the SQLite database and measurements table."""

    # Create the database if it does not already exist
    with sqlite3.connect(DATABASE_FILE) as connection:

        # Create the measurements table if it does not already exist
        connection.execute("""
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL
            )
        """)

        # CS2: add the sent_at column to existing CS1 databases
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_info(measurements)")
        ]
        if "sent_at" not in columns:
            connection.execute(
                "ALTER TABLE measurements ADD COLUMN sent_at TEXT"
            )


def take_measurements(metrics):
    """Measure the selected system metrics."""

    # Use one timestamp for all measurements in this run
    timestamp = datetime.now().strftime(TIME_FORMAT)
    measurements = []

    # Measure CPU usage over a one-second interval
    if "cpu" in metrics:
        measurements.append([
            timestamp,
            "cpu",
            psutil.cpu_percent(interval=1)
        ])

    # Measure percentage of used memory
    if "memory" in metrics:
        measurements.append([
            timestamp,
            "memory",
            psutil.virtual_memory().percent
        ])

    # Measure percentage of used disk space
    if "disk" in metrics:
        measurements.append([
            timestamp,
            "disk",
            psutil.disk_usage("/").percent
        ])

    return measurements


def save_measurements(measurements):
    """Save measurements to the SQLite database."""

    # Open the database
    with sqlite3.connect(DATABASE_FILE) as connection:

        # Add each measurement to the database
        connection.executemany(
            """
            INSERT INTO measurements (timestamp, metric, value)
            VALUES (?, ?, ?)
            """,
            measurements
        )

    print("Measurements saved successfully.")


def read_measurements():
    """Read all measurements from the SQLite database."""

    # Open the database
    with sqlite3.connect(DATABASE_FILE) as connection:

        # Return rows as dictionaries
        connection.row_factory = sqlite3.Row

        # Read all measurements in timestamp order
        rows = connection.execute("""
            SELECT timestamp, metric, value
            FROM measurements
            ORDER BY timestamp
        """).fetchall()

    return [dict(row) for row in rows]


def filter_measurements(measurements, start=None, end=None, metrics=None):
    """Filter measurements by time and metric."""

    filtered = []

    for measurement in measurements:

        # Convert the stored timestamp into a datetime object
        timestamp = datetime.strptime(
            measurement["timestamp"],
            TIME_FORMAT
        )

        # Skip measurements before the requested start time
        if start and timestamp < start:
            continue

        # Skip measurements after the requested end time
        if end and timestamp > end:
            continue

        # Skip metrics that were not requested
        if metrics and measurement["metric"] not in metrics:
            continue

        filtered.append(measurement)

    return filtered


def print_report(measurements):
    """Display measurements in a readable table."""

    print("\nMONITORING REPORT")
    print("-" * 65)

    if not measurements:
        print("No measurements found for the selected filters.")
        print("-" * 65)
        return

    # Print table headings
    print(f"{'Timestamp':<20} {'Metric':<15} {'Value':>10}")
    print("-" * 65)

    # Print each measurement
    for measurement in measurements:
        print(
            f"{measurement['timestamp']:<20} "
            f"{measurement['metric']:<15} "
            f"{measurement['value']:>10}%"
        )

    print("-" * 65)


def print_averages(measurements):
    """Calculate and display the average for each metric."""

    totals = {}
    counts = {}

    # Add each measurement to its metric total
    for measurement in measurements:
        value = float(measurement["value"])
        metric = measurement["metric"]

        totals[metric] = totals.get(metric, 0) + value
        counts[metric] = counts.get(metric, 0) + 1

    print("\nAVERAGES")
    print("-" * 40)

    # Calculate the average for each metric
    for metric in totals:
        average = totals[metric] / counts[metric]
        print(f"{metric:<15} {average:>10.2f}%")

    print("-" * 40)


def parse_metrics(value):
    """Convert a comma-separated metric string into a list."""

    if not value:
        return None

    # Remove whitespace and convert names to lowercase
    return [
        metric.strip().lower()
        for metric in value.split(",")
        if metric.strip()
    ]


def validate_metrics(metrics):
    """Check that all requested metrics are recognized."""

    if metrics is None:
        return True

    invalid = [
        metric
        for metric in metrics
        if metric not in RECOGNIZED_METRICS
    ]

    if invalid:
        print(
            "Error: unrecognized metric(s): "
            + ", ".join(invalid)
        )
        print(
            "Recognized metrics: "
            + ", ".join(RECOGNIZED_METRICS)
        )
        return False

    return True


def parse_time(value):
    """Convert a command-line time into a datetime object."""

    if not value:
        return None

    try:
        return datetime.strptime(value, TIME_FORMAT)
    except ValueError:
        print(
            "Error: dates must use the format "
            "YYYY-MM-DD HH:MM:SS"
        )
        return None


# ---------------------------------------------------------------------------
# CS2: sending measurements to the Knowledge Hub API
# ---------------------------------------------------------------------------

def load_api_settings():
    """Read the API URL and key from the environment or a .env file.

    Raises:
        ConfigError: if a setting is missing, or if the URL is not HTTPS
            (plain HTTP is only allowed to this machine, for local tests).
    """

    load_dotenv()
    url = os.getenv("KH_API_URL", "").rstrip("/")
    key = os.getenv("KH_API_KEY", "")

    if not url or not key:
        raise ConfigError("KH_API_URL and KH_API_KEY must be set (see .env.example).")

    # Never send the API key over an unencrypted connection
    parsed = urlparse(url)
    if parsed.scheme != "https" and parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ConfigError("KH_API_URL must use https:// (http only for localhost).")

    # Optional: trust a specific certificate (the collector is self-signed)
    ca_bundle = os.getenv("KH_CA_BUNDLE", "")
    if ca_bundle and not os.path.isfile(ca_bundle):
        raise ConfigError(f"KH_CA_BUNDLE file not found: {ca_bundle}")

    return url, key


def host_identity(api_url):
    """Return this host's name and the IP address it uses to reach the API."""

    hostname = os.getenv("KH_HOSTNAME") or socket.gethostname().split(".")[0]
    ip_address = os.getenv("KH_HOST_IP")

    if not ip_address:
        # Ask the OS which local address it would use; no packet is sent
        target = urlparse(api_url).hostname or "10.20.0.1"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((socket.gethostbyname(target), 443))
                ip_address = probe.getsockname()[0]
        except OSError:
            ip_address = "127.0.0.1"

    return hostname.lower(), ip_address


def to_utc_iso(local_timestamp):
    """Convert a stored local-time timestamp into ISO 8601 UTC."""

    local = datetime.strptime(local_timestamp, TIME_FORMAT).astimezone()
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_unsent(limit):
    """Return up to `limit` unsent rows that the API will still accept."""

    oldest = (datetime.now() - timedelta(days=MAX_AGE_DAYS)).strftime(TIME_FORMAT)

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            """
            SELECT id, timestamp, metric, value
            FROM measurements
            WHERE sent_at IS NULL AND timestamp >= ?
            ORDER BY id
            LIMIT ?
            """,
            (oldest, limit)
        ).fetchall()

        too_old = connection.execute(
            "SELECT COUNT(*) FROM measurements WHERE sent_at IS NULL AND timestamp < ?",
            (oldest,)
        ).fetchone()[0]

    return [dict(row) for row in rows], too_old


def mark_sent(row_ids):
    """Record that the API has confirmed these rows."""

    sent_at = datetime.now().strftime(TIME_FORMAT)

    with sqlite3.connect(DATABASE_FILE) as connection:
        connection.executemany(
            "UPDATE measurements SET sent_at = ? WHERE id = ?",
            [(sent_at, row_id) for row_id in row_ids]
        )


def build_payload(rows, hostname, ip_address):
    """Build the JSON body for POST /measurements (Design section 11)."""

    return {
        "hostname": hostname,
        "ip_address": ip_address,
        "measurements": [
            {
                "metric": row["metric"],
                "value": float(row["value"]),
                "unit": METRIC_UNIT,
                "timestamp": to_utc_iso(row["timestamp"]),
            }
            for row in rows
        ],
    }


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


def create_session(api_key):
    """Create an HTTP session with the API key and automatic retries.

    Connection errors and 502/503/504 answers are retried with back-off,
    honouring Retry-After. Retrying POST is safe because the API ignores
    duplicate measurements.
    """

    retry = Retry(
        total=3,
        connect=3,
        read=1,
        status=2,
        backoff_factor=2,
        status_forcelist=[502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    session = PinnedSession()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update({"X-API-Key": api_key, "Accept": "application/json"})

    # Verify the server against this file instead of the public CAs; TLS
    # verification is never switched off
    ca_bundle = os.getenv("KH_CA_BUNDLE", "")
    if ca_bundle:
        session.verify = ca_bundle
    return session


def describe_error(response):
    """Return the API's error message and field details as one string."""

    try:
        error = response.json().get("error", {})
    except ValueError:
        return response.text[:200]

    details = "; ".join(
        f"{item.get('field')}: {item.get('issue')}"
        for item in error.get("details", [])
    )
    return f"{error.get('message', '')} {details}".strip()


def send_measurements(batch_size=MAX_BATCH_SIZE, dry_run=False, session=None):
    """Upload all unsent measurements in batches. Returns an exit code."""

    try:
        api_url, api_key = load_api_settings()
    except ConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_CONFIG

    hostname, ip_address = host_identity(api_url)
    session = session or create_session(api_key)
    total_sent = total_duplicates = 0

    while True:
        rows, too_old = read_unsent(batch_size)

        if too_old and total_sent == 0:
            print(f"Note: {too_old} unsent measurement(s) are older than "
                  f"{MAX_AGE_DAYS} days and stay local only.")

        if not rows:
            break

        payload = build_payload(rows, hostname, ip_address)

        # Dry run: show the first batch without sending anything
        if dry_run:
            print(f"Dry run: would send {len(rows)} measurement(s) as "
                  f"{hostname} ({ip_address}) to {api_url}/measurements")
            print(payload["measurements"][:3])
            return EXIT_OK

        try:
            response = session.post(f"{api_url}/measurements", json=payload,
                                    timeout=REQUEST_TIMEOUT)
        except requests.RequestException as error:
            print(f"Error: API not reachable ({type(error).__name__}). "
                  f"{len(rows)} measurement(s) kept for the next run.", file=sys.stderr)
            return EXIT_TEMPORARY

        request_id = response.headers.get("X-Request-ID", "-")

        if response.status_code == 201:
            result = response.json()
            mark_sent([row["id"] for row in rows])
            total_sent += result.get("accepted", 0)
            total_duplicates += result.get("duplicates", 0)
            continue

        if response.status_code == 401:
            print("Error: the API rejected the API key (401). Check KH_API_KEY.",
                  file=sys.stderr)
            return EXIT_CONFIG

        if response.status_code in (400, 413, 415):
            print(f"Error: the API rejected the data ({response.status_code}, "
                  f"request {request_id}): {describe_error(response)}", file=sys.stderr)
            return EXIT_REJECTED

        print(f"Error: API answered {response.status_code} (request {request_id}). "
              f"{len(rows)} measurement(s) kept for the next run.", file=sys.stderr)
        return EXIT_TEMPORARY

    print(f"Sent {total_sent} measurement(s) to the API "
          f"({total_duplicates} already stored).")
    return EXIT_OK


def read_cloud_measurements(start=None, end=None, metrics=None, session=None):
    """Read measurements for this host back from the API (report --cloud)."""

    api_url, api_key = load_api_settings()
    hostname, _ = host_identity(api_url)
    session = session or create_session(api_key)

    params = {"hostname": hostname, "limit": 1000}
    if start:
        params["start"] = start.astimezone().isoformat()
    if end:
        params["end"] = end.astimezone().isoformat()

    response = session.get(f"{api_url}/measurements", params=params,
                           timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    measurements = []
    for item in response.json()["items"]:
        if metrics and item["metric"] not in metrics:
            continue
        utc = datetime.strptime(item["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        local = utc.replace(tzinfo=timezone.utc).astimezone()
        measurements.append({
            "timestamp": local.strftime(TIME_FORMAT),
            "metric": item["metric"],
            "value": item["value"],
        })

    return sorted(measurements, key=lambda m: m["timestamp"])


def main():
    """Run the monitoring application. Returns an exit code."""

    # Make sure the database and table exist
    create_database()

    # Define the command-line interface
    parser = argparse.ArgumentParser(
        description="Knowledge Hub monitoring application"
    )

    parser.add_argument(
        "mode",
        choices=["measure", "report", "send"],
        help="Choose whether to measure, report or send to the API"
    )

    parser.add_argument(
        "--metrics",
        help="Comma-separated metrics: cpu,memory,disk"
    )

    parser.add_argument(
        "--start",
        help="Start time: YYYY-MM-DD HH:MM:SS"
    )

    parser.add_argument(
        "--end",
        help="End time: YYYY-MM-DD HH:MM:SS"
    )

    parser.add_argument(
        "--average",
        action="store_true",
        help="Calculate averages"
    )

    parser.add_argument(
        "--cloud",
        action="store_true",
        help="report: read the measurements from the API instead of locally"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="send: show what would be sent without contacting the API"
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=MAX_BATCH_SIZE,
        help=f"send: measurements per request (1-{MAX_BATCH_SIZE})"
    )

    args = parser.parse_args()

    # Parse and validate requested metrics
    metrics = parse_metrics(args.metrics)

    if not validate_metrics(metrics):
        return EXIT_CONFIG

    # Measurement mode
    if args.mode == "measure":
        metrics = metrics or RECOGNIZED_METRICS
        save_measurements(take_measurements(metrics))
        return EXIT_OK

    # Send mode (CS2)
    if args.mode == "send":
        if not 1 <= args.batch_size <= MAX_BATCH_SIZE:
            print(f"Error: --batch-size must be between 1 and {MAX_BATCH_SIZE}.")
            return EXIT_CONFIG
        return send_measurements(args.batch_size, args.dry_run)

    # Report mode
    start = parse_time(args.start)
    end = parse_time(args.end)

    # Stop if an invalid date was supplied
    if (args.start and start is None) or (args.end and end is None):
        return EXIT_CONFIG

    # Make sure the time range is valid
    if start and end and start > end:
        print("Error: start time cannot be later than end time.")
        return EXIT_CONFIG

    # Read measurements from the API or from the local database
    if args.cloud:
        try:
            measurements = read_cloud_measurements(start, end, metrics)
        except (ConfigError, requests.RequestException) as error:
            print(f"Error: cloud report failed: {error}", file=sys.stderr)
            return EXIT_TEMPORARY
    else:
        measurements = filter_measurements(
            read_measurements(),
            start,
            end,
            metrics
        )

    # Display the report
    print_report(measurements)

    # Display averages when requested
    if args.average:
        print_averages(measurements)

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
