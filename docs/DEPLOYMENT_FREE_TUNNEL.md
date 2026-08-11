# $0 Deployment: Streamlit Community Cloud + Cloudflare Tunnel

Public demo of this app at zero hosting cost. The Streamlit frontend runs on
Streamlit Community Cloud (free); FastAPI, PostgreSQL, and Qdrant stay on
your Windows PC in Docker Compose; a free Cloudflare Tunnel exposes only the
FastAPI API to the internet over HTTPS.

```text
Streamlit Community Cloud
        |  HTTPS
        v
Cloudflare Tunnel  (cloudflared, running natively on your PC)
        |
        v
FastAPI (Docker, 127.0.0.1:${BACKEND_PORT})
        |
        v
Docker network "ragchat-net"
  |-- PostgreSQL (127.0.0.1:5432, loopback only)
  \-- Qdrant     (127.0.0.1:6333, loopback only)
```

Your PC has to be on and Docker Compose running for the public demo to work
— this is a demo/dev architecture, not production hosting.

---

## 1. Files changed and why

| File | Why |
|---|---|
| `docker-compose.yml` | Added a `qdrant` service (was host-native via `host.docker.internal`); removed the `frontend` service (frontend now deploys to Streamlit Community Cloud instead of running locally); bound `postgres`, `qdrant`, and `backend` ports to `127.0.0.1` instead of `0.0.0.0` so nothing but this machine (and `cloudflared`, which runs on it) can reach them. |
| `backend/app/core/config.py` | Added `cors_allowed_origins` setting (comma-separated, defaults to the local Streamlit dev origin). |
| `backend/app/main.py` | Added `CORSMiddleware` using that setting. |
| `.env.example` | Added `CORS_ALLOWED_ORIGINS`; documented that `QDRANT_HOST` here is for native (non-Docker) runs only — Compose overrides it. |
| `frontend/.streamlit/secrets.toml.example` | New — template for the `BACKEND_URL` secret Streamlit Community Cloud needs. |
| `.gitignore` | Added `frontend/.streamlit/secrets.toml` and `.cloudflared/` so real secrets/tunnel credentials never get committed. |

**Not changed:** `frontend/app/api_client.py` — it already reads
`BACKEND_URL` from an environment variable with no hardcoded localhost logic
that can't be overridden, and Streamlit Community Cloud exposes secrets as
env vars automatically, so `BACKEND_URL` set in Streamlit's Secrets panel is
picked up with zero code changes. The existing `/health` endpoint already
checks both Postgres and Qdrant independently (distinct error messages), so
no new health endpoints were added. RBAC, guardrails, and the retrieval/rerank/
LLM flow are untouched.

