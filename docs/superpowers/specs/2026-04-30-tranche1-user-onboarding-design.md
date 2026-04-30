# Tranche 1 — User Onboarding (Sign-In, Wizard, Sectors, Themes)

**Status:** design draft, awaiting implementation plan
**Date:** 2026-04-30
**Scope:** sign-up flow, sign-in UX, six-step onboarding wizard, sector + country + theme data model, founding-member referral
**Out of scope:** tier model + entitlements (Tranche 2), Stripe billing (Tranche 2), team workspace + sharing (Tranche 3), full theme universe with cross-references (Tranche 3)

---

## 1. Goal

Enable invited buyside users to sign in via Google or Microsoft OAuth, complete a 6-step onboarding wizard that captures their role / firm strategy / sectors / countries / themes / referrals, and land in a personalized dashboard — all in under 60 seconds for a typical user.

The wizard answers also seed the per-user universe (subscribed thesis groups → companies → news/alerts/chat priors) so the dashboard is useful from minute zero.

## 2. Architecture overview

```
┌─────────────────────────┐         ┌──────────────────────────┐
│  alphagraph.com         │         │  alphagraph-backend      │
│  (Vercel — Next.js)     │         │  (Render — FastAPI)      │
│                         │         │                          │
│  /                      ├────────▶│  POST /public/waitlist   │
│   <LandingPage>         │  visitor │   waitlist_entry         │
│   <WaitlistForm>        │         │                          │
│                         │         │  GET  /admin/waitlist    │
│  /signin                │         │  POST /admin/waitlist/   │
│   <SignInPage>          ├────────▶│        :id/approve       │
│   ┌─Google─┐ ┌─MS────┐  │         │   sends invite email     │
│                         │         │   (via Resend)           │
│  /onboarding            │         │                          │
│   <OnboardingWizard>    ├────────▶│  PUT  /me/profile        │
│    Step 1..6            │         │   per-step save          │
│                         │         │  POST /me/wizard/finish  │
│                         │         │  POST /me/wizard/skip    │
│                         │         │                          │
│  /dashboard             │         │  GET  /me/profile        │
│   <UserMenu>            │         │  GET  /sectors           │
│   <Settings>            │         │  GET  /countries         │
└─────────────────────────┘         └──────────┬───────────────┘
                                               │
                                    ┌──────────▼──────────────┐
                                    │  Postgres (Neon)        │
                                    │   + 5 new tables        │
                                    │   + waitlist_entry      │
                                    │   + user_profile        │
                                    │   + user_sector         │
                                    │   + user_country        │
                                    │   + user_theme          │
                                    │   + (existing) app_user,│
                                    │     user_universe_*     │
                                    └─────────────────────────┘
```

## 3. User journeys

### 3.1 First-time visitor → approved → dashboard

1. Visitor lands on `/` → sees marketing copy + "Request Access" button
2. Clicks → fills waitlist form (name, email, role, firm, "why you'd use AlphaGraph")
3. Submits → `POST /public/waitlist` → row in `waitlist_entry`, status=`pending`
4. Sees "Thanks — we'll be in touch" page
5. (Sharon manually reviews queue at `/admin/waitlist`, clicks Approve)
6. Backend marks status=`approved`, sends invite email via Resend
7. User receives email with sign-in link
8. Clicks → lands on `/signin` → clicks "Sign in with Google"
9. OAuth → callback → backend checks `waitlist_entry.email` matches OAuth email → creates `app_user`
10. `user_profile.wizard_completed_at IS NULL` → frontend redirects to `/onboarding`
11. User completes 6-step wizard
12. Step 6 finish → `POST /me/wizard/finish` → backend auto-subscribes them to thesis groups based on sectors picked, sends founding-member invites
13. Lands on `/dashboard` with their personalized universe loaded

### 3.2 Self-serve attempt (not on waitlist)

1. User clicks Sign in with Google directly without invite
2. OAuth callback → email NOT in waitlist
3. Backend auto-creates `waitlist_entry` with status=`self_serve_attempt`, source=`oauth_callback`
4. Frontend shows "You're not on the access list yet — we'll review your interest" page
5. (Sharon sees them in the admin queue with `self_serve_attempt` tag — can promote to `approved` if good fit)

