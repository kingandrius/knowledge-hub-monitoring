#!/bin/bash
# Install the Knowledge Hub collector on Monitoring-1 (MonitorServer, 10.10.10.30).
#
#   sudo bash deploy/install-collector.sh            # from the repo folder
#   sudo bash deploy/install-collector.sh add <host> # one more agent key later
#
# Result:
#   /opt/kh-collector        app code + venv (root-owned, read-only for the service)
#   /etc/kh-collector        certificate, private key, collector.env, relay.env
#   /var/lib/kh-collector    SQLite database (the only writable path)
#   kh-collector.service     gunicorn on https://0.0.0.0:8443 as user khcollector
#   /etc/cron.d/kh-relay     relay to Azure every 4 hours (needs relay.env, Thu/Fri)
#   /root/kh-agent-keys.txt  one key per agent (type it on that host, then
#                            store it in the password manager and delete the file)
set -euo pipefail

AGENTS="monitorserver directoryserver fileserver staffpc1 staffpc2 webserver"
IP="10.10.10.30"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/kh-collector
ETC=/etc/kh-collector
DATA=/var/lib/kh-collector
KEYS=/root/kh-agent-keys.txt

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }

new_key() {   # prints "<key> <hash>"
    "$APP/.venv/bin/python" "$APP/tools/make_agent_key.py"
}

add_agent() {   # $1 = host name; appends key to KEYS and hash to collector.env
    local host="$1" pair key hash
    pair="$(new_key)"; key="${pair% *}"; hash="${pair#* }"
    echo "$host $key" >> "$KEYS"
    sed -i "s/^KH_API_KEY_HASHES=\(.\+\)$/KH_API_KEY_HASHES=\1,$hash/; s/^KH_API_KEY_HASHES=$/KH_API_KEY_HASHES=$hash/" "$ETC/collector.env"
    echo "  key for $host added"
}

if [ "${1:-}" = "add" ]; then
    [ -n "${2:-}" ] || { echo "Usage: $0 add <host>"; exit 1; }
    add_agent "$2"
    systemctl restart kh-collector
    echo "Key in $KEYS (line for $2). Collector restarted."
    exit 0
fi

echo "== 1/7 packages"
apt-get install -y -q python3-venv openssl curl >/dev/null

echo "== 2/7 service user and folders"
id khcollector >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin khcollector
install -d -o root -g root -m 755 "$APP"
install -d -o root -g khcollector -m 750 "$ETC"
install -d -o khcollector -g khcollector -m 750 "$DATA" /var/log/kh-collector

echo "== 3/7 application code and venv"
rm -rf "$APP/khapi" "$APP/tools"
cp -r "$REPO/api/khapi" "$REPO/api/tools" "$REPO/api/app.py" "$REPO/api/requirements-collector.txt" "$APP/"
install -m 755 "$REPO/deploy/run-relay.sh" "$APP/run-relay.sh"
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q -r "$APP/requirements-collector.txt"

echo "== 4/7 self-signed certificate (IP $IP, valid 1 year)"
if [ ! -f "$ETC/collector.crt" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 -sha256 \
        -keyout "$ETC/collector.key" -out "$ETC/collector.crt" \
        -subj "/CN=monitorserver" \
        -addext "subjectAltName=IP:$IP,IP:127.0.0.1,DNS:monitorserver" 2>/dev/null
fi
chown root:khcollector "$ETC/collector.key" "$ETC/collector.crt"
chmod 640 "$ETC/collector.key"; chmod 644 "$ETC/collector.crt"

echo "== 5/7 settings and agent keys"
if [ ! -f "$ETC/collector.env" ]; then
    cat > "$ETC/collector.env" <<EOF
KH_API_KEY_HASHES=
KH_STORAGE_BACKEND=sqlite
KH_SQLITE_PATH=$DATA/collector.db
KH_PROXY_HOPS=0
KH_LOG_LEVEL=INFO
KH_LOG_FILE=
EOF
    : > "$KEYS"; chmod 600 "$KEYS"
    for host in $AGENTS; do add_agent "$host"; done
fi
if [ ! -f "$ETC/relay.env" ]; then
    cat > "$ETC/relay.env" <<EOF
# Filled in after the Azure session (29/30 Oct). Never put a collector key here.
KH_SQLITE_PATH=$DATA/collector.db
KH_RELAY_URL=
KH_RELAY_KEY=
EOF
fi
chown root:khcollector "$ETC/collector.env" "$ETC/relay.env"
chmod 640 "$ETC/collector.env" "$ETC/relay.env"

echo "== 6/7 service, relay schedule, host firewall"
install -m 644 "$REPO/deploy/kh-collector.service" /etc/systemd/system/kh-collector.service
systemctl daemon-reload
systemctl enable --now kh-collector >/dev/null 2>&1
systemctl restart kh-collector
cat > /etc/cron.d/kh-relay <<EOF
# Forward collector data to Azure every 4 hours (one database wake-up per run)
7 */4 * * * khcollector $APP/run-relay.sh >> /var/log/kh-collector/relay.log 2>&1
EOF
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow from 10.10.0.0/16 to any port 8443 proto tcp >/dev/null
    echo "  ufw: 8443/tcp allowed from 10.10.0.0/16"
fi

echo "== 7/7 health check"
sleep 3
curl -s --cacert "$ETC/collector.crt" "https://$IP:8443/api/v1/health"; echo
echo
echo "Certificate fingerprint (agents must show the same value):"
openssl x509 -in "$ETC/collector.crt" -noout -fingerprint -sha256
echo
echo "Agent keys are in $KEYS:  sudo cat $KEYS"
