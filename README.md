# Knowledge Hub Monitoring

CS1 monitoring application, extended in Case Study 2 to send its measurements
to a central API in Azure.

| Path | What |
|---|---|
| `monitoring.py` | Monitoring CLI: `measure`, `report [--cloud]`, `send [--dry-run]` |
| `monitoring_config.py` | CS1 Task 5 configuration script |
| `tests/` | unittest suite for the monitoring script |
| `api/` | Flask API (App Service, and the on-prem collector), see `api/README.md` |
| `api/tools/relay.py` | Relay: collector → Azure API every 4 h |
| `deploy/` | Install scripts: collector (Monitoring-1), Linux agent, Windows agent |
| `TASK11.md` | CS1 Task 11 documentation |

## Monitoring script (Monitoring-1, 10.10.10.30)

```bash
pip install -r requirements.txt
cp .env.example .env            # set KH_API_URL and KH_API_KEY
python monitoring.py measure    # store CPU, memory, disk locally (as in CS1)
python monitoring.py send --dry-run
python monitoring.py send       # upload unsent rows in batches
python monitoring.py report --cloud --average
python -m unittest -v
```

The local database is the buffer: rows are marked as sent only after the API
answers 201, so an unavailable tunnel, API or database never loses data.
Exit codes: 0 OK, 1 temporary failure, 2 configuration or key error,
3 data rejected.

Schedule (crontab on Monitoring-1): measure every 5 minutes, send every
4 hours, so the serverless database is woken about six times a day:

```cron
*/5 * * * *  cd /opt/kh-monitoring && .venv/bin/python monitoring.py measure >/dev/null
7 */4 * * *  cd /opt/kh-monitoring && .venv/bin/python monitoring.py send >> send.log 2>&1
```

## Collector design (CS2, 27 Oct)

Every host runs the same `monitoring.py` as an **agent**. Agents send to the
**collector** on MonitorServer instead of to Azure; only the collector talks
to Azure, through the **relay**.

```
agents (every host)  --HTTPS 8443, own key-->  collector (MonitorServer, SQLite)
                                                   | relay, every 4 h, Azure key
                                                   v
                                     Azure API (private endpoint) -> Azure SQL
```

| Host | OS | Install |
|---|---|---|
| MonitorServer 10.10.10.30 | Ubuntu | `sudo bash deploy/install-collector.sh`, then `install-agent.sh` |
| Webserver 10.10.40.10 | Ubuntu | `sudo bash deploy/install-agent.sh <key>` |
| DirectoryServer, FileServer, StaffPC1, StaffPC2 | Windows | `powershell -ExecutionPolicy Bypass -File deploy\install-agent.ps1 -Key <key>` |

- **Why:** one database wake-up per 4 h whatever the number of hosts (free
  vCore allowance); only MonitorServer holds the Azure key and needs the VPN
  path; data keeps collecting on-prem while the tunnel is down.
- **TLS:** self-signed certificate (SAN IP 10.10.10.30). Agents fetch it once
  and the installer stops unless the SHA-256 fingerprint matches the one shown
  on MonitorServer (trust on first use, checked by hand). Agents verify it on
  every request (`KH_CA_BUNDLE`); verification is never switched off.
- **Keys:** one key per agent (revocable per host); the collector stores only
  SHA-256 hashes. The Azure key lives only in `/etc/kh-collector/relay.env`.
  Known limitation: a key is not bound to a host name.
- **Least privilege:** the collector runs as `khcollector` with a read-only
  file system except `/var/lib/kh-collector`; agents run as `khagent`
  (Linux) or SYSTEM with a folder ACL (Windows).
- **Check:** `sudo -u khcollector /opt/kh-collector/run-relay.sh --status`
  shows rows per host and how many still wait for Azure.

Schedules: agent measure `*/5`, send `*/15` (local, free); relay `7 */4`.
