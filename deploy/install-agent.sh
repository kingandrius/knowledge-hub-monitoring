#!/bin/bash
# Install the monitoring agent on a Linux host (MonitorServer, Webserver, ...).
#
#   sudo bash deploy/install-agent.sh <agent-key> [collector-ip]
#
# Result:
#   /opt/kh-agent           monitoring.py + venv + .env + local buffer database
#   /etc/cron.d/kh-agent    measure every 5 min, send to the collector every 15 min
#   user khagent            runs the agent (no login shell)
set -euo pipefail

KEY="${1:-}"
COLLECTOR="${2:-10.10.10.30}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DIR=/opt/kh-agent

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }
[ -n "$KEY" ] || { echo "Usage: sudo bash $0 <agent-key> [collector-ip]"; exit 1; }

echo "== 1/5 packages, user, folder"
apt-get install -y -q python3-venv openssl >/dev/null
id khagent >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin khagent
install -d -o khagent -g khagent -m 750 "$DIR" /var/log/kh-agent

echo "== 2/5 code and venv"
install -o root -g khagent -m 644 "$REPO/monitoring.py" "$REPO/requirements.txt" "$DIR/"
[ -x "$DIR/.venv/bin/python" ] || python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

echo "== 3/5 collector certificate (trust on first use, check the fingerprint)"
openssl s_client -connect "$COLLECTOR:8443" -showcerts < /dev/null > "$DIR/s_client.txt" 2>/dev/null || true
openssl x509 -in "$DIR/s_client.txt" -out "$DIR/collector.crt"
rm -f "$DIR/s_client.txt"
openssl x509 -in "$DIR/collector.crt" -noout -fingerprint -sha256
read -r -p "Same fingerprint as shown on MonitorServer? (yes/no) " answer
[ "$answer" = "yes" ] || { rm -f "$DIR/collector.crt"; echo "Stopped: certificate not trusted."; exit 1; }
chown root:khagent "$DIR/collector.crt"; chmod 644 "$DIR/collector.crt"

echo "== 4/5 settings"
cat > "$DIR/.env" <<EOF
KH_API_URL=https://$COLLECTOR:8443/api/v1
KH_API_KEY=$KEY
KH_CA_BUNDLE=$DIR/collector.crt
EOF
chown khagent:khagent "$DIR/.env"; chmod 600 "$DIR/.env"
cat > /etc/cron.d/kh-agent <<EOF
# Knowledge Hub agent: measure every 5 min, send to the collector every 15 min
*/5 * * * *  khagent cd $DIR && .venv/bin/python monitoring.py measure > /dev/null 2>&1
*/15 * * * * khagent cd $DIR && .venv/bin/python monitoring.py send >> /var/log/kh-agent/send.log 2>&1
EOF

echo "== 5/5 first measurement and send"
cd "$DIR"
sudo -u khagent .venv/bin/python monitoring.py measure
sudo -u khagent .venv/bin/python monitoring.py send
