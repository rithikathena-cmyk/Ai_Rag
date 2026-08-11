# $0 Deployment: Streamlit Cloud + Render + Supabase + Qdrant Cloud

Fully-managed alternative to `docs/DEPLOYMENT_FREE_TUNNEL.md` — no PC needs to
stay on. Every piece runs on a free tier of its own managed service instead.

```text
Streamlit Community Cloud (frontend, free)
        |  HTTPS
        v
Render (FastAPI, free web service — Docker deploy)
        |
        +--> Supabase (PostgreSQL, free)
        \--> Qdrant Cloud (vector DB, free)
```

## ⚠️ Read this before you start

**Render's free web service tier is 512MB RAM.** This backend lazy-loads
`BAAI/bge-m3` (embedding) and `bge-reranker-base` (reranking) on the first
real request — not at boot, so `/health` will look fine right after deploy —
and BGE-M3 alone typically needs well over 512MB once loaded. The first chat,
search, or upload request is likely to OOM-crash the free instance. This
guide still deploys it as-is so you can see the actual failure mode yourself;
if it does OOM, your options are Render's paid Starter tier (~$7/mo, 2GB RAM)
or moving the backend back to your own PC (see the tunnel doc) while still
using Supabase/Qdrant Cloud for storage.

Two smaller, near-certain frictions on the free tier:
- **Cold start**: free services sleep after 15 minutes idle, ~1 minute to wake.
- **Slow first build**: this backend's `requirements.txt` pulls in `docling`,
  `torch`, `transformers`, and `spacy` — a multi-hundred-MB dependency tree.
  Render's free build minutes are limited; the first build may take a while.

## 1. Files changed and why

| File | Why |
|---|---|
| `backend/app/core/config.py` | Added `qdrant_url`/`qdrant_api_key` (Qdrant Cloud auth) and `postgres_sslmode` (Supabase requires SSL) — all opt-in, blank by default, so local/Docker behavior is unchanged. |
| `backend/app/db/qdrant.py` | `get_qdrant_client()` now uses `url=`+`api_key=` when `qdrant_url` is set, falling back to the existing `host=`+`port=` otherwise. |
| `backend/app/db/postgres.py` | Passes `sslmode` through to the connection only when `postgres_sslmode` is set. |
| `backend/Dockerfile` | `CMD` now binds to `$PORT` (shell form, with a `:-8000` fallback) — Render's Docker services inject a dynamic port the container must listen on; docker-compose.yml never sets `PORT`, so local behavior is unchanged. |
| `.env.example` | Documented the three new variables. |

**Why Docker deploy, not Render's native Python runtime** (the original
plan): `docling` needs `libgl1`/`libglib2.0-0` system packages a native
buildpack won't install, and the existing Dockerfile already pins CPU-only
torch wheels (smaller footprint — matters a lot on 512MB). Point Render at
`backend/Dockerfile` directly rather than requirements.txt + a start command.

**Not changed**: `frontend/app/api_client.py`, RBAC, guardrails, the
retrieval/rerank/LLM flow — same reasoning as the tunnel doc: `BACKEND_URL`
is already an env var with no hardcoded localhost, so the frontend needs zero
code changes, only a different secret value.

---

## 2. Supabase (PostgreSQL)

1. https://supabase.com → sign up (free, no card) → **New project**.
2. Once provisioned, click **Connect** (top of the project dashboard) →
   pick the **Session pooler** mode, not "Direct connection" (IPv6-only
   unless you pay for Supabase's IPv4 add-on — Render's outbound traffic is
   IPv4) and not "Transaction pooler" (port 6543, a different pooling mode).
   Session pooler's hostname looks like `aws-0-<region>.pooler.supabase.com`
   on port **5432**.
3. From that connection string, pull out the individual pieces for your
   Render environment variables (Render env vars, not `.env` — that file
   never leaves your machine):
   ```
   POSTGRES_HOST=<the pooler host, e.g. aws-0-ap-south-1.pooler.supabase.com>
   POSTGRES_PORT=5432
   POSTGRES_DB=postgres
   POSTGRES_USER=<e.g. postgres.<project-ref>>
   POSTGRES_PASSWORD=<your project's database password — decode any %XX URL-encoding first>
   POSTGRES_SSLMODE=require
   ```
4. Note: **free Supabase projects pause after 1 week of inactivity** — a
   pause means the next request fails until you click "Restore" in the
   Supabase dashboard. Not auto-recovering; you have to do this manually.

## 3. Qdrant Cloud

1. https://cloud.qdrant.io → sign up (free, no card) → **Create cluster**
   → Free tier (1GB RAM / 0.5 vCPU / 4GB disk).