### 3.3 Founding-member referred user

1. Approved user X reaches Step 6 of wizard, enters peer Y's email
2. `POST /me/wizard/finish` includes `invitees: [{email: "y@..."}]`
3. Backend creates `waitlist_entry` for Y with status=`approved` (auto-approved because referred), `referred_by_user_id = X`
4. Sends invite email to Y
5. Y signs in → matches waitlist → no extra approval needed → enters wizard

### 3.4 Returning user

1. User signs in
2. Backend checks `user_profile.wizard_completed_at` and `wizard_skipped_at`
3. If complete → straight to dashboard
4. If mid-wizard → resume at `wizard_current_step`
5. If skipped → dashboard with **all thesis groups subscribed** (treat as if user picked Generalist with zero sectors). Settings page lets them refine later.

### 3.5 Generalist semantics

Generalist is **additive**, not exclusive. A user can pick:
- 0 sectors + Generalist  → dashboard shows all sectors equally weighted
- 1–3 sectors + Generalist → dashboard shows all sectors, with picked sectors sort-elevated in news/alerts/chat priors
- 1–3 sectors only        → dashboard limits to those sector groups
- Generalist only         → equivalent to "skipped" (all groups subscribed)

The "max 3 sectors" cap applies only to *non-Generalist* sector picks. Generalist is always available as an additional toggle.

## 4. Data model

### 4.1 New tables

```sql
-- Waitlist queue
CREATE TABLE waitlist_entry (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                    CITEXT UNIQUE NOT NULL,        -- case-insensitive uniqueness
    full_name                TEXT,
    self_reported_role       TEXT,
    self_reported_firm       TEXT,
    note                     TEXT,                          -- "why you'd use AlphaGraph"
    referrer                 TEXT,                          -- "how did you hear" optional
    referred_by_user_id      UUID REFERENCES app_user(id) ON DELETE SET NULL,
    status                   TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'rejected', 'self_serve_attempt')),
    requested_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at              TIMESTAMPTZ,
    approved_by_user_id      UUID REFERENCES app_user(id) ON DELETE SET NULL,
    rejected_reason          TEXT,
    invite_email_sent_at     TIMESTAMPTZ,
    invite_email_clicked_at  TIMESTAMPTZ
);
CREATE INDEX idx_waitlist_status ON waitlist_entry(status, requested_at DESC);

-- User profile (extends app_user with wizard answers)
CREATE TABLE user_profile (
    user_id                  UUID PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    role                     TEXT,                          -- 'buyside_analyst' | 'buyside_pm' | 'sell_side' | 'wealth_manager' | 'other'
    role_other               TEXT,                          -- non-null when role='other'
    firm_strategy            TEXT,                          -- 'long_only' | 'long_short' | 'rel_value' | 'macro' | 'sell_side' | 'other'
    firm_strategy_other      TEXT,
    firm_name                TEXT,                          -- inferred from email domain or self-entered
    is_generalist            BOOLEAN NOT NULL DEFAULT false,
    wizard_current_step      INT NOT NULL DEFAULT 1
                             CHECK (wizard_current_step BETWEEN 1 AND 6),
    wizard_completed_at      TIMESTAMPTZ,
    wizard_skipped_at        TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- GICS sector catalogue (seeded; admin-editable later)
CREATE TABLE gics_sector (
    id                       TEXT PRIMARY KEY,              -- 'energy', 'semiconductors_eq', 'software_services', 'generalist', 'other'
    parent_sector_id         TEXT REFERENCES gics_sector(id),
    display_name             TEXT NOT NULL,
    is_industry_group        BOOLEAN NOT NULL DEFAULT false, -- true for the IT/Comm splits
    is_synthetic             BOOLEAN NOT NULL DEFAULT false, -- true for 'generalist' / 'other'
    sort_order               INT NOT NULL DEFAULT 0
);

-- User-selected sectors (max 3 enforced at app layer)
CREATE TABLE user_sector (
    user_id                  UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    sector_id                TEXT NOT NULL REFERENCES gics_sector(id),
    custom_label             TEXT,                          -- non-null when sector_id='other'
    selected_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, sector_id)
);

-- User-selected countries
CREATE TABLE user_country (
    user_id                  UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    country_code             TEXT NOT NULL,                 -- 'US' | 'EU' | 'JP' | 'KR' | 'CN' | 'HK' | 'TW' | 'IN' | 'AU' | 'OTHER'
    custom_label             TEXT,                          -- non-null when country_code='OTHER'
    selected_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, country_code)
);

-- User free-text themes, tagged to a sector
CREATE TABLE user_theme (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    sector_id                TEXT REFERENCES gics_sector(id), -- NULL = cross-sector / generalist
    theme_text               TEXT NOT NULL,
    sort_order               INT NOT NULL DEFAULT 0,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_user_theme ON user_theme(user_id, sector_id);
```

