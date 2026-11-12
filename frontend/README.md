# Knowledge Hub Monitoring – Web Frontend (CS3)

Flask web interface for the monitoring data (REQ-S2P3-001). It reads
everything through the monitoring API (`../api`) with its own API key; it
has no database of its own.

| Page | Use case | API call |
|---|---|---|
| `/` | UC2 current status per host | `GET /measurements` (last 24 h), newest value per host |
| `/history` | UC3 history + averages | `GET /measurements` + `GET /measurements/summary` |
| `/containers` | UC4 container status | `GET /measurements` (last 1 h), metrics `container_*` |
| `/auth/redirect`, `/auth/logout` | UC1 / UC5 sign-in and sign-out | Entra ID |
| `/health` | probe for Docker / Container Apps (no sign-in) | none |

## Sign-in (Entra ID, REQ-S2P3-002)

- Library: Microsoft `identity` (MSAL, OpenID Connect authorization code flow).
- App registration `app-kh-monitoring-web` in tenant IronWolfBV, single tenant,
  permission `User.Read` (delegated, admin consent).
- **Authorisation is done by Entra ID:** enterprise app setting *Assignment
  required = Yes*, only group `grp-kh-monitoring-users` assigned. Anyone else
  is refused by Entra (AADSTS50105) before reaching the app.
- Tokens stay in the server-side session (Flask-Session); the browser only gets
  a session cookie (HttpOnly, SameSite=Lax, Secure except in local testing).
- The app refuses to start if any Entra setting is missing (fail closed).

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
cp .env.example .env        # API URL + key, Entra tenant/client/secret
python app.py               # http://127.0.0.1:8000
python -m unittest -v       # 10 tests
```

If the API is down, every page shows "Data not available" with HTTP 503.
