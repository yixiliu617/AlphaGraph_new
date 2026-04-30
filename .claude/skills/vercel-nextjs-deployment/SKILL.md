---
name: vercel-nextjs-deployment
description: End-to-end procedure for deploying a Next.js frontend to Vercel — covers project setup (root directory, framework preset, install commands), env-var wiring (build-time NEXT_PUBLIC_* vs runtime, per-environment scoping), cross-origin backend integration (CORS, cookies, SameSite=None requirement), pre-deploy build hardening (TS strict-mode gotchas, Next.js version-bump policies), Vercel security policies (CVE-blocking deploys silently), and the dozen edge cases we hit during the AlphaGraph rollout (Vercel UI log truncation, Next 15 PageProps breaking change, two-Claude-sessions-on-one-repo race, PowerShell grep, etc.). Use whenever you are deploying a Next.js app to Vercel — first time OR debugging a stuck deploy. Pairs with `oauth-service-integration` (OAuth providers need Vercel hostnames added) and `deployment_runbook.md` (Render/Neon side).
version: 1.0
last_validated_at: 2026-04-29
conditions: []
prerequisites: []
tags: [deployment, vercel, nextjs, frontend, ci-cd, procedure]
---

# Vercel + Next.js Deployment

Get a Next.js frontend deployed to Vercel correctly **on the first try**. Captures the dozen edge cases we paid for during the AlphaGraph rollout so future deploys don't repeat them.

## When to use this skill

- First-time Vercel deploy of a Next.js app
- Debugging a Vercel deploy that's stuck, failing, or "succeeds but doesn't work"
- Wiring a Vercel-hosted frontend to a backend on a different domain (Render / AWS / etc.) — the cross-origin setup is non-obvious
- Reviewing a project's Vercel config before pilot demos

## What "done" looks like

- Deployment shows green ✓ in Vercel dashboard
- `https://<project>.vercel.app` loads the app
- Cross-origin backend calls succeed with cookies (auth survives page refresh)
- Vercel preview deploys (per-PR URLs) also work without backend changes
- No "Vulnerable version" warnings at end of build log

---

## Part A: Pre-flight checklist

Before clicking "New Project" in Vercel, gather:

| Item | Where to get it |
|---|---|
| GitHub repo URL | The actual *human* URL `https://github.com/<user>/<repo>` (no `.git` suffix) |
| Root directory of the Next app | If monorepo, the subfolder (e.g. `frontend`). If repo root IS the Next app, blank. |
| Backend URL | The full `https://api.example.com` (or `https://backend.onrender.com`) — *not* localhost |
| `NEXT_PUBLIC_*` vars | These are baked at BUILD time, not runtime. List them all up front. |
| Domain (optional) | If using a custom domain, know which TLD before deploy so DNS records are ready |

**Critical fact:** `NEXT_PUBLIC_*` env vars are **inlined into JS at build time**. Changing them requires a redeploy. Plan accordingly.

---

## Part B: Vercel project setup

### B.1 Connect repo

1. https://vercel.com → **Add New** → **Project**
2. Select the GitHub repo. If first time, authorize Vercel's GitHub app for the org/account.
3. **Configure project** — read every field, don't skip-and-deploy:

| Field | Value | Why it matters |
|---|---|---|
| **Framework Preset** | Next.js (auto-detected) | Tells Vercel to run `next build` and use Next.js's serverless adapter |
| **Root Directory** | `frontend` (or wherever `package.json` lives) ★ | If the Next app is in a subfolder and you don't set this, Vercel tries to build from repo root and fails confusingly. **Most common first-deploy mistake.** |
| **Build Command** | leave default (`next build`) | Override only if using a custom build script |
| **Output Directory** | leave default (`.next`) | Override only if `next.config.js` sets `distDir` |
| **Install Command** | leave default | Vercel detects from lockfile (`package-lock.json` → npm, `yarn.lock` → yarn, `pnpm-lock.yaml` → pnpm) |
| **Node Version** | 20.x or 22.x (set in `engines` or via Vercel UI) | Default is whatever's current; pin if you have node-version-sensitive deps |

### B.2 Set env vars BEFORE first deploy

Scroll down to **Environment Variables**. Set the runtime env vars NOW:

```
NEXT_PUBLIC_API_URL = https://placeholder.example.com/api/v1
```

