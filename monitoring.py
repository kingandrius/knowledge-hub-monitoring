import argparse
import sqlite3
from datetime import datetime

import psutil


### Configuration
DATABASE_FILE = "monitoring.db"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
RECOGNIZED_METRICS = ["cpu", "memory", "disk"]


def create_database():
    """Create the SQLite database and measurements table."""

    ### Create the database if it does not already exist
    with sqlite3.connect(DATABASE_FILE) as connection:

        ### Create the measurements table if it does not already exist
        connection.execute("""
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL
            )
        """)


def take_measurements(metrics):
    """Measure the selected system metrics."""

    ### Use one timestamp for all measurements in this run
    timestamp = datetime.now().strftime(TIME_FORMAT)
    measurements = []

    ### Measure CPU usage over a one-second interval
    if "cpu" in metrics:
        measurements.append([
            timestamp,
            "cpu",
            psutil.cpu_percent(interval=1)
        ])

    ### Measure percentage of used memory
    if "memory" in metrics:
        measurements.append([
            timestamp,
            "memory",
            psutil.virtual_memory().percent
        ])

    ### Measure percentage of used disk space
    if "disk" in metrics:
        measurements.append([
            timestamp,
            "disk",
            psutil.disk_usage("/").percent
        ])

    return measurements


def save_measurements(measurements):
    """Save measurements to the SQLite database."""

    ### Open the database
    with sqlite3.connect(DATABASE_FILE) as connection:

        ### Add each measurement to the database
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

    ### Open the database
    with sqlite3.connect(DATABASE_FILE) as connection:

        ### Return rows as dictionaries
        connection.row_factory = sqlite3.Row

        ### Read all measurements in timestamp order
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

        ### Convert the stored timestamp into a datetime object
        timestamp = datetime.strptime(
            measurement["timestamp"],
            TIME_FORMAT
        )

        ### Skip measurements before the requested start time
        if start and timestamp < start:
            continue

        ### Skip measurements after the requested end time
        if end and timestamp > end:
            continue

        ### Skip metrics that were not requested
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

    ### Print table headings
    print(f"{'Timestamp':<20} {'Metric':<15} {'Value':>10}")
    print("-" * 65)

    ### Print each measurement
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

    ### Add each measurement to its metric total
    for measurement in measurements:
        value = float(measurement["value"])
        metric = measurement["metric"]

        totals[metric] = totals.get(metric, 0) + value
        counts[metric] = counts.get(metric, 0) + 1

    print("\nAVERAGES")
    print("-" * 40)

    ### Calculate the average for each metric
    for metric in totals:
        average = totals[metric] / counts[metric]
        print(f"{metric:<15} {average:>10.2f}%")

    print("-" * 40)


def parse_metrics(value):
    """Convert a comma-separated metric string into a list."""

    if not value:
        return None

    ### Remove whitespace and convert names to lowercase
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


def main():
    """Run the monitoring application."""

    ### Make sure the database and table exist
    create_database()

    ### Define the command-line interface
    parser = argparse.ArgumentParser(
        description="Knowledge Hub monitoring application"
    )

    parser.add_argument(
        "mode",
        choices=["measure", "report"],
        help="Choose whether to measure or report"
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

    args = parser.parse_args()

    ### Parse and validate requested metrics
    metrics = parse_metrics(args.metrics)

    if not validate_metrics(metrics):
        return

    ### Measurement mode
    if args.mode == "measure":
        metrics = metrics or RECOGNIZED_METRICS
        save_measurements(take_measurements(metrics))
        return

    ### Report mode
    start = parse_time(args.start)
    end = parse_time(args.end)

    ### Stop if an invalid date was supplied
    if (args.start and start is None) or (args.end and end is None):
        return

    ### Make sure the time range is valid
    if start and end and start > end:
        print("Error: start time cannot be later than end time.")
        return

    ### Read and filter stored measurements
    measurements = read_measurements()

    measurements = filter_measurements(
        measurements,
        start,
        end,
        metrics
    )

    ### Display the report
    print_report(measurements)

    ### Display averages when requested
    if args.average:
        print_averages(measurements)


if __name__ == "__main__":
    main()