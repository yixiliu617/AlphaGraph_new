# AlphaGraph — Deployment Runbook (Vercel + Render + Neon)

**Status:** v1, 2026-04-29.
**Target:** first pilot URL, AWS-migration-ready.
**Estimated time first run:** 3–4 hours, mostly account setup + OAuth-redirect updates.

---

## What you'll have at the end

```
https://alphagraph.vercel.app           ← frontend (Vercel)
            ↓ fetch (cookies cross-origin)
https://alphagraph-backend.onrender.com ← FastAPI (Render web)
            ↓ APScheduler in another Render service
            ↓ shared Postgres
ep-cool-name.neon.tech                  ← Postgres (Neon, pgvector ready)
            ↓ parquets + IR PDFs
Backblaze B2 bucket "alphagraph-data"   ← OR Render Disk for pilot
```

Sized for 0–25 paying users at ~$15–80/mo total.

---

## Pre-flight

You'll need:
- GitHub repo with this code pushed
- A credit card (every vendor below has free tier first; some require a card on file)
- ~3 hours uninterrupted

---

## Phase 0 — Decisions to lock before clicking

| Decision | Default | Notes |
|---|---|---|
| Backend region | `singapore` | ~30ms to HK, ~70ms to Tokyo, ~150ms to NYC. If pilots are mostly US, use `oregon`. |
| Frontend region | `auto` (Vercel global edge) | Don't change |
| Postgres region | match backend | Neon `aws-ap-southeast-1` for Singapore Render |
| Storage backend | `fs` for pilot, `s3` later | Switching is one env-var flip + a one-shot rsync |
| Custom domain | not needed v1 | `*.vercel.app` and `*.onrender.com` are fine for first pilots |

---

## Phase 1 — Provision accounts (60 min)

### 1.1 Neon (Postgres) — 10 min

1. Sign up at https://console.neon.tech.
2. Create project: name `alphagraph`, region `AWS ap-southeast-1 (Singapore)` (or match Render region).
3. Default database `neondb` is fine, or rename to `alphagraph`.
4. **Connection string** — click "Connection Details" (or the Connect button) → copy the **pooled** string (host contains `-pooler`, e.g. `postgresql://...@ep-xxx-pooler.../alphagraph?sslmode=require`). Paste into a temp text file; you'll need it in 1.4.
5. **Enable Postgres extensions** (required for migrations + Pillar A RAG later):
    - Neon dashboard → click your project → **left sidebar → "SQL Editor"** (icon `</>`).
    - Confirm the database dropdown at the top shows your database (`neondb` or `alphagraph`).
    - Paste these two commands together:
      ```sql
      CREATE EXTENSION IF NOT EXISTS pgcrypto;
      CREATE EXTENSION IF NOT EXISTS vector;
      ```
    - Click **▶ Run** (or `Ctrl+Enter`). Should output `CREATE EXTENSION` twice.
    - Verify with:
      ```sql
      SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto', 'vector');
      ```
      Expect 2 rows.
    - **Alternative** (terminal): `psql "<your-connection-string>" -c "CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS vector;"`

### 1.2 Backblaze B2 (S3-compatible storage) — 10 min

Skip this if pilot is staying on Render Disk. You can flip to B2 later.

1. Sign up at https://www.backblaze.com/b2/sign-up.html.
2. Buckets → Create a Bucket: name `alphagraph-data`, files private, region `us-west-002` (or closest).
3. App Keys → Add a New Application Key: bucket-scoped to `alphagraph-data`, read/write.
4. Save: `keyID` (=`S3_ACCESS_KEY_ID`), `applicationKey` (=`S3_SECRET_ACCESS_KEY`), and the endpoint URL (e.g. `https://s3.us-west-002.backblazeb2.com`).

### 1.3 Vercel (frontend) — 10 min

1. Sign up at https://vercel.com.
2. Import your GitHub repo. Vercel auto-detects Next.js.
3. Project settings:
    - **Root Directory:** `frontend/`
    - **Framework Preset:** Next.js
    - **Build Command:** (default — `next build`)
    - **Output Directory:** (default — `.next`)
4. Don't deploy yet — first set the env var:
    - Settings → Environment Variables → Add:
      - `NEXT_PUBLIC_API_URL` = (placeholder — fill after Render is up)
5. Trigger first deploy after Render is live.

