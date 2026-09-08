#!/usr/bin/env python3
"""monitoring_config.py

Knowledge Hub Library -- Initial Monitoring Configuration Tool.

Task 5 deliverable: a small, in-memory command-line tool that lets a
user register one or more systems (hostname + IP address) together
with the list of metrics that should be monitored for each system,
and then display everything back in a well-formatted way.

This is intentionally simple. Nothing is persisted to disk yet, and no
actual monitoring is performed -- both of those come in later weeks.
Only Python 3.12+ standard library features are used.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MonitoredSystem:
    """A single system that should later be monitored.

    Attributes:
        hostname: The system's hostname (e.g. "FileServer-1").
        ip_address: The system's IPv4 (or IPv6) address as a string.
        metrics: The metric names to track for this system
            (e.g. "cpu-usage", "disk-0-usage", "memory-usage").
    """

    hostname: str
    ip_address: str
    metrics: list[str] = field(default_factory=list)


def prompt_non_empty(prompt_text: str) -> str:
    """Ask the user for a value and keep asking until it isn't blank.

    Args:
        prompt_text: The text shown to the user.

    Returns:
        The trimmed, non-empty string the user entered.
    """
    while True:
        value = input(prompt_text).strip()
        if value:
            return value
        print("  This value can't be empty, please try again.")


def prompt_metrics() -> list[str]:
    """Ask the user for a comma-separated list of metrics to monitor.

    Returns:
        A list of metric names with surrounding whitespace removed and
        empty entries discarded. Duplicate names are kept as entered.
    """
    raw_input_value = prompt_non_empty(
        "  Metrics to monitor (comma-separated, e.g. "
        "cpu-usage, memory-usage, disk-0-usage): "
    )
    metrics = [metric.strip() for metric in raw_input_value.split(",")]
    return [metric for metric in metrics if metric]


def prompt_new_system() -> MonitoredSystem:
    """Collect hostname, IP address and metrics for one new system.

    Returns:
        A populated MonitoredSystem instance.
    """
    print("\nEnter the details of the system to monitor:")
    hostname = prompt_non_empty("  Hostname: ")
    ip_address = prompt_non_empty("  IP address: ")
    metrics = prompt_metrics()
    return MonitoredSystem(
        hostname=hostname, ip_address=ip_address, metrics=metrics
    )


def prompt_yes_no(prompt_text: str) -> bool:
    """Ask a yes/no question until the user gives a clear answer.

    Args:
        prompt_text: The question to show the user.

    Returns:
        True for a "yes" answer, False for a "no" answer.
    """
    while True:
        answer = input(prompt_text).strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please answer with 'y' or 'n'.")


def collect_systems() -> list[MonitoredSystem]:
    """Interactively collect any number of systems from the user.

    Returns:
        The list of MonitoredSystem entries the user entered, in the
        order they were added.
    """
    systems: list[MonitoredSystem] = []
    while True:
        systems.append(prompt_new_system())
        if not prompt_yes_no("\nAdd another system? (y/n): "):
            return systems


def format_systems_report(systems: list[MonitoredSystem]) -> str:
    """Build a well-formatted, human-readable report of all systems.

    Args:
        systems: The systems to include in the report.

    Returns:
        A multi-line string ready to be printed.
    """
    if not systems:
        return "No systems have been configured."

    lines = [
        "", "=" * 60, "MONITORING CONFIGURATION SUMMARY".center(60),
        "=" * 60,
    ]

    for index, system in enumerate(systems, start=1):
        lines.append(f"\nSystem {index}: {system.hostname}")
        lines.append("-" * 60)
        lines.append(f"  Hostname:    {system.hostname}")
        lines.append(f"  IP address:  {system.ip_address}")
        if system.metrics:
            lines.append("  Metrics:")
            for metric in system.metrics:
                lines.append(f"    - {metric}")
        else:
            lines.append("  Metrics:     (none specified)")

    lines.append("\n" + "=" * 60)
    lines.append(f"Total systems configured: {len(systems)}")
    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> None:
    """Run the interactive monitoring configuration tool."""
    print("Knowledge Hub Library -- Monitoring Configuration (Week 1)")
    print("Everything you enter is stored in memory only for this run.\n")

    systems = collect_systems()
    print(format_systems_report(systems))


if __name__ == "__main__":
    main()
