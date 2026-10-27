#!/bin/sh
# Called by /etc/cron.d/kh-relay as user khcollector: forward to Azure.
set -a
. /etc/kh-collector/relay.env
set +a
cd /opt/kh-collector && exec .venv/bin/python tools/relay.py "$@"