### 4.2 Extension to existing `app_user`

```sql
-- Admin role flag (just Sharon for v1)
ALTER TABLE app_user ADD COLUMN admin_role TEXT NOT NULL DEFAULT 'user'
    CHECK (admin_role IN ('user', 'admin'));
```

A future tier model (Tranche 2) will add `tier TEXT NOT NULL DEFAULT 'tier1_free'` to the same table; we leave it out now.

### 4.3 Seed data — GICS sectors

15 rows. IT/Comm split into industry groups, plus Generalist and Other:

| id | display_name | parent | is_industry_group | sort_order |
|---|---|---|---|---|
| `energy` | Energy | — | false | 100 |
| `materials` | Materials | — | false | 110 |
| `industrials` | Industrials | — | false | 120 |
| `consumer_discretionary` | Consumer Discretionary | — | false | 130 |
| `consumer_staples` | Consumer Staples | — | false | 140 |
| `health_care` | Health Care | — | false | 150 |
| `financials` | Financials | — | false | 160 |
| `semiconductors_eq` | Semiconductors & Equipment | `information_technology` | true | 170 |
| `tech_hardware_eq` | Tech Hardware & Equipment | `information_technology` | true | 180 |
| `software_services` | Software & Services | `information_technology` | true | 190 |
| `telecom_services` | Telecom Services | `communication_services` | true | 200 |
| `media_entertainment` | Media & Entertainment | `communication_services` | true | 210 |
| `utilities` | Utilities | — | false | 220 |
| `real_estate` | Real Estate | — | false | 230 |
| `generalist` | Generalist | — | false (synthetic) | 240 |
| `other` | Other | — | false (synthetic) | 250 |

Parent rows (`information_technology`, `communication_services`) exist in the catalogue but aren't selectable in the wizard — they group the industry-group children for future use (e.g., admin reports).

## 5. Backend API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/public/waitlist` | none | Submit waitlist application |
| GET | `/admin/waitlist?status=pending` | admin | Sharon's queue |
| POST | `/admin/waitlist/{id}/approve` | admin | Mark approved, send invite email |
| POST | `/admin/waitlist/{id}/reject` | admin | Mark rejected (optional reason) |
| GET | `/me/profile` | user | Current profile (used by `/onboarding` + `/settings`) |
| PUT | `/me/profile` | user | Per-step save during wizard |
| POST | `/me/wizard/finish` | user | Mark complete + side-effects |
| POST | `/me/wizard/skip` | user | Mark skipped |
| GET | `/me/onboarding-status` | user | Used by frontend router (where to redirect post-signin) |
| GET | `/sectors` | user | GICS catalogue |
| GET | `/countries` | user | Country catalogue |
| GET | `/me/sectors` `/me/countries` `/me/themes` | user | Read user selections |
| PUT | `/me/sectors` `/me/countries` `/me/themes` | user | Update from settings page |

### 5.1 Side-effects of `/me/wizard/finish`

1. Set `user_profile.wizard_completed_at = now()`.
2. **Subscribe to thesis groups** based on sector picks:
   - For each picked sector → look up which `universe_group`s map to it → INSERT into `user_universe_group` (auto-subscribe). Sector→group mapping seeded as static config (e.g. `software_services` → `ai_software_apps`, `ai_software_models`).
   - If `is_generalist=true` → INSERT into `user_universe_group` for every group in the system (all 31 thesis groups).
   - If neither sectors nor Generalist picked → no subscriptions (user must add via settings; the wizard requires at least one sector or Generalist to reach Continue, so this state is unreachable from the wizard but possible after edits).
