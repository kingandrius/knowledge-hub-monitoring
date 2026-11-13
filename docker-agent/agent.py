"""Knowledge Hub docker-agent (CS3).

Reads the local Docker daemon and sends one "host" per container to the
Knowledge Hub API, with three metrics:

    container_up      1 if the container is running, else 0
    container_cpu     CPU use as % of the whole Docker host (0-100)
    container_memory  memory use as % of the container limit (0-100)

Stopped containers only send container_up = 0.

The agent only uses read calls (list containers, read stats). It does not
keep a local buffer: if the API is down, that round is logged and skipped.

Settings (environment or .env):
    KH_API_URL     e.g. http://api:5000/api/v1
    KH_API_KEY     the agent's own API key
    KH_HOST_IP     IP address reported for the containers (the Docker host)
    KH_INTERVAL    seconds between rounds (default 60)

Usage:
    python agent.py          run forever
    python agent.py --once   one round, then stop (for testing)
"""
import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

log = logging.getLogger("docker-agent")

REQUEST_TIMEOUT = (5, 30)  # connect, read


def safe_hostname(name):
    """Turn a container name into a valid RFC 1123 host name for the API."""

    name = re.sub(r"[^a-z0-9-]", "-", name.lower().lstrip("/"))
    name = name.strip("-")[:63].strip("-")
    return name or "container"


def cpu_percent(stats):
    """CPU use as % of the whole host, from one Docker stats sample."""

    cpu = stats.get("cpu_stats", {})
    pre = stats.get("precpu_stats", {})
    cpu_delta = (cpu.get("cpu_usage", {}).get("total_usage", 0)
                 - pre.get("cpu_usage", {}).get("total_usage", 0))
    system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)

    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0

    # Not multiplied by the number of CPUs, so 100 = the whole host is busy
    return round(min(cpu_delta / system_delta * 100, 100.0), 2)


def memory_percent(stats):
    """Memory use (without file cache) as % of the container's limit."""

    memory = stats.get("memory_stats", {})
    usage = memory.get("usage", 0)
    limit = memory.get("limit", 0)
    details = memory.get("stats", {})

    # Same calculation as `docker stats`: leave out the file cache
    cache = details.get("inactive_file", details.get("total_inactive_file", 0))

    if limit <= 0:
        return 0.0
    return round(min(max(usage - cache, 0) / limit * 100, 100.0), 2)


def collect(client):
    """Return {hostname: [measurement, ...]} for every container."""

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {}

    for container in client.containers.list(all=True):
        running = container.status == "running"
        items = [{"metric": "container_up", "value": 1.0 if running else 0.0,
                  "timestamp": timestamp}]

        if running:
            stats = container.stats(stream=False)
            items.append({"metric": "container_cpu", "value": cpu_percent(stats),
                          "unit": "percent", "timestamp": timestamp})
            items.append({"metric": "container_memory", "value": memory_percent(stats),
                          "unit": "percent", "timestamp": timestamp})

        result[safe_hostname(container.name)] = items

    return result


def send(session, api_url, host_ip, data):
    """POST one request per container. Returns the number that failed."""

    failed = 0
    for hostname, items in data.items():
        payload = {"hostname": hostname, "ip_address": host_ip, "measurements": items}
        try:
            response = session.post(f"{api_url}/measurements", json=payload,
                                    timeout=REQUEST_TIMEOUT)
        except requests.RequestException as error:
            log.warning("API not reachable (%s); round skipped", type(error).__name__)
            return len(data)

        if response.status_code != 201:
            log.warning("API answered %s for %s: %s", response.status_code,
                        hostname, response.text[:200])
            failed += 1

    return failed


def load_settings():
    """Read and check the settings. Exits with code 2 if something is missing."""

    load_dotenv()
    settings = {
        "api_url": os.getenv("KH_API_URL", "").rstrip("/"),
        "api_key": os.getenv("KH_API_KEY", ""),
        "host_ip": os.getenv("KH_HOST_IP", "127.0.0.1"),
        "interval": int(os.getenv("KH_INTERVAL", "60")),
    }
    if not settings["api_url"] or not settings["api_key"]:
        log.error("KH_API_URL and KH_API_KEY must be set (see .env.example)")
        sys.exit(2)
    return settings


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Knowledge Hub docker-agent")
    parser.add_argument("--once", action="store_true", help="one round, then stop")
    args = parser.parse_args()

    settings = load_settings()

    import docker  # imported here so the unit tests do not need Docker
    client = docker.from_env()

    session = requests.Session()
    session.headers.update({"X-API-Key": settings["api_key"]})

    while True:
        try:
            data = collect(client)
            failed = send(session, settings["api_url"], settings["host_ip"], data)
            log.info("sent %d container(s), %d failed", len(data) - failed, failed)
        except docker.errors.DockerException as error:
            log.warning("Docker not reachable: %s", error)

        if args.once:
            return 0
        time.sleep(settings["interval"])


if __name__ == "__main__":
    sys.exit(main())
