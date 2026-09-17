#!/usr/bin/env python3

"""
Knowledge Hub Library - Monitoring Configuration Tool

Task 5:
Register computers and the metrics that should be monitored.
The configuration is stored in memory and displayed at the end.
"""


def input_new_computer():
    """Ask the user for a computer name and return it."""

    print("Enter computers to be managed")
    computer = input("Enter a computer name: ").strip()

    return computer


def ask_for_metrics():
    """Ask the user for metrics until an empty line is entered."""

    metrics = []

    print("Enter metrics to measure")

    while True:
        metric = input("Metric to measure? ").strip()

        if metric == "":
            break

        metrics.append(metric)

    return metrics


def print_monitoring_data(data):
    """Print the monitoring configuration for one computer."""

    print(data["name"])

    for metric in data["metrics"]:
        print(" -", metric)

    print()


# Main program
computers = []

while True:
    computer = input_new_computer()

    if computer == "":
        break

    metrics = ask_for_metrics()

    computers.append({
        "name": computer,
        "metrics": metrics
    })


# Display all configured computers
for computer in computers:
    print_monitoring_data(computer)