3. For each invitee email → INSERT into `waitlist_entry` with `status='approved'`, `referred_by_user_id = current_user.id`, send invite email.
4. Award founding-member badge if 3+ invitees ever sign up (computed lazily, not at finish-time).

### 5.2 Side-effects of `/me/wizard/skip`

1. Set `user_profile.wizard_skipped_at = now()`.
2. **Subscribe to all 31 thesis groups** (Generalist default). Skipped users see the full dashboard with no preferences applied.
3. Set `is_generalist = true` so settings page reflects current state.

## 6. Frontend component tree

```
src/app/
├── (public)/                                   # unauthenticated routes
│   ├── page.tsx                                # <LandingPage>
│   ├── signin/page.tsx                         # <SignInPage>
│   └── waitlist/
│       ├── page.tsx                            # <WaitlistForm>
│       └── thanks/page.tsx                     # <WaitlistThanksPage>
├── (onboarding)/                               # post-signin, pre-wizard-complete
│   └── onboarding/
│       ├── page.tsx                            # <OnboardingWizard> (state machine)
│       ├── components/
│       │   ├── WizardShell.tsx                 # the card + progress bar + back/continue
│       │   ├── Step1Role.tsx
│       │   ├── Step2FirmStrategy.tsx
│       │   ├── Step3Sectors.tsx                # max-3 logic
│       │   ├── Step4Countries.tsx
│       │   ├── Step5Themes.tsx                 # per-sector inputs
│       │   └── Step6Invite.tsx
│       └── hooks/
│           ├── useOnboardingState.ts           # Zustand or React-context state
│           └── useWizardSave.ts                # debounced PUT /me/profile per step
├── (dashboard)/                                # authenticated, post-onboarding
│   ├── layout.tsx                              # <UserMenu> top-right
│   └── settings/
│       └── profile/page.tsx                    # edit role/sectors/countries/themes after wizard
└── (admin)/                                    # admin_role='admin' only
    └── waitlist/page.tsx                       # <AdminWaitlistQueue>
```

### 6.1 Visual design — Linear-light spec

| Token | Value |
|---|---|
| Background (page) | `#f6f7f9` |
| Background (card) | `#ffffff` |
| Border (default) | `#e5e7eb` (slate-200) |
| Border (hairline) | `#f1f5f9` (slate-100) |
| Border-dashed | `#cbd5e1` (slate-300) |
| Text (primary) | `#0f172a` (slate-900) |
| Text (secondary) | `#475569` (slate-600) |
| Text (muted) | `#64748b` (slate-500) |
| Text (placeholder) | `#94a3b8` (slate-400) |
| Accent | `#5b6cff` (custom indigo) |
| Accent-fill (selected) | `#eef0ff` (indigo-50) |
| Accent-text | `#3b46c0` (indigo-700) |
| Accent CTA bg | `#5b6cff` |
| Accent CTA shadow | `0 1px 2px rgba(91,108,255,0.2)` |
| Generalist accent | `#7c3aed` (purple-600) |
| Card shadow | `0 1px 2px rgba(15,23,42,0.04), 0 4px 12px rgba(15,23,42,0.04)` |
| Card border-radius | `10px` |
| Chip border-radius | `6px` |
| CTA border-radius | `6px` |
| Font (sans) | Inter, system-ui fallback |
| Font (mono) | "SF Mono", ui-monospace, monospace |
| Heading | `22px / 600 / -0.012em letter-spacing` |
| Body | `13px / 400 / 1.5 line-height` |
| Step counter | `11px / 500 / 0.04em letter-spacing / uppercase` |

Reused across: sign-in page, dashboard user menu, settings page, all wizard steps. This is the pilot's design system in miniature.

### 6.2 Wizard state management

`useOnboardingState` (Zustand store):

```typescript
{
  currentStep: 1..6,
  role: { value: string, customText?: string },
  firmStrategy: { value: string, customText?: string },
  sectors: Array<{ id: string, customLabel?: string }>,    // max 3 if !isGeneralist
  isGeneralist: boolean,
  countries: Array<{ code: string, customLabel?: string }>,
  themes: Array<{ sectorId: string | null, text: string }>, // null = cross-sector
  invites: Array<{ email: string, message?: string }>,
}
```