Use a placeholder if the real backend URL doesn't exist yet — fix in step B.4. **Do not skip this** — if you deploy with the env var unset, the build will inline `undefined` everywhere it's referenced.

### B.3 Click Deploy

First build runs in ~3–5 min. Watch the Build Logs panel.

### B.4 After deploy: wire to real backend

1. Settings → Environment Variables
2. Edit `NEXT_PUBLIC_API_URL` → set to actual backend hostname
3. **Deployments** tab → top deployment → ⋮ → **Redeploy**

Env var changes don't take effect until a redeploy because `NEXT_PUBLIC_*` is baked into the build.

---

## Part C: Cross-origin backend integration

When frontend is on `*.vercel.app` and backend is on `*.onrender.com` (or anywhere else), browsers enforce strict cross-origin rules.

### C.1 Frontend fetch must include credentials

```typescript
// frontend/src/lib/api/base.ts
const response = await fetch(url, {
    method, headers, body,
    credentials: "include",   // ← REQUIRED for cookies cross-origin
});
```

Without this, the browser strips cookies and auth-protected endpoints 401.

### C.2 Backend CORS must allow credentials

FastAPI:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],         # exact origin, NOT wildcard
    allow_origin_regex=settings.CORS_ORIGIN_REGEX, # for vercel preview deploys
    allow_credentials=True,                         # ← required
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`allow_origins=["*"]` with `allow_credentials=True` is **invalid** (browsers reject it). Always list explicit origins or use a regex.

### C.3 Cookies must be SameSite=None + Secure in production

```python
SESSION_COOKIE_SECURE   = True    # HTTPS only
SESSION_COOKIE_SAMESITE = "none"  # cross-origin allowed (REQUIRED when frontend + backend are different domains)
```

`SameSite=Lax` (the dev default) **blocks cross-origin cookies entirely**. The browser silently drops them — no error, just 401s on auth endpoints.

### C.4 Vercel preview deploys (per-PR URLs)

Each PR gets a unique Vercel URL like `alphagraph-git-feat-xyz-username.vercel.app`. CORS config must allow these without redeploying the backend per-PR.

```python
CORS_ORIGIN_REGEX = r"^https://alphagraph-.*\.vercel\.app$"
```

Pair with the canonical production origin in `CORS_ORIGINS`.

### C.5 OAuth provider redirect URI updates

**Forgetting this is the #1 reason "sign-in worked locally but not on Vercel".**

Add Vercel hostname (and Render/AWS hostname, if separate) to:

- Google Cloud Console → Credentials → OAuth Client → Authorized redirect URIs → click outer **Save** ★
- Microsoft Entra ID → App registration → Authentication → Web → Redirect URIs → **Save**

Both `/api/v1/auth/<provider>/callback` AND `/api/v1/connections/callback/<provider>` paths if you have both auth + service-connection flows.

See `oauth-service-integration` SKILL.md for full provider walkthrough.

---

## Part D: Build hardening

Vercel's `next build` is stricter than `next dev`. These break in production builds even though they pass locally:

### D.1 Next.js 15: dynamic-route `params` is now a Promise ★

Next 15 changed `params` from sync object to `Promise<{...}>` — **breaking change for dynamic routes** (`[id]/page.tsx`, `[slug]/page.tsx`, etc.).

**Old (Next 14):**
```tsx
interface Props { params: { id: string } }
export default function Page({ params }: Props) {
  return <Foo id={params.id} />;
}
```

**New (Next 15):**
```tsx
interface Props { params: Promise<{ id: string }> }
export default async function Page({ params }: Props) {
  const { id } = await params;
  return <Foo id={id} />;
}
```

Same for `searchParams`. If you upgrade from Next 14 → 15 and forget this, every dynamic-route page will fail type-check on Vercel with:

```
Type error: Type 'Props' does not satisfy the constraint 'PageProps'.
  Types of property 'params' are incompatible.
```

To find every dynamic page that needs updating:

```bash
find frontend/src/app -path '*\[*\]*' -name 'page.tsx'
```

### D.2 TypeScript strict-mode catches dead code

Vercel's production build runs `tsc --noEmit` strictly. Comparisons that the type system says are impossible fail compilation:

```typescript
// Type of `code` is 'BMO' | 'AMC' | 'TBD' | undefined
if (!code || code === "") return null;  // ← FAILS: '"" has no overlap with the union'
```