**Note on the variable name:** the task spec's example used `API_BASE_URL`;
the codebase already has a working `BACKEND_URL` (also used by
`docker-compose.yml`'s old frontend override) doing the exact same job, so
that name was kept rather than introducing a second name for the same
concept. Use `BACKEND_URL` everywhere below.

---

## 2. Start Postgres, Qdrant, and FastAPI locally

```powershell
docker compose up -d --build
docker compose ps
```

All three should show as running; `backend` will show `(healthy)` once its
own `/health` check (which itself checks Postgres and Qdrant) passes —
usually 30-60s including model warmup.

Stop everything:

```powershell
docker compose down
```

(add `-v` only if you intentionally want to wipe the Postgres/Qdrant volumes)

## 3. Test FastAPI locally

```powershell
curl http://127.0.0.1:8010/health
```

(replace `8010` with your `.env`'s `BACKEND_PORT`)

Expected: `{"status":"ok","qdrant":"connected","postgres":"connected"}`. If
it 503s, the body says which of the two backing stores failed.

## 4. Install and configure cloudflared

```powershell
winget install --id Cloudflare.cloudflared
cloudflared --version
```

No Cloudflare account is required for the free "quick tunnel" used below.

## 5. Create the public HTTPS tunnel

```powershell
cloudflared tunnel --url http://127.0.0.1:8010
```

(again, use your actual `BACKEND_PORT`)

Leave this running — it prints a random public URL like:

```text
https://random-two-words.trycloudflare.com
```

This is your `BACKEND_URL` / `<cloudflare-tunnel-domain>`. It's free forever
but **ephemeral** — it changes every time you restart `cloudflared`. If you
want a stable, permanent URL instead (and already own a domain added to
Cloudflare), see §10 below for a named tunnel; skip it otherwise.

## 6. Test the public API

```powershell
curl https://random-two-words.trycloudflare.com/health
```

Same expected response as §3, now reachable from anywhere. If this works but
step 3 also worked, the tunnel is good — move to the frontend.

## 7. Deploy the Streamlit frontend to Streamlit Community Cloud

1. Push your latest code (this repo already has `frontend/app/requirements.txt`
   and `frontend/app/runtime.txt` in place for Streamlit Cloud).
2. Go to https://share.streamlit.io, sign in with GitHub, click **New app**.
3. Repository: your repo. Branch: `main`. Main file path: `frontend/app/main.py`.
4. Click **Deploy** (don't add secrets yet — do that next, it's clearer as a
   separate step).

## 8. Configure Streamlit secrets

In the deployed app's **Settings -> Secrets**, paste (adjust the URL to
whatever `cloudflared` printed in §5):

```toml
BACKEND_URL = "https://random-two-words.trycloudflare.com"
```

Save — Streamlit restarts the app automatically. This file is a template of
the same content: `frontend/.streamlit/secrets.toml.example`.

Also add the deployed app's own URL to your **local** backend's CORS list in
`.env`:

```
CORS_ALLOWED_ORIGINS=http://localhost:8501,https://your-app-name.streamlit.app
```

then `docker compose up -d --build backend` to pick it up.

## 9. End-to-end test

1. Open your Streamlit Community Cloud app URL in a browser.
2. Log in with a seeded account.
3. Ask a chat question that should hit retrieval (a topic covered by an
   ingested document).
4. Confirm: guardrails still run (try an obviously off-scope or injection-style
   message and confirm it's blocked), the answer cites a source, and
   role-based document scoping still holds (log in as a role restricted to
   one department and confirm it can't surface another department's docs).
5. `docker compose logs -f backend` on your PC while doing this — you should
   see the requests arriving with a `cloudflare` / tunnel user-agent, and no
   errors.

---

## 10. Optional: a stable URL instead of the random one

Only if you already own a domain and have added it to Cloudflare (free) —
this replaces the ephemeral quick tunnel with a persistent one:

```powershell
cloudflared tunnel login
cloudflared tunnel create ragchat-backend
cloudflared tunnel route dns ragchat-backend api.yourdomain.com
```

Create `%USERPROFILE%\.cloudflared\config.yml`:

```yaml
tunnel: ragchat-backend
credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
ingress:
  - hostname: api.yourdomain.com
    service: http://127.0.0.1:8010
  - service: http_status:404
```

Run it (add `cloudflared service install` afterward to make it start with
Windows, if you want persistence across reboots):

```powershell
cloudflared tunnel run ragchat-backend
```

`BACKEND_URL` becomes `https://api.yourdomain.com` and no longer changes on
restart.

---

## 11. Security considerations

- **Postgres and Qdrant are never exposed**, not even accidentally — their
  compose port mappings are bound to `127.0.0.1`, so nothing outside this
  machine can reach them regardless of tunnel/firewall state.
- **Only `/health` and the API routes behind FastAPI are reachable** through
  the tunnel — the tunnel forwards to the backend's port only.
- **CORS is a browser-side control, not the real security boundary here** —
  `api_client.py` calls the backend server-to-server (Python `requests`),
  which browsers' CORS rules don't apply to at all. The actual boundary is
  the existing JWT bearer auth + RBAC + guardrails pipeline, all unchanged.
  CORS is still configured (per your requirement) in case any client-side
  JS/custom component ever calls the API directly from the browser.
- **The quick tunnel URL is public and unauthenticated at the transport
  level** — anyone with the URL can hit `/health` or attempt `/auth/login`.
  This is no different from any public API; your existing auth/guardrails are
  what actually gate access. Don't put real user data in a demo you don't
  intend to keep securing.
- **`.env`, `frontend/.streamlit/secrets.toml`, and `.cloudflared/`** are all
  gitignored — verify `git status` never shows them before committing.
- **This depends on your PC staying on and connected.** Sleep/hibernate or a
  network drop kills the tunnel and the demo goes dark until you restart it.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker compose ps` shows `backend` unhealthy | Postgres/Qdrant not ready yet, or a code error at startup | `docker compose logs backend` — check the `/health` error detail for which store failed |
| Streamlit shows "Could not reach the backend" | `BACKEND_URL` secret wrong/missing, tunnel not running, or backend container down | Re-check §6's curl against the tunnel URL directly, outside Streamlit, to isolate tunnel vs. app |
| `cloudflared` prints connection errors on startup | No internet, or Windows Firewall blocking outbound | Confirm normal internet access; `cloudflared` only needs outbound HTTPS, no inbound firewall rule needed |
| Tunnel URL works via `curl` but not from the Streamlit app | CORS or a stale secret | Check `CORS_ALLOWED_ORIGINS` includes your `*.streamlit.app` URL and that you saved/redeployed after adding the secret |
| 401/403 from `/chat` or `/search` through the tunnel but not locally | Expired token after a Streamlit Cloud cold restart, or a genuinely different backend if the tunnel URL rotated | Log out/in again in the Streamlit app; if using the quick tunnel, confirm the URL in Secrets still matches the currently-running `cloudflared` session |
| `docker compose up` fails to bind a port | Another process already using `127.0.0.1:5432`/`6333`/`8010` (e.g. a native Postgres/Qdrant install per your local dev setup) | Stop the native service first, or change the port in `.env` |
| Qdrant data "disappeared" after restart | `docker compose down -v` was run (wipes named volumes) | Use `docker compose down` without `-v` — data lives in the `qdrant_storage`/`postgres_data` named volumes and survives normal restarts |