Per-step save: `useWizardSave` debounces 500ms after last change and `PUT /me/profile` with the partial state. On `Continue` click, force-flush the debounce. Step transition only after backend responds 200.

Recovery: on next sign-in, fetch `/me/profile` → hydrate Zustand → render at `wizard_current_step`.

## 7. Email infrastructure

**Provider: Resend** (recommended). Reasoning:
- Developer-friendly API (`resend.emails.send({...})`)
- Cheaper than Postmark at small scale (3k emails/mo free tier covers ~6 months of pilot growth)
- DKIM/SPF setup in dashboard takes ~20 min once
- Switchable to SES at scale (just an API endpoint change)

**Templates required:**

1. **`waitlist_received.html`** — to user, "Thanks for requesting access to AlphaGraph"
2. **`waitlist_approved.html`** — to user, "You're approved · click to sign in" (contains sign-in link to `https://alphagraph.com/signin`)
3. **`waitlist_referral_invite.html`** — to user invited via founding-member, "[X] invited you to AlphaGraph"
4. **`admin_new_waitlist_signup.html`** — to Sharon, "Someone applied" (one-line digest)

Templates live at `frontend/emails/*.tsx` using React Email (renders to HTML server-side via Resend SDK).

## 8. Authorization

For now, two roles: `user` and `admin`. Stored on `app_user.admin_role`.

Middleware (`require_admin`) on `/api/v1/admin/*` endpoints. Sharon's email is hardcoded in seed data as the only `admin_role='admin'` initially; future tier-2 design adds proper role assignment via UI.

`require_user` middleware (already exists in `backend/app/api/auth_deps.py`) is sufficient for `/me/*` endpoints.

## 9. Industry references — what we're benchmarking against

| Reference | What we borrow |
|---|---|
| Linear | Light-theme aesthetic, segmented progress, keyboard-first hints (`↵` on CTA), tight typography, square-ish chips, slate borders |
| Notion | Big bold heading, generous whitespace, single emoji per step (only on welcome page, not all 6), white card on light gray page |
| Granola | "Skip" prominently available, low-friction defaults, no required fields except first 3 steps |
| Figma | (Rejected for in-app onboarding — too consumer-grade. May reuse for marketing landing page.) |
| Vercel | Per-environment env-vars (Production/Preview/Development) for `NEXT_PUBLIC_API_URL`. Already done. |
| Stripe | Customer-portal pattern for future billing (Tranche 2, not in this spec) |

## 10. What this design EXCLUDES (deferred to later Tranches)

- ❌ Tier model (free / pro / VIP) and per-tier data access — Tranche 2
- ❌ Stripe billing integration — Tranche 2
- ❌ Workspace / team model and shared notes/projects — Tranche 3
- ❌ Theme universe with cross-references (themes ↔ companies / sectors / news) — Tranche 3
- ❌ Magic-link email auth as fallback — only if a pilot specifically can't OAuth
- ❌ Self-serve open signup — re-opens after pilot
- ❌ Domain-based team auto-detection — invites from same firm domain still go through normal OAuth, not auto-grouped
- ❌ Companies/coverage list in wizard — handled by future "onboarding agent" per user direction

## 11. Success criteria