**Fix:** drop the redundant check.

```typescript
if (!code) return null;
```

`next dev` doesn't catch these — only the production build does.

### D.3 Case-sensitive imports

Linux is case-sensitive, Windows/macOS aren't. An import like `import X from "./mycomponent"` works on dev (Mac/Win) but **fails on Vercel (Linux)** if the file is `MyComponent.tsx`.

Pre-flight check:

```bash
# spot common mismatches in your imports
grep -rE 'from "[\.@]/[a-z]' frontend/src/  # imports starting lowercase
ls frontend/src/components/                 # check actual filenames
```

If you've ever renamed a file to change case, git on Windows/Mac may have kept the old casing. Force-fix:

```bash
git mv -f MyComponent.tsx Tmp.tsx
git mv -f Tmp.tsx MyComponent.tsx
```

### D.4 Next.js version policy: Vercel blocks vulnerable versions ★

This one is silent and confusing. If your Next.js version has a published CVE, **Vercel refuses to deploy** even though the build itself succeeds. Symptom:

```
✓ Build Completed in /vercel/output [56s]
Vulnerable version of Next.js detected, please update immediately.
[Deployment shows "Failed" in UI with no obvious error]
```

The fix is to bump to the latest patched version of your Next.js major:

```bash
npm install next@latest --save     # latest stable
# or pin to known-good patch:
npm install next@15.5.15 --save
```

Always check https://nextjs.org/blog or https://nextjs.org/docs/messages for known CVEs before deploying.

### D.5 `next-env.d.ts` regenerates and may differ between OS

`npm install next` regenerates `frontend/next-env.d.ts`. Sometimes the diff is OS-dependent (line endings, etc.). Commit it as-is — Vercel doesn't care, but git complains otherwise.

### D.6 Build memory limits

Default Vercel build runs in 8 GB RAM. Heavy monorepo type-checking can OOM. Symptoms: build hangs at "Linting and checking validity of types ..." then dies silently.

Fixes (try in order):
1. Run `tsc --noEmit` locally; if it spikes >4 GB, you have type-checking work to do
2. Split workspace into smaller projects (turborepo)
3. Upgrade Vercel plan (Pro = 16 GB)
4. As last resort: disable Next's type check at build time (`next.config.js: typescript: { ignoreBuildErrors: true }`) — masks future bugs, do not use casually

---

## Part E: Test + verify

After every deploy:

### E.1 Frontend renders

`https://<project>.vercel.app` loads and routes work.

### E.2 Backend health check responds cross-origin

Open browser console → Network tab → reload page. Look for:
- API requests have `Cookie: ag_session=...` in Request Headers (or whatever cookie name)
- API responses have `Access-Control-Allow-Origin: https://<project>.vercel.app` and `Access-Control-Allow-Credentials: true`
- No CORS errors in console

### E.3 Sign-in flow works end-to-end

Click Sign In with Google → consent → land back logged-in (not 401).

If it 401s after consent, run through Part C step-by-step. The most common cause is the cookie SameSite/Secure config.

### E.4 Preview deploys work

Push a feature branch + open a PR → Vercel auto-deploys preview → that URL also works (CORS_ORIGIN_REGEX should match it).

### E.5 No CVE warning at end of build log

Scroll to the very bottom of the Build Logs. The last meaningful line should be `Build Completed`, NOT `Vulnerable version of Next.js detected`.

---

## Part F: Edge cases / gotchas (the dozen we paid for)

### G.1 Vercel UI truncates build logs

Symptom: log says "70 lines" but only ~15 are visible; you can't find the error.

Fixes:
- Click inside the log panel and **scroll down**
- Or use Vercel CLI: `npm install -g vercel && vercel logs <url> --output raw`
- Or look for a "View All Logs" / download button (varies by Vercel UI version)

### G.2 "Deployment failed" with NO build error

Build logs end clean (`Build Completed`) but UI shows "Failed". This is usually:
- Vercel CVE policy blocked the deploy (D.4)
- Function size exceeded (50 MB Hobby / 250 MB Pro per serverless function)
- Custom domain assignment failed (Vercel sometimes shows this generically)

Look for the LAST informative line of the log. If it's not a build error, it's a post-build policy issue.

### G.3 `Module not found` on Vercel only