2. Once ready, copy the cluster URL (starts with `https://`, ends `:6333`)
   and generate an API key.
3. Render environment variables:
   ```
   QDRANT_URL=https://xxxxx.aws.cloud.qdrant.io:6333
   QDRANT_API_KEY=<your generated key>
   ```
4. Leave `QDRANT_HOST`/`QDRANT_PORT` unset on Render — `qdrant_url` takes
   priority when present (see `db/qdrant.py`).

## 4. Render (FastAPI backend)

1. Push this repo to GitHub if you haven't already.
2. https://render.com → sign up (free, no card for the free tier) →
   **New → Web Service** → connect your repo.
3. **Important**: under "Language", choose **Docker**, not the auto-detected
   Python runtime (see §1 above for why). Root/Dockerfile path:
   `backend/Dockerfile`. Docker build context: `backend/`.
4. Instance type: **Free**.
5. **Environment** tab — add:
   ```
   ANTHROPIC_API_KEY=<your key>
   POSTGRES_HOST=<from §2>
   POSTGRES_PORT=5432
   POSTGRES_DB=postgres
   POSTGRES_USER=<from §2>
   POSTGRES_PASSWORD=<from §2>
   POSTGRES_SSLMODE=require
   QDRANT_URL=<from §3>
   QDRANT_API_KEY=<from §3>
   CORS_ALLOWED_ORIGINS=http://localhost:8501,https://your-app-name.streamlit.app
   JWT_SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
   BOOTSTRAP_ADMIN_EMAIL=<your first admin email, optional but recommended>
   BOOTSTRAP_ADMIN_PASSWORD=<a real password, optional but recommended>
   ```
   Do **not** set `PORT` yourself — Render injects it; the Dockerfile CMD
   now reads it automatically.
6. **Create Web Service** — first build will be slow (see the warning above).
   Watch the build logs; Render gives you a URL like
   `https://your-backend.onrender.com` once live.

## 5. Test the backend directly

```powershell
curl https://your-backend.onrender.com/health
```

Expected: `{"status":"ok","qdrant":"connected","postgres":"connected"}`.
If this 503s, the body names which store failed — check that store's env
vars first.

**Then send one real chat/search request** (through the deployed Streamlit
app in the next section, or directly via curl with a login token) — this is
the request that actually exercises the RAM risk from the top of this doc.

## 6. Streamlit Cloud (frontend)

Same as `docs/DEPLOYMENT_FREE_TUNNEL.md` §7-8:
1. https://share.streamlit.io → **New app** → this repo, branch `main`,
   main file `frontend/app/main.py` → Deploy.
2. **Settings → Secrets**:
   ```toml
   BACKEND_URL = "https://your-backend.onrender.com"
   ```
3. Once you know your Streamlit app's real URL, go back to Render's
   environment variables and update `CORS_ALLOWED_ORIGINS` to include it,
   then redeploy the backend.

## 7. End-to-end test

Same checklist as the tunnel doc: log in with a seeded account, ask a chat
question that should hit retrieval, try an off-scope/injection message and
confirm guardrails still block it, confirm a department-restricted role
can't see another department's documents. Watch Render's logs while doing
this — that's where an OOM kill (if it happens) will show up as the
container restarting mid-request.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend container restarts right after a chat/search/upload request | OOM — BGE-M3/reranker exceeded 512MB (see the warning at the top) | Upgrade to Render's Starter tier, or move the backend back to your own PC |
| `/health` 503s with `"postgres": "..."` error | Wrong pooler host/port, or `POSTGRES_SSLMODE` not set to `require` | Re-check §2's values; the Session pooler runs on port 5432 (not 6543, which is Transaction pooler) |
| `/health` 503s with `"qdrant": "..."` error | `QDRANT_URL` missing the `:6333` port, or wrong API key | Re-copy both from the Qdrant Cloud dashboard |
| First request after idle takes ~1 minute | Render free tier cold start (expected) | Nothing to fix — inherent to the free tier |
| Build fails or times out | Free tier build-minute limits hit by this repo's large dependency tree | Retry, or trim unused heavy deps if this becomes a recurring problem |
| Streamlit shows "Could not reach the backend" | `BACKEND_URL` secret wrong, or backend asleep/crashed | `curl` the Render URL directly (§5) to isolate Streamlit vs. backend |
| 401/403 through the deployed app but not locally | Expired token after a cold start, or CORS blocking a legitimate origin | Log out/in again; confirm `CORS_ALLOWED_ORIGINS` includes your exact `*.streamlit.app` URL |
| Supabase project shows "paused" | 1 week of inactivity (free tier) | Click "Restore" in the Supabase dashboard — manual, no auto-recovery |