- A pilot completes the full sign-up → wizard → dashboard journey in **under 60 seconds** on first try
- 100% of completed wizards result in a non-empty `user_universe_group` subscription (no zero-coverage users)
- Admin queue (Sharon's view) lists all waitlist applicants with status, role, firm, and "approve" / "reject" buttons within 1 click
- A founding-member invitee experiences zero manual approval delay between Sharon approving the inviter and the invitee being able to sign in
- Sign-in works on Chrome, Safari, Firefox; tested on macOS, Windows, iOS Safari, Android Chrome
- Wizard progress persists across browser refresh / sign-out and resumes at the correct step
- Hallucination-free: zero LLM calls in the wizard. All copy is static template text. (Mentioned because future Tranches add LLM work — this one stays deterministic.)

## 12. Open decisions at implementation time

1. **Strict-vs-lenient OAuth-email-vs-waitlist-email match.** When a user requests waitlist with `x@example.com` but signs in with Google `x@gmail.com`, do we reject (strict) or auto-add the gmail to waitlist + approve (lenient)? *Recommended: lenient.*
2. **Whether to use Resend vs Postmark vs SES.** *Recommended: Resend for v1 due to simpler setup; switchable later.*
3. **Where the landing page lives.** Same domain (`/`) or split (`www.alphagraph.com` for marketing, `app.alphagraph.com` for the app)? *Recommended: same domain for v1; split later when we have a real marketing page.*
4. **Wizard step navigation — keyboard shortcuts.** Number keys 1-9 to pick options? Tab + Enter? Arrow keys? *Recommended: Tab + Enter for accessibility minimum; number keys for power users on single-select steps (1, 2, 3, 4 visible in UI).*
5. **Founding-member referral reward delivery.** "Priority support + early access to all new features for life" — actual deliverable mechanism? *Recommended: founding-member badge column on `app_user`, surfaced in user menu; "early access" semantics defined when first feature gate exists.*
6. **Admin waitlist UI scope for v1.** Just a list view with approve/reject buttons, OR also bulk-actions / search / domain filter? *Recommended: list + buttons for v1; bulk actions when queue exceeds 50 entries.*

## 13. Migration path

1. **Migration `0006_user_onboarding`** — adds the 6 new tables, ALTER on `app_user`, seed data for `gics_sector`. **Requires `CREATE EXTENSION IF NOT EXISTS citext;`** at the start of the migration (used for case-insensitive email uniqueness on `waitlist_entry.email`).
2. **Sector → universe_group mapping config** — static JSON or Python dict in `backend/app/services/onboarding/sector_mapping.py` defining which `universe_group`s belong to each sector. Reviewed/adjusted at implementation time.
3. **Backend services + endpoints** — new `backend/app/services/onboarding/` module + new `backend/app/api/routers/v1/{onboarding,admin_waitlist,public_waitlist}.py`.
4. **Resend integration** — `backend/app/services/email/resend_client.py` with idempotent send (deduped by `waitlist_entry.id` + template_key).
5. **Frontend wizard** — new `(public)`, `(onboarding)`, `(admin)` route groups in Next.js app router.
6. **Settings → Profile page** — reuses wizard component primitives.
7. **Seed admin user** — Sharon's `app_user.admin_role` set to `admin` via one-time migration (or env-var driven seed).
8. **OAuth flow update** — auth callback routes through onboarding-status check before redirecting.

Migration order respects existing 0001–0005 (Phase 2 schema). The new `0006` introduces the citext extension and waitlist/profile/sector/country/theme tables. The `gics_sector` seed runs as part of the migration's `op.execute(INSERT ...)` for idempotence.

## 14. Out-of-band concerns we're aware of

- **Email deliverability** — Resend's free tier is fine for v1, but if a pilot's inbox spam-filters the invite email, the user is silently stuck. Mitigation: include a copy of the sign-in link directly in the admin queue UI so Sharon can DM it manually if needed.
- **Wizard abandonment** — if a user closes mid-wizard and never returns, they have a half-filled `user_profile` and no group subscriptions. The backend treats `wizard_completed_at IS NULL AND wizard_skipped_at IS NULL` as "incomplete"; the UI redirects to `/onboarding` on next sign-in. After 30 days incomplete, send a "still want in?" email (Tranche 2 nice-to-have).
- **Generalist + sector picks combination** — user can pick 3 sectors AND Generalist. Defined behavior: dashboard shows ALL sectors (because Generalist) but elevates the 3 picked sectors via sort order in news/alerts. Settings page lets them remove Generalist later.

## 15. References

- Brainstorming session: 2026-04-30 (visual companion served from `.superpowers/brainstorm/`)
- Existing schema: `backend/alembic/versions/0001..0005_*.py` — the foundation we're extending
- Existing OAuth implementation: `backend/app/services/auth/` + `backend/app/api/routers/v1/auth.py`
- Existing user state: `user_universe_group`, `user_universe_ticker` — wizard auto-subscribes through these
- Roadmap context: `roadmap_v1.md` — Tranche 1 sits in Stream 2 weeks 1–2 of Pillar A