### 1.4 Render (backend + worker) — 25 min

1. Sign up at https://render.com.
2. New + → Blueprint → connect this repo. Render reads `render.yaml` from the root.
3. Update the `repo:` fields in `render.yaml` with your actual GitHub URL, push the change.
4. Render proposes 2 services: `alphagraph-backend` (web) + `alphagraph-prices-scheduler` (worker). Click Apply.
5. **Set the secrets** for `alphagraph-backend` (Environment tab):
    - `POSTGRES_URI` = (Neon pooled connection string from 1.1)
    - `AUTH_DATABASE_URI` = (leave blank — falls back to POSTGRES_URI)
    - `TOKEN_ENCRYPTION_KEY` = run locally: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and paste
    - `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` = from Phase 2.1
    - `MICROSOFT_OAUTH_CLIENT_ID` / `_SECRET` = from Phase 2.2
    - `ANTHROPIC_API_KEY` = your Anthropic key
    - `OAUTH_REDIRECT_BASE` = `https://<your-render-host>.onrender.com`  (Render assigns the host on first deploy; come back here)
    - `FRONTEND_URL` = `https://<your-vercel-host>.vercel.app`
    - `CORS_ORIGINS` = `https://<your-vercel-host>.vercel.app`
6. Wait for first build (~5 min). Verify `/healthz` returns 200.
7. Run the migrations + seed (one-time):
    - Render dashboard → Shell tab on `alphagraph-backend`:
      ```sh
      cd /app/backend && alembic upgrade head
      cd /app && PYTHONPATH=. python -m backend.scripts.seed_universe
      ```
    - Verify: should see "rows: 31 / listings: 394 / pre_ipo_watch: 28".

### 1.5 Wire frontend to backend — 5 min

1. Go back to Vercel → Settings → Environment Variables:
    - `NEXT_PUBLIC_API_URL` = `https://<your-render-host>.onrender.com/api/v1`
2. Deploy → trigger redeploy.
3. Visit `https://<your-vercel-host>.vercel.app` — frontend should load and successfully fetch from backend.

---

## Phase 2 — OAuth provider config (30 min)

### 2.1 Google Cloud Console