Almost always case-sensitivity (D.3) OR a file is in `.gitignore` so it's not in the Vercel clone. Check:

```bash
git check-ignore -v <the-file>
```

If it's ignored, either remove from gitignore or move the file.

### G.4 `process.env.X is undefined` at build time

`NEXT_PUBLIC_*` vars MUST be set before build. If you set them after first deploy, redeploy is required (env var is inlined at build time).

### G.5 Cookies don't persist after sign-in

Always one of:
- Frontend `fetch` missing `credentials: "include"` (C.1)
- Backend CORS missing `allow_credentials=True` (C.2)
- Cookie missing `SameSite=None` + `Secure` in prod (C.3)
- Cookie domain set wrong (e.g. `.example.com` when actual domain is `app.example.com`)

### G.6 OAuth `redirect_uri_mismatch` on Vercel

Add the Vercel URL to Google Cloud Console / Microsoft Entra ID redirect URIs (C.5). And **click the outer Save button** in Google Cloud — the inline edit is not the same as form submit.

### G.7 Vercel CLI vs UI mismatch

If `vercel --prod` from CLI behaves differently than the auto-build from GitHub push: usually because the CLI uploads your local `node_modules` which can differ from a fresh `npm install`. Always trust the auto-build (fresh clone + install) for prod state.

### G.8 PowerShell doesn't have `grep`

When I tell you to run `git log --oneline | grep <pattern>`, PowerShell users substitute:

```powershell
git log --oneline | Select-String "<pattern>"
# or:
git log --oneline | findstr "<pattern>"
```

### G.9 Two Claude/AI sessions on one repo

Running two AI sessions in two PowerShell windows on the same repo causes:
- Concurrent commits with non-FF conflicts
- Each session sees stale `git log` output
- One session's commits appear "missing" because the other session pushed first

Fix: use `git worktree add ../sibling-dir -b feat/branch-name` to give each session its own checkout + branch. Or just close one session.

### G.10 Build cache misses on every deploy

Vercel caches `node_modules` between builds. Cache invalidates if:
- `package-lock.json` content changes
- Node version changes
- Build environment variables change

Cold install adds ~1-2 min to build. If your builds are slow, check if you're invalidating cache unintentionally (e.g. running `npm install <pkg>` and committing the lockfile change with no actual dep change).

### G.11 Build succeeds but preview URL shows 404

Usually `output: 'export'` in `next.config.js` (static export) but you have dynamic routes that need server functions. Either remove `output: 'export'` or convert all routes to static.

### G.12 Vercel preview deploys hit production backend

Vercel previews have their own preview URL but typically use the same env vars as production. If you want preview to use a staging backend, set `NEXT_PUBLIC_API_URL` separately in the **Preview** environment dropdown (not just Production).

---

## Part G: Output format

When this skill is invoked to set up a new Vercel deploy, deliverable is:

1. **Vercel project config** (settings + env vars set per Parts B and C)
2. **Code commits** with any Next.js / TS / cookie / CORS changes from Part D
3. **Verification** as a PR description checklist:
   - [ ] Build green ✓ (no `Vulnerable version` warning)
   - [ ] Frontend loads at Vercel URL
   - [ ] API calls succeed cross-origin (Network tab clean)
   - [ ] Sign-in flow works end-to-end (auth cookie persists)
   - [ ] Preview deploy from a PR also works
   - [ ] OAuth redirect URIs updated in Google Cloud + Microsoft Entra
4. **Memory / docs updates** if a new edge case arose (append to Part F here, bump version + last_validated_at)

---

## Reference: where deploy config lives in this codebase

- Frontend Vercel config: implicit (Vercel reads `frontend/package.json` + `frontend/next.config.js`)
- Backend CORS: `backend/main.py` + `backend/app/core/config.py`
- Frontend fetch base: `frontend/src/lib/api/base.ts`
- Cookie config (prod): `backend/app/core/config.py` (`SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`, `SESSION_COOKIE_DOMAIN`)
- Production env-var template: `.env.production.example`
- Companion skills: `oauth-service-integration`, `deployment_runbook.md`

## Change log

- **1.0 (2026-04-29)** — initial. Built from the Vercel + Render + Neon rollout for AlphaGraph. 12 edge cases captured (★ = the three that bit hardest: Next 15 PageProps async breaking change, CVE-policy silent block, cookie SameSite=None requirement).
