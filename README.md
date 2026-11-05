# Knowledge Hub Monitoring

CS1 monitoring application, extended in Case Study 2 to send its measurements
to a central API in Azure.

| Path | What |
|---|---|
| `monitoring.py` | Monitoring CLI: `measure`, `report [--cloud]`, `send [--dry-run]` |
| `monitoring_config.py` | CS1 Task 5 configuration script |
| `intune/` | Intune compliance policy as code (Microsoft Graph), see below |
| `tests/` | unittest suite for the monitoring and Intune scripts |
| `api/` | Flask API (App Service), see `api/README.md` |
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

## Intune compliance policy as code (`intune/`)

`intune/intune_policy.py` keeps the Windows compliance policy
`cp-kh-windows-baseline` in Git (`intune/policies/cp-kh-windows-baseline.json`)
and applies it to Intune through Microsoft Graph.

| Mode | What it does |
|---|---|
| `export` | Reads the live policy and saves it as JSON (IDs, dates and empty fields removed) |
| `apply [--dry-run]` | Creates or updates the policy from the JSON, then assigns it to **one group only** (no "All devices", no exclusions) |
| `show` | Prints the policy's current assignments and flags exclusions |

**Setup (once):**

1. Entra admin center → App registrations → `app-kh-intune-automation` (single tenant).
2. API permissions → Microsoft Graph → **Application** permission
   `DeviceManagementConfiguration.ReadWrite.All` → Grant admin consent.
   Nothing else (least privilege).
3. Certificates & secrets → new client secret → store the value in the password manager.
4. Copy `intune/.env.example` to `intune/.env` and fill in tenant ID, client ID,
   secret and the Object ID of `grp-kh-staff-devices`. `.env` is in `.gitignore`.

**Use:**

```bash
python intune/intune_policy.py export          # live policy -> JSON, then review + commit
python intune/intune_policy.py apply --dry-run # says what would change
python intune/intune_policy.py apply           # make Intune match Git
python intune/intune_policy.py show
```

Notes:

- Uses Graph **beta**: in v1.0 the Defender and firewall settings of a Windows
  compliance policy are not exposed, so an export from v1.0 silently dropped
  5 of the 6 settings. Always review the exported JSON before committing.
- Why the assignment is scripted: on 5 Nov the group was added under
  "Excluded" in the portal by mistake. An exclusion wins, so the PC got no
  compliance policy. `apply` always writes the same reviewed assignment.
- Errors: 429 / 5xx are retried (respecting Retry-After); 401 / 403 stop with
  a clear message. Exit codes as for `monitoring.py`: 0 OK, 1 temporary,
  2 configuration / sign-in / permission, 3 rejected by Graph.