1. https://console.cloud.google.com → APIs & Services → Credentials → your OAuth Client.
2. Authorized redirect URIs — add BOTH (don't replace existing localhost ones):
    - `https://<render-host>.onrender.com/api/v1/auth/google/callback`
    - `https://<render-host>.onrender.com/api/v1/connections/callback/google`
3. **Click the outer Save button** at the bottom (this trips most people up).

### 2.2 Microsoft Entra ID

1. https://entra.microsoft.com → Identity → Applications → App registrations → AlphaGraph.
2. Authentication → Web → Redirect URIs — add BOTH:
    - `https://<render-host>.onrender.com/api/v1/auth/microsoft/callback`
    - `https://<render-host>.onrender.com/api/v1/connections/callback/microsoft`
3. Save.

### 2.3 Test sign-in

1. Visit the deployed frontend, click Sign in with Google.
2. Should redirect → Google consent → back to your frontend logged in.
3. If 401: check `CORS_ORIGINS` includes the exact Vercel URL and `SESSION_COOKIE_SAMESITE=none` + `SESSION_COOKIE_SECURE=true`.

---

## Phase 3 — Storage migration to S3 (when ready)

The pilot can run on Render Disk indefinitely (cheaper, simpler). Switch to S3 when:
- You need to share parquets between web + worker dynos
- A second backend instance becomes useful (HA / multi-region)
- Disk usage approaches the 10 GB allotment

Steps:

1. Provision Backblaze B2 (Phase 1.2 if skipped).
2. Render env (both services):
    - `STORAGE_BACKEND=s3`
    - `S3_BUCKET=alphagraph-data`
    - `S3_ENDPOINT_URL=https://s3.us-west-002.backblazeb2.com`
    - `S3_REGION=us-west-002`
    - `S3_ACCESS_KEY_ID=<from B2>`
    - `S3_SECRET_ACCESS_KEY=<from B2>`
3. Add `boto3>=1.34` to `backend/requirements.txt` and redeploy.
4. One-time data sync: from Render shell on `alphagraph-backend`:
    ```sh
    cd /app && python -m backend.scripts.migrate_data_to_s3   # planned helper
    ```
    Or use rclone locally to push `backend/data/` → B2.
5. Restart both services. They now read/write S3 transparently.

---

## Phase 4 — AWS migration (Year 2 trigger)

When one of these triggers fires (see `roadmap_v1.md` § 5):
- Hosting bill > $1,000/mo
- Pillar B audio pipeline goes prod
- Institutional customer requests AWS-hosted with documented controls
- Render multi-day outage
- Neon free + scaler tier exceeded

Migration:
1. **Frontend stays on Vercel** (or moves to Amplify; Vercel is fine indefinitely).
2. **Database:** Neon → AWS RDS Postgres (or AWS Aurora Serverless). `pg_dump` + `pg_restore`. Update `POSTGRES_URI` env var. Done.
3. **Storage:** S3 endpoint → AWS S3 endpoint. Change `S3_ENDPOINT_URL=` (blank) + `S3_REGION=us-east-1` (or wherever). Optionally `aws s3 sync` from B2 to AWS S3 for one-shot copy.
4. **Compute:** Build same Dockerfile, push to ECR. Define ECS Fargate tasks: one for `web` (matches the web service in render.yaml), one for `worker` (matches the worker). Application Load Balancer in front of web. ACM cert for HTTPS.
5. **OAuth callbacks** updated to the new ALB hostname (or custom domain).
6. Cut over DNS. Monitor.

Estimated migration effort: 2–4 days for someone who's done it before, ~1 week first time.

---

## Phase 5 — Day-2 ops

### Logs
- Render: Logs tab on each service. Stream real-time, filter, download.
- Vercel: Deployments → Function Logs.
- Neon: Operations → Logs.

### Metrics
Add Sentry (free tier covers 5k errors/mo):
1. https://sentry.io → create project (Python + Node).
2. Add `SENTRY_DSN_BACKEND` and `SENTRY_DSN_FRONTEND` env vars.
3. Add the Sentry SDKs (`sentry-sdk[fastapi]` for backend, `@sentry/nextjs` for frontend) — left as future work.

### Backups
- Neon: automatic point-in-time recovery within retention window (7d on free, 30d on paid).
- Backblaze B2: lifecycle rule for versioning if data is critical (~$0.005/GB-mo extra).
- Render Disk: snapshot manually via Render dashboard (no automatic schedule on starter plan).

### Custom domain
1. Add domain to Vercel (Settings → Domains). Verify DNS.
2. Add same domain on Render with a CNAME for the backend subdomain (e.g. `api.alphagraph.com`).
3. Update env vars: `OAUTH_REDIRECT_BASE`, `FRONTEND_URL`, `CORS_ORIGINS`. Update OAuth provider redirect URIs.
4. Optionally enable `SESSION_COOKIE_DOMAIN=.alphagraph.com` so cookies span subdomains.

---

## Common failure modes (and fixes)

| Symptom | Cause | Fix |
|---|---|---|
| 401 from frontend, 200 from same URL in browser bar | `credentials: "include"` missing OR `SESSION_COOKIE_SAMESITE` not `none` in prod | Already fixed in `frontend/src/lib/api/base.ts`; verify env vars |
| OAuth redirect_uri_mismatch | Forgot to add the new Render URL in Google/Microsoft console (or didn't click outer Save in Google) | See `oauth-service-integration` skill, edge case E.1 |
| `relation "app_user" does not exist` | Forgot to run `alembic upgrade head` on Neon | Phase 1.4 step 7 |
| `gen_random_uuid() does not exist` | pgcrypto extension not enabled on Neon | Phase 1.1 step 7 |
| `vector type does not exist` (Pillar A) | pgvector not enabled | Phase 1.1 step 6 |
| Worker doesn't fire jobs | Worker service crashed; check Render logs. Common cause: missing env var that the web service has. | render.yaml uses `fromService` to share env; verify both services have all needed secrets |
| Frontend says "Loading..." forever | CORS reject — check browser console; CORS_ORIGINS doesn't include Vercel host | Update `CORS_ORIGINS` env var on Render |

---

## References

- Architecture v3: `architecture_and_design_v3.md` § 7 deployment topology
- OAuth integration skill: `.claude/skills/oauth-service-integration/SKILL.md` (15 captured edge cases)
- Active roadmap: `roadmap_v1.md`
- Storage abstraction: `backend/app/core/storage.py` (the lever for AWS migration)
