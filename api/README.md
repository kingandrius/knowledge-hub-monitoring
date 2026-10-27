# Knowledge Hub Monitoring API

Flask REST API that receives monitoring measurements from the CS1 monitoring
script (Monitoring-1, `10.10.10.30`) and stores them centrally. Case Study 2,
requirement REQ-S2P2-05. Target host: Azure App Service (Linux, B1, Python 3.12),
reachable only through a private endpoint over the site-to-site VPN.

## Structure

```
app.py                  Entry point (App Service runs app:app with gunicorn)
khapi/
  __init__.py           Application factory create_app()
  config.py             Settings from environment variables / .env
  models.py             Measurement, MeasurementBatch + validation rules
  auth.py               X-API-Key check against SHA-256 hashes
  routes.py             /api/v1 endpoints
  errors.py             JSON error format, 503 for storage outages
  logging_setup.py      Request IDs, access log, stdout / rotating file
  storage/
    base.py             Repository interface
    memory.py           In-memory store (unit tests)
    sqlite.py           Local SQLite store (Task 3)
                        Azure SQL store via pyodbc follows in Task 4 (4.1)
tests/test_api.py       unittest suite
tools/make_api_key.py   Generates an API key and its hash
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python tools/make_api_key.py          # put the hash in .env, keep the key safe
cp .env.example .env                  # then edit KH_API_KEY_HASHES
python app.py                         # http://127.0.0.1:5000/api/v1/health
python -m unittest -v                 # 19 tests
```

## Endpoints (v1)

| Method | Path | Auth | Success |
|---|---|---|---|
| GET | `/api/v1/health` | none | 200 liveness (does not touch the database) |
| POST | `/api/v1/measurements` | X-API-Key | 201 `{accepted, duplicates, request_id}` |
| GET | `/api/v1/measurements?hostname=&metric=&start=&end=&limit=` | X-API-Key | 200 `{count, items}` |
| GET | `/api/v1/measurements/summary?hostname=&metric=&start=&end=` | X-API-Key | 200 `{items}` with count/avg/min/max |

Errors: 400 validation (all field errors listed), 401 missing/invalid key,
404, 405, 413 body > 256 KB, 415 not JSON, 503 database unavailable
(`Retry-After: 60`), 500 unexpected. Every response carries `X-Request-ID`.

Example body:

```json
{
  "hostname": "monitoring-1",
  "ip_address": "10.10.10.30",
  "measurements": [
    {"metric": "cpu_usage", "value": 12.5, "unit": "percent", "timestamp": "2026-10-15T09:00:00Z"},
    {"metric": "disk_usage", "value": 61.2, "unit": "percent", "timestamp": "2026-10-15T09:00:00Z"}
  ]
}
```

A resent batch is harmless: rows with the same host, metric and timestamp
are counted as duplicates and not stored twice.

## Security notes

- Secrets live only in `.env` (git-ignored) or App Service settings; the API
  stores key hashes, never keys. Two hashes may be set during key rotation.
- The app refuses to start when no key hash is configured.
- API keys and request bodies are never written to the log.
