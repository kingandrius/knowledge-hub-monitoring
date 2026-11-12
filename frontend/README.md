# Knowledge Hub Monitoring – Web Frontend (CS3)

Flask web interface for the monitoring data (REQ-S2P3-001). It reads
everything through the monitoring API (`../api`) with its own API key; it
has no database of its own.

| Page | Use case | API call |
|---|---|---|
| `/` | UC2 current status per host | `GET /measurements` (last 24 h), newest value per host |
| `/history` | UC3 history + averages | `GET /measurements` + `GET /measurements/summary` |
| `/containers` | UC4 container status | `GET /measurements` (last 1 h), metrics `container_*` |
| `/health` | probe for Docker / Container Apps | none |

Container data uses the same API: the docker-agent sends one "host" per
container with the metrics `container_up` (1/0), `container_cpu` and
`container_memory`.

## Run locally

```bash
# 1. API (other terminal, from ../api): python app.py   -> port 5000
# 2. Make a key for the frontend and add its hash to the API's KH_API_KEY_HASHES
python ../api/tools/make_api_key.py
# 3. Frontend
pip install -r requirements.txt
cp .env.example .env        # set KH_API_URL and KH_API_KEY
python app.py               # http://127.0.0.1:8000
python -m unittest -v       # 7 tests
```

If the API is down, every page shows "Data not available" with HTTP 503.
