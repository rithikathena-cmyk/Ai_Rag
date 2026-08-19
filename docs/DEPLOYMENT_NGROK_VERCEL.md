# $0 Deployment: Vercel + ngrok

Public demo of the React frontend (`frontend-react/`) at zero hosting cost.
The frontend deploys to Vercel (free); FastAPI, PostgreSQL, and Qdrant stay
on your PC exactly as they run today (native `.venv`, not Docker); a free
ngrok tunnel exposes only the FastAPI API to the internet over HTTPS.

```text
Vercel (frontend-react static build)
        |  HTTPS
        v
ngrok tunnel (ngrok http 8010, running natively on your PC)
        |
        v
FastAPI (native .venv, 127.0.0.1:8010)
        |
        v
  |-- PostgreSQL (localhost:5432)
  \-- Qdrant     (127.0.0.1:6333)
```

Your PC has to be on, with the backend running, for the public demo to work
— this is a demo/dev architecture, not production hosting. Supersedes
`docs/DEPLOYMENT_FREE_TUNNEL.md` and `docs/DEPLOYMENT_FREE_MANAGED.md` for
this purpose — both predate the React rewrite and target the old Streamlit
frontend (`frontend/`), which is no longer what's deployed.

---

## 1. Files changed and why

| File | Why |
|---|---|
| `frontend-react/src/api/client.ts` | `baseURL` now reads `VITE_API_BASE_URL` (falling back to `/api`, so local dev via Vite's proxy is unchanged) — a static Vercel build has no dev-server proxy at runtime, so it needs the backend's real origin baked in at build time. Also added the `ngrok-skip-browser-warning` header unconditionally: ngrok's free tier serves an HTML interstitial in place of the real API response otherwise, which would break every request silently (JSON parse errors, not 4xx/5xx) — harmless against any other backend origin. |
| `frontend-react/vercel.json` | New — SPA fallback rewrite (`/*` → `/index.html`) so React Router routes like `/guardrail-policies` resolve on direct navigation/refresh instead of 404ing (Vercel serves static files by default with no knowledge of client-side routes). |
| `.env.example` | `CORS_ALLOWED_ORIGINS` comment/default updated — no longer references the Streamlit Community Cloud URL that's no longer part of this deployment. |

**Not changed**: backend RBAC, guardrails, retrieval/rerank/LLM flow, and the
`CORS_ALLOWED_ORIGINS` mechanism itself (already existed, already wired into
`CORSMiddleware` in `main.py`).

---

## 2. Start the backend locally

Same as your normal local dev flow — native `.venv`, not Docker:

```powershell
../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

(run from `backend/`; adjust the venv path if different — allow ~2 minutes
for model warmup on first boot)

## 3. Test the backend locally

```powershell
curl http://127.0.0.1:8010/health
```

Expected: `{"status":"ok","qdrant":"connected","postgres":"connected"}`.

## 4. Install and authenticate ngrok

Already installed this session (`winget install --id Ngrok.Ngrok`). ngrok
requires a free account — this step is yours to do interactively:

1. Sign up at https://dashboard.ngrok.com/signup (free, no card).
2. Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken.
3. In a terminal:
   ```powershell
   ngrok config add-authtoken <your-token>
   ```

## 5. Create the public HTTPS tunnel

```powershell
ngrok http 8010
```

Leave this running — it prints a public URL like:

```text
https://random-word-1234.ngrok-free.app
```

This is free but **ephemeral** — it changes every time you restart `ngrok`
(a paid plan gets a reserved, stable domain instead). Each rotation means
redoing §7 and §9 below with the new URL.

## 6. Test the public API

```powershell
curl https://random-word-1234.ngrok-free.app/health
```

Same expected response as §3, now reachable from anywhere.

## 7. Add the tunnel URL to CORS

In `.env` (project root):

```
CORS_ALLOWED_ORIGINS=http://localhost:5173,https://random-word-1234.ngrok-free.app
```

Restart the backend (config is loaded once at startup) to pick it up.

## 8. Deploy the frontend to Vercel

```powershell
npm install -g vercel   # already done this session
vercel login            # interactive — opens a browser
cd frontend-react
vercel link              # first time only: creates/links the Vercel project
vercel env add VITE_API_BASE_URL production
# paste https://random-word-1234.ngrok-free.app when prompted
vercel --prod
```

Vercel prints your production URL, e.g. `https://your-app.vercel.app`. Vite
bakes `VITE_API_BASE_URL` in at build time — any time the ngrok URL changes
(§5), update the env var (`vercel env rm/add`) and re-run `vercel --prod`.

## 9. Add the Vercel URL to CORS too

Same as §7:

```
CORS_ALLOWED_ORIGINS=http://localhost:5173,https://random-word-1234.ngrok-free.app,https://your-app.vercel.app
```

Restart the backend again.

## 10. End-to-end test

1. Open your `https://your-app.vercel.app` URL in a browser.
2. Log in with a seeded/demo account.
3. Ask a chat question that should hit retrieval (a topic covered by an
   ingested document) — confirm it cites a source.
4. Try an obviously off-scope or injection-style message and confirm
   guardrails still block it.
5. Log in as a role restricted to one department and confirm it can't
   surface another department's documents.
6. Watch the terminal running `ngrok http 8010` — you should see the
   requests arriving with a `200` status and no tunnel errors.

---

## 11. Security considerations

- **Postgres and Qdrant are never exposed** — nothing in this setup opens a
  port for them; only `ngrok http 8010` (the FastAPI port) is tunneled.
- **CORS is a browser-side control, not the real security boundary** — the
  actual boundary is the existing JWT bearer auth + RBAC + guardrails
  pipeline, all unchanged. CORS just decides which origins a browser will let
  JS call the API from.
- **The ngrok URL is public and unauthenticated at the transport level** —
  anyone with it can hit `/health` or attempt `/auth/login`. No different
  from any public API; your existing auth/guardrails are what actually gate
  access. Don't put real user data in a demo you don't intend to keep
  securing.
- **This depends on your PC staying on and connected.** Sleep/hibernate, a
  network drop, or closing the `ngrok` terminal kills the tunnel and the demo
  goes dark until you restart it (with a new URL, unless on a paid plan).
- Verify `.env` is still gitignored before committing anything — it holds
  `ANTHROPIC_API_KEY`, `JWT_SECRET_KEY`, and DB credentials.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Vercel app shows a generic network error, or the browser console shows a JSON parse error | `ngrok-skip-browser-warning` header missing, or ngrok tunnel not running | Confirm `client.ts` sets the header (already added); `curl` the ngrok URL directly to confirm the tunnel is up |
| 401/403 through the deployed app but not locally | Expired token after redeploy, or CORS blocking the origin | Log out/in again; confirm `CORS_ALLOWED_ORIGINS` includes your exact `*.vercel.app` URL, no trailing slash |
| Routes 404 on refresh (e.g. `/guardrail-policies`) | `vercel.json` SPA rewrite missing or not deployed | Confirm `frontend-react/vercel.json` exists and was included in the deploy |
| Frontend calls still hit `/api` (relative) instead of the ngrok URL | `VITE_API_BASE_URL` not set as a Vercel **production** env var before the build ran | `vercel env ls` to check; env vars only take effect on the next build — re-run `vercel --prod` after adding it |
| `ngrok http 8010` fails to start / asks to sign in | Authtoken not configured | Re-run §4 |
| Backend works locally but ngrok URL 502s | Backend not actually running on port 8010, or crashed | Re-check §2/§3 |
