# Tranche 1 — User Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the invite-only sign-up flow + 6-step onboarding wizard for AlphaGraph, so approved buyside users can sign in via Google/Microsoft OAuth, complete a structured personalization wizard, and land in a dashboard with their thesis-group subscriptions auto-provisioned.

**Architecture:** Phase 2 schema extension (5 new tables: waitlist_entry, user_profile, gics_sector, user_sector, user_country, user_theme + ALTER app_user). FastAPI endpoints under `/api/v1/{public,admin,me}/*`. Next.js App Router with new `(public)`, `(onboarding)`, `(admin)` route groups. Resend for transactional email. Linear-light visual style with Tailwind. Wizard state via Zustand with per-step server save.

**Tech Stack:** SQLAlchemy 2 + Alembic (Phase2Base) · FastAPI · Pydantic v2 · Next.js 15 App Router · React 19 · Tailwind · Zustand · Resend · Postgres 16 (Neon) · pytest · Playwright (e2e)

**Spec reference:** `docs/superpowers/specs/2026-04-30-tranche1-user-onboarding-design.md`

---

## File map (decomposition lock-in)

### Backend — new files

```
backend/alembic/versions/
  0006_user_onboarding.py                       — migration: 5 tables + ALTER app_user + GICS seed + citext

backend/app/models/orm/
  waitlist_orm.py                               — WaitlistEntry
  user_profile_orm.py                           — UserProfile
  gics_sector_orm.py                            — GicsSector
  user_sector_orm.py                            — UserSector
  user_country_orm.py                           — UserCountry
  user_theme_orm.py                             — UserTheme

backend/app/services/onboarding/
  __init__.py
  sector_mapping.py                             — sector_id → universe_group_id[] static config
  finalize_wizard.py                            — side-effects of /me/wizard/finish
  waitlist_email.py                             — wraps email_service for waitlist-specific sends

backend/app/services/email/
  __init__.py
  resend_client.py                              — thin Resend SDK wrapper, idempotent send-by-id
  templates/
    waitlist_received.py                        — Python template (string interp) for v1
    waitlist_approved.py
    waitlist_referral_invite.py
    admin_new_waitlist_signup.py

backend/app/api/routers/v1/
  public_waitlist.py                            — POST /public/waitlist
  admin_waitlist.py                             — GET/POST /admin/waitlist + /approve + /reject
  onboarding.py                                 — /me/profile, /me/sectors, /me/countries, /me/themes,
                                                   /me/wizard/{finish,skip}, /me/onboarding-status,
                                                   /sectors, /countries

backend/tests/integration/onboarding/
  test_waitlist_public.py
  test_waitlist_admin.py
  test_profile.py
  test_wizard_finish.py
  test_oauth_callback_routing.py
  test_sector_mapping.py
```

### Backend — modified files

```
backend/app/db/phase2_session.py                — side-effect import the 6 new ORMs
backend/alembic/env.py                          — same
backend/main.py                                 — register 3 new routers
backend/app/api/routers/v1/auth.py              — OAuth callback consults waitlist + onboarding-status
backend/app/api/auth_deps.py                    — add `require_admin` dep
backend/app/models/orm/user_orm.py              — ALTER add admin_role to AppUser
backend/app/core/config.py                      — RESEND_API_KEY, EMAIL_FROM, ADMIN_EMAIL_BCC
backend/requirements.txt                        — add resend>=2.0.0
.env.production.example                         — document new env vars
```

### Frontend — new files

```
frontend/src/lib/onboarding/
  types.ts                                      — TypeScript types matching backend Pydantic models
  client.ts                                     — fetch wrappers for /me/profile, /me/wizard/*, etc.
  store.ts                                      — Zustand store (useOnboardingState)
  useWizardSave.ts                              — debounced save hook

frontend/src/components/wizard/
  WizardShell.tsx                               — card + progress bar + back/continue
  ProgressBar.tsx                               — segmented progress
  ChipSingleSelect.tsx                          — Step 1, 2 primitive
  ChipMultiSelect.tsx                           — Step 3, 4 primitive (with max-N support)
  ThemeInput.tsx                                — Step 5 per-sector free-text input
  InviteList.tsx                                — Step 6 email rows

frontend/src/app/(public)/
  layout.tsx                                    — public-route layout (no UserMenu)
  page.tsx                                      — landing page <LandingPage>
  signin/page.tsx                               — <SignInPage>
  waitlist/page.tsx                             — <WaitlistForm>
  waitlist/thanks/page.tsx                      — <WaitlistThanksPage>
  waitlist/access-pending/page.tsx              — for self-serve attempt rejected at OAuth callback

frontend/src/app/(onboarding)/
  layout.tsx                                    — minimal onboarding layout (no nav, just card)
  onboarding/page.tsx                           — <OnboardingWizard> orchestrator
  onboarding/components/
    Step1Role.tsx
    Step2FirmStrategy.tsx
    Step3Sectors.tsx
    Step4Countries.tsx
    Step5Themes.tsx
    Step6Invite.tsx

frontend/src/app/(dashboard)/
  settings/profile/page.tsx                     — edit role/sectors/countries/themes after wizard
  components/UserMenu.tsx                       — top-right user dropdown

frontend/src/app/(admin)/
  layout.tsx                                    — admin layout (admin_role check)
  admin/waitlist/page.tsx                       — <AdminWaitlistQueue>

frontend/src/styles/
  tokens.css                                    — Linear-light CSS variables (or extend tailwind.config.ts)

frontend/tests/e2e/
  onboarding.spec.ts                            — Playwright test of full wizard journey
```

### Frontend — modified files

```
frontend/tailwind.config.ts                     — add Linear-light tokens (indigo-500=#5b6cff)
frontend/src/app/(dashboard)/layout.tsx         — mount UserMenu in top-right
frontend/src/app/layout.tsx                     — root layout adjustments (auth context, redirect logic)
frontend/src/lib/api/base.ts                    — already has credentials:include; add /me/onboarding-status helper
frontend/src/middleware.ts                      — Next middleware to redirect unauthenticated users to /signin
                                                   and onboarding-incomplete to /onboarding
frontend/package.json                           — add zustand, @playwright/test, playwright
```

---

## Tasks

The plan has 30 tasks grouped into 10 phases. Each phase produces meaningful working software.

- **Phase A — Database foundation** (Tasks 1–4)
- **Phase B — Email service** (Tasks 5–6)
- **Phase C — Public + admin waitlist API** (Tasks 7–10)
- **Phase D — Profile + wizard API** (Tasks 11–14)
- **Phase E — OAuth callback integration** (Tasks 15–16)
- **Phase F — Frontend design system** (Tasks 17–18)
- **Phase G — Public pages + sign-in** (Tasks 19–21)
- **Phase H — Wizard frontend** (Tasks 22–26)
- **Phase I — Post-wizard surfaces** (Tasks 27–29)
- **Phase J — End-to-end smoke + finish** (Task 30)

---

## Phase A — Database foundation

### Task 1: ORM models for all 6 new tables

**Files:**
- Create: `backend/app/models/orm/waitlist_orm.py`
- Create: `backend/app/models/orm/user_profile_orm.py`
- Create: `backend/app/models/orm/gics_sector_orm.py`
- Create: `backend/app/models/orm/user_sector_orm.py`
- Create: `backend/app/models/orm/user_country_orm.py`
- Create: `backend/app/models/orm/user_theme_orm.py`
- Modify: `backend/app/models/orm/user_orm.py` (add `admin_role` column)
- Modify: `backend/app/db/phase2_session.py` (side-effect imports)
- Modify: `backend/alembic/env.py` (side-effect imports)

- [ ] **Step 1: Create `waitlist_orm.py` and `user_profile_orm.py`**

```python
# backend/app/models/orm/waitlist_orm.py
from __future__ import annotations
from sqlalchemy import (CheckConstraint, Column, DateTime, ForeignKey, Index,
                        String, Text, text)
from sqlalchemy.dialects.postgresql import UUID
import uuid
from backend.app.db.phase2_session import Phase2Base


class WaitlistEntry(Phase2Base):
    """Invite-only waitlist queue. Sharon manually approves; founding-member
    referrals auto-approve. The `email` column is CITEXT so case differences
    don't create duplicate rows (b@x.com == B@X.com)."""
    __tablename__ = "waitlist_entry"

    id                      = Column(UUID(as_uuid=True), primary_key=True,
                                     server_default=text("gen_random_uuid()"),
                                     default=uuid.uuid4)
    # CITEXT type isn't built into SA dialects; use String at the ORM layer
    # and rely on the DB CITEXT column type (set in the migration).
    email                   = Column(String(320), nullable=False, unique=True)
    full_name               = Column(String(255), nullable=True)
    self_reported_role      = Column(String(64),  nullable=True)
    self_reported_firm      = Column(String(255), nullable=True)
    note                    = Column(Text,        nullable=True)
    referrer                = Column(String(255), nullable=True)
    referred_by_user_id     = Column(UUID(as_uuid=True),
                                     ForeignKey("app_user.id", ondelete="SET NULL"),
                                     nullable=True)
    status                  = Column(String(32),  nullable=False,
                                     server_default=text("'pending'"))
    requested_at            = Column(DateTime(timezone=True), nullable=False,
                                     server_default=text("CURRENT_TIMESTAMP"))
    approved_at             = Column(DateTime(timezone=True), nullable=True)
    approved_by_user_id     = Column(UUID(as_uuid=True),
                                     ForeignKey("app_user.id", ondelete="SET NULL"),
                                     nullable=True)
    rejected_reason         = Column(Text, nullable=True)
    invite_email_sent_at    = Column(DateTime(timezone=True), nullable=True)
    invite_email_clicked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'self_serve_attempt')",
            name="ck_waitlist_status",
        ),
        Index("ix_waitlist_status_requested",
              "status", "requested_at"),
    )
```

```python
# backend/app/models/orm/user_profile_orm.py
from __future__ import annotations
from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey,
                        Integer, String, text)
from sqlalchemy.dialects.postgresql import UUID
from backend.app.db.phase2_session import Phase2Base


class UserProfile(Phase2Base):
    """Wizard answers + onboarding state. One-to-one with app_user."""
    __tablename__ = "user_profile"

    user_id              = Column(UUID(as_uuid=True),
                                  ForeignKey("app_user.id", ondelete="CASCADE"),
                                  primary_key=True)
    role                 = Column(String(32),  nullable=True)
    role_other           = Column(String(255), nullable=True)
    firm_strategy        = Column(String(32),  nullable=True)
    firm_strategy_other  = Column(String(255), nullable=True)
    firm_name            = Column(String(255), nullable=True)
    is_generalist        = Column(Boolean,     nullable=False,
                                  server_default=text("false"))
    wizard_current_step  = Column(Integer,     nullable=False,
                                  server_default=text("1"))
    wizard_completed_at  = Column(DateTime(timezone=True), nullable=True)
    wizard_skipped_at    = Column(DateTime(timezone=True), nullable=True)
    created_at           = Column(DateTime(timezone=True), nullable=False,
                                  server_default=text("CURRENT_TIMESTAMP"))
    updated_at           = Column(DateTime(timezone=True), nullable=False,
                                  server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("wizard_current_step BETWEEN 1 AND 6",
                        name="ck_user_profile_step"),
    )
```

- [ ] **Step 2: Create the 4 catalogue/relation ORMs**

```python
# backend/app/models/orm/gics_sector_orm.py
from __future__ import annotations
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, text
from backend.app.db.phase2_session import Phase2Base


class GicsSector(Phase2Base):
    __tablename__ = "gics_sector"

    id                = Column(String(64),  primary_key=True)
    parent_sector_id  = Column(String(64),
                               ForeignKey("gics_sector.id", ondelete="SET NULL"),
                               nullable=True)
    display_name      = Column(String(128), nullable=False)
    is_industry_group = Column(Boolean,     nullable=False, server_default=text("false"))
    is_synthetic      = Column(Boolean,     nullable=False, server_default=text("false"))
    sort_order        = Column(Integer,     nullable=False, server_default=text("0"))
```

```python
# backend/app/models/orm/user_sector_orm.py
from __future__ import annotations
from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from backend.app.db.phase2_session import Phase2Base


class UserSector(Phase2Base):
    __tablename__ = "user_sector"

    user_id      = Column(UUID(as_uuid=True),
                          ForeignKey("app_user.id", ondelete="CASCADE"),
                          primary_key=True)
    sector_id    = Column(String(64),
                          ForeignKey("gics_sector.id"),
                          primary_key=True)
    custom_label = Column(String(255), nullable=True)  # for sector_id='other'
    selected_at  = Column(DateTime(timezone=True), nullable=False,
                          server_default=text("CURRENT_TIMESTAMP"))
```

```python
# backend/app/models/orm/user_country_orm.py
from __future__ import annotations
from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from backend.app.db.phase2_session import Phase2Base


class UserCountry(Phase2Base):
    __tablename__ = "user_country"

    user_id      = Column(UUID(as_uuid=True),
                          ForeignKey("app_user.id", ondelete="CASCADE"),
                          primary_key=True)
    country_code = Column(String(8), primary_key=True)
    custom_label = Column(String(255), nullable=True)
    selected_at  = Column(DateTime(timezone=True), nullable=False,
                          server_default=text("CURRENT_TIMESTAMP"))
```

```python
# backend/app/models/orm/user_theme_orm.py
from __future__ import annotations
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
import uuid
from backend.app.db.phase2_session import Phase2Base


class UserTheme(Phase2Base):
    """Free-text themes typed by user, tagged to sector (NULL = cross-sector)."""
    __tablename__ = "user_theme"

    id         = Column(UUID(as_uuid=True), primary_key=True,
                        server_default=text("gen_random_uuid()"), default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True),
                        ForeignKey("app_user.id", ondelete="CASCADE"),
                        nullable=False)
    sector_id  = Column(String(64),
                        ForeignKey("gics_sector.id", ondelete="SET NULL"),
                        nullable=True)
    theme_text = Column(Text,    nullable=False)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("ix_user_theme_user_sector", "user_id", "sector_id"),
    )
```

- [ ] **Step 3: ALTER `AppUser` to add `admin_role`**

In `backend/app/models/orm/user_orm.py`, find the `AppUser` class and add this column near the top of the column list:

```python
    admin_role = Column(String(16), nullable=False, server_default=text("'user'"))
```

- [ ] **Step 4: Wire side-effect imports in `phase2_session.py` and `alembic/env.py`**

In `backend/app/db/phase2_session.py`, find the existing block of `from backend.app.models.orm import ...` lines and APPEND:

```python
from backend.app.models.orm import waitlist_orm        # noqa: F401, E402
from backend.app.models.orm import user_profile_orm    # noqa: F401, E402
from backend.app.models.orm import gics_sector_orm     # noqa: F401, E402
from backend.app.models.orm import user_sector_orm     # noqa: F401, E402
from backend.app.models.orm import user_country_orm    # noqa: F401, E402
from backend.app.models.orm import user_theme_orm      # noqa: F401, E402
```

In `backend/alembic/env.py`, find the existing block of `import backend.app.models.orm.<name>` lines and APPEND:

```python
import backend.app.models.orm.waitlist_orm         # noqa: F401, E402
import backend.app.models.orm.user_profile_orm     # noqa: F401, E402
import backend.app.models.orm.gics_sector_orm      # noqa: F401, E402
import backend.app.models.orm.user_sector_orm      # noqa: F401, E402
import backend.app.models.orm.user_country_orm     # noqa: F401, E402
import backend.app.models.orm.user_theme_orm       # noqa: F401, E402
```

- [ ] **Step 5: Verify imports work + commit**

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=. python -c "from backend.app.db.phase2_session import Phase2Base; print('tables:', sorted(Phase2Base.metadata.tables.keys()))"
```

Expected output includes: `gics_sector`, `user_country`, `user_profile`, `user_sector`, `user_theme`, `waitlist_entry` plus existing Phase 2 tables.

```bash
git add backend/app/models/orm/waitlist_orm.py backend/app/models/orm/user_profile_orm.py \
        backend/app/models/orm/gics_sector_orm.py backend/app/models/orm/user_sector_orm.py \
        backend/app/models/orm/user_country_orm.py backend/app/models/orm/user_theme_orm.py \
        backend/app/models/orm/user_orm.py backend/app/db/phase2_session.py backend/alembic/env.py
git commit -m "feat(onboarding): ORM models for waitlist + user_profile + GICS + sectors/countries/themes"
```

---

### Task 2: Alembic migration 0006 with citext + 6 tables + GICS seed

**Files:**
- Create: `backend/alembic/versions/0006_user_onboarding.py`

- [ ] **Step 1: Generate migration skeleton**

```bash
cd backend
POSTGRES_URI="postgresql+psycopg2://alphagraph:alphagraph_dev@localhost:5432/alphagraph" \
    alembic revision -m "user onboarding (waitlist + profile + GICS)"
```

Rename the generated file to `0006_user_onboarding.py` and replace its `revision = ...` line with `revision = "0006"`, `down_revision = "0005"`.

- [ ] **Step 2: Write the upgrade body — extension + 6 tables + ALTER**

Replace `def upgrade()` with:

```python
def upgrade() -> None:
    # citext = case-insensitive text. Used for waitlist_entry.email so
    # b@x.com == B@X.com on uniqueness.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # ---- ALTER app_user — add admin_role ----
    op.add_column(
        "app_user",
        sa.Column("admin_role", sa.String(16), nullable=False,
                  server_default=sa.text("'user'")),
    )
    op.create_check_constraint(
        "ck_app_user_admin_role",
        "app_user",
        "admin_role IN ('user', 'admin')",
    )

    # ---- waitlist_entry ----
    op.execute("""
        CREATE TABLE waitlist_entry (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email                    CITEXT UNIQUE NOT NULL,
            full_name                VARCHAR(255),
            self_reported_role       VARCHAR(64),
            self_reported_firm       VARCHAR(255),
            note                     TEXT,
            referrer                 VARCHAR(255),
            referred_by_user_id      UUID REFERENCES app_user(id) ON DELETE SET NULL,
            status                   VARCHAR(32) NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending','approved','rejected','self_serve_attempt')),
            requested_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_at              TIMESTAMPTZ,
            approved_by_user_id      UUID REFERENCES app_user(id) ON DELETE SET NULL,
            rejected_reason          TEXT,
            invite_email_sent_at     TIMESTAMPTZ,
            invite_email_clicked_at  TIMESTAMPTZ
        )
    """)
    op.create_index("ix_waitlist_status_requested",
                    "waitlist_entry", ["status", "requested_at"])

    # ---- gics_sector ----
    op.create_table(
        "gics_sector",
        sa.Column("id",                sa.String(64),  primary_key=True),
        sa.Column("parent_sector_id",  sa.String(64),
                  sa.ForeignKey("gics_sector.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("display_name",      sa.String(128), nullable=False),
        sa.Column("is_industry_group", sa.Boolean(),   nullable=False,
                  server_default=sa.text("false")),
        sa.Column("is_synthetic",      sa.Boolean(),   nullable=False,
                  server_default=sa.text("false")),
        sa.Column("sort_order",        sa.Integer(),   nullable=False,
                  server_default=sa.text("0")),
    )

    # ---- user_profile ----
    op.create_table(
        "user_profile",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("role",                 sa.String(32),  nullable=True),
        sa.Column("role_other",           sa.String(255), nullable=True),
        sa.Column("firm_strategy",        sa.String(32),  nullable=True),
        sa.Column("firm_strategy_other",  sa.String(255), nullable=True),
        sa.Column("firm_name",            sa.String(255), nullable=True),
        sa.Column("is_generalist",        sa.Boolean(),   nullable=False,
                  server_default=sa.text("false")),
        sa.Column("wizard_current_step",  sa.Integer(),   nullable=False,
                  server_default=sa.text("1")),
        sa.Column("wizard_completed_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("wizard_skipped_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("wizard_current_step BETWEEN 1 AND 6",
                           name="ck_user_profile_step"),
    )

    # ---- user_sector ----
    op.create_table(
        "user_sector",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("sector_id", sa.String(64),
                  sa.ForeignKey("gics_sector.id"), primary_key=True),
        sa.Column("custom_label", sa.String(255), nullable=True),
        sa.Column("selected_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    # ---- user_country ----
    op.create_table(
        "user_country",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("country_code", sa.String(8), primary_key=True),
        sa.Column("custom_label", sa.String(255), nullable=True),
        sa.Column("selected_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    # ---- user_theme ----
    op.create_table(
        "user_theme",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sector_id", sa.String(64),
                  sa.ForeignKey("gics_sector.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("theme_text", sa.Text(),    nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_user_theme_user_sector",
                    "user_theme", ["user_id", "sector_id"])

    # ---- Seed gics_sector (16 rows: 14 selectable + 2 parent groupings) ----
    op.execute("""
        INSERT INTO gics_sector (id, parent_sector_id, display_name, is_industry_group, is_synthetic, sort_order) VALUES
          ('information_technology',    NULL, 'Information Technology',     false, false, 0),
          ('communication_services',    NULL, 'Communication Services',     false, false, 0),
          ('energy',                    NULL, 'Energy',                     false, false, 100),
          ('materials',                 NULL, 'Materials',                  false, false, 110),
          ('industrials',               NULL, 'Industrials',                false, false, 120),
          ('consumer_discretionary',    NULL, 'Consumer Discretionary',     false, false, 130),
          ('consumer_staples',          NULL, 'Consumer Staples',           false, false, 140),
          ('health_care',               NULL, 'Health Care',                false, false, 150),
          ('financials',                NULL, 'Financials',                 false, false, 160),
          ('semiconductors_eq',         'information_technology', 'Semiconductors & Equipment', true, false, 170),
          ('tech_hardware_eq',          'information_technology', 'Tech Hardware & Equipment',  true, false, 180),
          ('software_services',         'information_technology', 'Software & Services',        true, false, 190),
          ('telecom_services',          'communication_services', 'Telecom Services',           true, false, 200),
          ('media_entertainment',       'communication_services', 'Media & Entertainment',      true, false, 210),
          ('utilities',                 NULL, 'Utilities',                  false, false, 220),
          ('real_estate',               NULL, 'Real Estate',                false, false, 230),
          ('generalist',                NULL, 'Generalist',                 false, true,  240),
          ('other',                     NULL, 'Other',                      false, true,  250)
    """)


def downgrade() -> None:
    op.drop_index("ix_user_theme_user_sector", table_name="user_theme")
    op.drop_table("user_theme")
    op.drop_table("user_country")
    op.drop_table("user_sector")
    op.drop_table("user_profile")
    op.drop_table("gics_sector")
    op.drop_index("ix_waitlist_status_requested", table_name="waitlist_entry")
    op.execute("DROP TABLE waitlist_entry")
    op.drop_constraint("ck_app_user_admin_role", "app_user", type_="check")
    op.drop_column("app_user", "admin_role")
    # Don't drop the citext extension — other tables may still need it.
```

Add the missing import at top:
```python
from sqlalchemy.dialects import postgresql
```

- [ ] **Step 3: Apply migration to local Postgres**

```bash
cd backend
POSTGRES_URI="postgresql+psycopg2://alphagraph:alphagraph_dev@localhost:5432/alphagraph" \
    alembic upgrade head
```

Expected: `Running upgrade 0005 -> 0006, user onboarding (waitlist + profile + GICS)`.

- [ ] **Step 4: Verify schema + seed**

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=. python -c "
from dotenv import load_dotenv; load_dotenv('.env')
from backend.app.db.phase2_session import Phase2SessionLocal
from backend.app.models.orm.gics_sector_orm import GicsSector
db = Phase2SessionLocal()
rows = db.query(GicsSector).order_by(GicsSector.sort_order).all()
print(f'gics_sector rows: {len(rows)}')
for r in rows[:5]:
    print(f'  {r.id:30s} {r.display_name}')
"
```

Expected: `gics_sector rows: 18` (16 selectable + 2 parent groupings; you'll see 14 selectable + 2 synthetic in the wizard).

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0006_user_onboarding.py
git commit -m "feat(migration): 0006 user onboarding schema + GICS seed"
```

---

### Task 3: `require_admin` auth dependency

**Files:**
- Modify: `backend/app/api/auth_deps.py`
- Test: `backend/tests/integration/onboarding/__init__.py` + `test_admin_dep.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/onboarding/__init__.py — empty
# backend/tests/integration/onboarding/test_admin_dep.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app.api.auth_deps import require_admin


def test_require_admin_blocks_non_admin():
    """A user with admin_role='user' is rejected with 403 by require_admin."""
    from unittest.mock import MagicMock
    from backend.app.api.auth_deps import require_admin
    from fastapi import HTTPException

    fake_user = MagicMock(admin_role="user", id="abc")
    with pytest.raises(HTTPException) as exc:
        require_admin(current_user=fake_user)
    assert exc.value.status_code == 403


def test_require_admin_allows_admin():
    from unittest.mock import MagicMock
    fake_user = MagicMock(admin_role="admin", id="abc")
    result = require_admin(current_user=fake_user)
    assert result is fake_user
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_admin_dep.py -v
```

Expected: ImportError or "function not defined" because `require_admin` doesn't exist yet.

- [ ] **Step 3: Implement `require_admin`**

In `backend/app/api/auth_deps.py`, find the existing `require_user` function and add directly below it:

```python
def require_admin(current_user=Depends(require_user)):
    """403 unless the user has admin_role='admin'."""
    if getattr(current_user, "admin_role", "user") != "admin":
        raise HTTPException(status_code=403, detail="admin access required")
    return current_user
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_admin_dep.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/auth_deps.py backend/tests/integration/onboarding/
git commit -m "feat(auth): require_admin dep + tests"
```

---

### Task 4: Promote Sharon to admin

**Files:**
- Create: `backend/scripts/seed_admin_user.py`

This is a one-shot script Sharon runs once after migration 0006 is applied. It elevates her existing `app_user` row to `admin_role='admin'`. We don't bake this into the migration because Sharon's email may differ between environments (dev/prod).

- [ ] **Step 1: Create the script**

```python
# backend/scripts/seed_admin_user.py
"""
One-shot: promote a single user to admin_role='admin'.

Usage:
    PYTHONPATH=. python -m backend.scripts.seed_admin_user --email sharonyoutube1@gmail.com

The user must already exist in app_user (they signed in once via OAuth).
Idempotent: safe to re-run.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

from backend.app.db.phase2_session import Phase2SessionLocal
from backend.app.models.orm.user_orm import AppUser


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    args = p.parse_args()

    db = Phase2SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.email == args.email).first()
        if user is None:
            print(f"[ERROR] no app_user with email {args.email}", file=sys.stderr)
            print("Sign in with this email at least once before running this script.",
                  file=sys.stderr)
            return 2
        if user.admin_role == "admin":
            print(f"[ok] {args.email} is already admin")
            return 0
        user.admin_role = "admin"
        db.commit()
        print(f"[ok] promoted {args.email} -> admin_role='admin'")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run locally + verify**

```bash
cd /c/Users/Sharo/AI_projects/AlphaGraph_new && PYTHONIOENCODING=utf-8 PYTHONPATH=. python -m backend.scripts.seed_admin_user --email sharonyoutube1@gmail.com
```

Expected (assuming Sharon's user exists in local Postgres): `[ok] promoted sharonyoutube1@gmail.com -> admin_role='admin'`.

If user doesn't exist locally, that's fine — script exits clean. The real run will be against prod Render Postgres after the first OAuth sign-in.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/seed_admin_user.py
git commit -m "feat(admin): one-shot script to promote a user to admin role"
```

---

## Phase B — Email service

### Task 5: Resend client + config

**Files:**
- Create: `backend/app/services/email/__init__.py`
- Create: `backend/app/services/email/resend_client.py`
- Modify: `backend/app/core/config.py` (add 3 new settings)
- Modify: `backend/requirements.txt` (add `resend>=2.0.0`)
- Modify: `.env.production.example` (document new vars)
- Test: `backend/tests/integration/onboarding/test_resend_client.py`

- [ ] **Step 1: Add config settings**

In `backend/app/core/config.py`, find the existing `class Settings(BaseSettings):` and add (near `ANTHROPIC_API_KEY`):

```python
    # ---- Email (Resend) ----
    # Set RESEND_API_KEY for prod. Dev: leave blank → emails are logged not sent.
    RESEND_API_KEY: Optional[str] = None
    EMAIL_FROM: str = "AlphaGraph <noreply@alphagraph.com>"
    ADMIN_EMAIL_BCC: Optional[str] = None  # Sharon's email; BCC'd on every waitlist email
```

- [ ] **Step 2: Add resend to requirements**

In `backend/requirements.txt`, add:

```
resend>=2.0.0  # transactional email; used by waitlist + onboarding flow
```

Then locally:

```bash
pip install resend>=2.0.0
```

In `.env.production.example`, find the LLM keys section and append:

```bash
# ---- Email (Resend) ----
RESEND_API_KEY=re_...                # https://resend.com/api-keys
EMAIL_FROM=AlphaGraph <noreply@alphagraph.com>
ADMIN_EMAIL_BCC=sharonyoutube1@gmail.com
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/integration/onboarding/test_resend_client.py
from unittest.mock import patch, MagicMock
from backend.app.services.email.resend_client import send_email, EmailNotConfiguredError


def test_send_email_logs_when_no_api_key(caplog, monkeypatch):
    monkeypatch.setattr("backend.app.services.email.resend_client._api_key", lambda: None)
    import logging
    caplog.set_level(logging.INFO)
    result = send_email(
        to="recipient@example.com",
        subject="Hi",
        html="<p>Hello</p>",
    )
    assert result["status"] == "logged_not_sent"
    assert any("recipient@example.com" in r.message for r in caplog.records)


def test_send_email_calls_resend_when_configured(monkeypatch):
    fake_resend = MagicMock()
    fake_resend.Emails.send.return_value = {"id": "email-id-123"}
    monkeypatch.setattr("backend.app.services.email.resend_client._api_key", lambda: "re_test")
    monkeypatch.setattr("backend.app.services.email.resend_client._resend_module", lambda: fake_resend)
    result = send_email(
        to="recipient@example.com",
        subject="Hi",
        html="<p>Hello</p>",
        bcc="admin@example.com",
    )
    assert result["status"] == "sent"
    assert result["id"] == "email-id-123"
    fake_resend.Emails.send.assert_called_once()
    payload = fake_resend.Emails.send.call_args[0][0]
    assert payload["to"] == ["recipient@example.com"]
    assert payload["bcc"] == ["admin@example.com"]
    assert "Hello" in payload["html"]
```

- [ ] **Step 4: Implement `resend_client.py`**

```python
# backend/app/services/email/__init__.py — empty
```

```python
# backend/app/services/email/resend_client.py
"""
Thin Resend SDK wrapper. Two failure modes handled:
  - Missing RESEND_API_KEY → log the email; useful in dev / tests.
  - Resend API error → propagate as RuntimeError; caller decides retry.
"""
from __future__ import annotations
import logging
from typing import Optional
from backend.app.core.config import settings

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(RuntimeError):
    pass


def _api_key() -> Optional[str]:
    """Indirection so tests can monkeypatch."""
    return settings.RESEND_API_KEY


def _resend_module():
    """Lazy import so dev environments without resend installed don't crash."""
    import resend
    return resend


def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    bcc: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> dict:
    """Send an email via Resend. If RESEND_API_KEY is unset, log the
    payload and return status='logged_not_sent' (useful in dev)."""
    if not _api_key():
        logger.info(
            "[EMAIL not sent — RESEND_API_KEY missing] to=%s subject=%r html_len=%d",
            to, subject, len(html),
        )
        return {"status": "logged_not_sent"}

    resend = _resend_module()
    resend.api_key = _api_key()

    payload = {
        "from": settings.EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if bcc or settings.ADMIN_EMAIL_BCC:
        payload["bcc"] = [bcc or settings.ADMIN_EMAIL_BCC]
    if reply_to:
        payload["reply_to"] = reply_to

    response = resend.Emails.send(payload)
    return {"status": "sent", "id": response.get("id")}
```

- [ ] **Step 5: Run tests + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_resend_client.py -v
```

Expected: 2 passed.

```bash
git add backend/app/services/email/ backend/app/core/config.py backend/requirements.txt \
        .env.production.example backend/tests/integration/onboarding/test_resend_client.py
git commit -m "feat(email): Resend client + config + dev fallback (log instead of send)"
```

---

### Task 6: Email templates (4 templates as Python functions)

**Files:**
- Create: `backend/app/services/email/templates/__init__.py`
- Create: `backend/app/services/email/templates/waitlist_received.py`
- Create: `backend/app/services/email/templates/waitlist_approved.py`
- Create: `backend/app/services/email/templates/waitlist_referral_invite.py`
- Create: `backend/app/services/email/templates/admin_new_waitlist_signup.py`
- Test: `backend/tests/integration/onboarding/test_email_templates.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/onboarding/test_email_templates.py
from backend.app.services.email.templates.waitlist_received import render_waitlist_received
from backend.app.services.email.templates.waitlist_approved import render_waitlist_approved
from backend.app.services.email.templates.waitlist_referral_invite import render_waitlist_referral_invite
from backend.app.services.email.templates.admin_new_waitlist_signup import render_admin_new_waitlist_signup


def test_waitlist_received():
    out = render_waitlist_received(full_name="Alice")
    assert "Alice" in out["html"]
    assert "thank" in out["subject"].lower() or "received" in out["subject"].lower()


def test_waitlist_approved():
    out = render_waitlist_approved(full_name="Bob", signin_url="https://alphagraph.com/signin")
    assert "Bob" in out["html"]
    assert "https://alphagraph.com/signin" in out["html"]


def test_waitlist_referral_invite():
    out = render_waitlist_referral_invite(
        invitee_name="Carol",
        inviter_name="Alice",
        inviter_message="Thought you'd like this.",
        signin_url="https://alphagraph.com/signin",
    )
    assert "Alice" in out["html"]
    assert "Thought you'd like this." in out["html"]


def test_admin_new_waitlist_signup():
    out = render_admin_new_waitlist_signup(
        applicant_email="newuser@example.com",
        applicant_name="Dan",
        role="Buyside Analyst",
        firm="Acme Capital",
    )
    assert "newuser@example.com" in out["html"]
    assert "Dan" in out["html"]
    assert "/admin/waitlist" in out["html"]
```

- [ ] **Step 2: Run test to fail**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_email_templates.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the 4 templates**

```python
# backend/app/services/email/templates/__init__.py — empty
```

```python
# backend/app/services/email/templates/waitlist_received.py
def render_waitlist_received(*, full_name: str | None) -> dict:
    name = full_name or "there"
    subject = "AlphaGraph — application received"
    html = f"""
<!doctype html><html><body style="font-family:-apple-system,Inter,sans-serif;color:#0f172a;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="margin:0 0 8px 0">Thanks, {name}.</h2>
  <p>We've received your AlphaGraph access request. Our team reviews each application personally — usually within 1 business day.</p>
  <p>You'll get a follow-up email once you're approved. In the meantime, follow <a href="https://alphagraph.com">@alphagraph_ai</a> for product updates.</p>
  <p style="color:#64748b;font-size:13px;margin-top:24px">— AlphaGraph team</p>
</body></html>
"""
    return {"subject": subject, "html": html.strip()}
```

```python
# backend/app/services/email/templates/waitlist_approved.py
def render_waitlist_approved(*, full_name: str | None, signin_url: str) -> dict:
    name = full_name or "there"
    subject = "You're approved for AlphaGraph"
    html = f"""
<!doctype html><html><body style="font-family:-apple-system,Inter,sans-serif;color:#0f172a;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="margin:0 0 8px 0">Welcome, {name}.</h2>
  <p>You're approved. Click below to sign in with the Google or Microsoft account you used to apply.</p>
  <p style="margin:24px 0">
    <a href="{signin_url}" style="background:#5b6cff;color:#fff;padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block">Sign in to AlphaGraph</a>
  </p>
  <p style="color:#64748b;font-size:13px">First sign-in takes you through a quick 6-step setup so we can tailor your dashboard. About 60 seconds.</p>
  <p style="color:#94a3b8;font-size:12px;margin-top:24px">If the button doesn't work, copy this link: <code>{signin_url}</code></p>
</body></html>
"""
    return {"subject": subject, "html": html.strip()}
```

```python
# backend/app/services/email/templates/waitlist_referral_invite.py
def render_waitlist_referral_invite(
    *,
    invitee_name: str | None,
    inviter_name: str,
    inviter_message: str | None,
    signin_url: str,
) -> dict:
    name = invitee_name or "there"
    msg_block = ""
    if inviter_message:
        msg_block = f"""
  <blockquote style="border-left:3px solid #e5e7eb;margin:12px 0;padding:6px 16px;color:#475569;font-style:italic">
    {inviter_message}
  </blockquote>
"""
    subject = f"{inviter_name} invited you to AlphaGraph"
    html = f"""
<!doctype html><html><body style="font-family:-apple-system,Inter,sans-serif;color:#0f172a;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="margin:0 0 8px 0">{inviter_name} invited you, {name}.</h2>
  <p>You can skip the waitlist — sign in directly with the email this invitation was sent to.</p>
{msg_block}
  <p style="margin:24px 0">
    <a href="{signin_url}" style="background:#5b6cff;color:#fff;padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:500;display:inline-block">Sign in to AlphaGraph</a>
  </p>
  <p style="color:#64748b;font-size:13px">AlphaGraph is the AI-bottleneck research platform for buyside analysts. Trustworthy fundamentals + multilingual transcripts + zero-hallucination chat.</p>
</body></html>
"""
    return {"subject": subject, "html": html.strip()}
```

```python
# backend/app/services/email/templates/admin_new_waitlist_signup.py
def render_admin_new_waitlist_signup(
    *,
    applicant_email: str,
    applicant_name: str | None,
    role: str | None,
    firm: str | None,
) -> dict:
    name = applicant_name or "(no name given)"
    role_str = role or "(no role)"
    firm_str = firm or "(no firm)"
    subject = f"[AlphaGraph waitlist] {applicant_email}"
    html = f"""
<!doctype html><html><body style="font-family:-apple-system,Inter,sans-serif;color:#0f172a;max-width:560px;margin:0 auto;padding:24px">
  <h3 style="margin:0 0 8px 0">New waitlist application</h3>
  <table style="font-size:13px;border-collapse:collapse">
    <tr><td style="padding:4px 12px 4px 0;color:#64748b">Name</td><td>{name}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#64748b">Email</td><td><code>{applicant_email}</code></td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#64748b">Role</td><td>{role_str}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#64748b">Firm</td><td>{firm_str}</td></tr>
  </table>
  <p style="margin-top:18px"><a href="https://alphagraph.com/admin/waitlist">Review queue →</a></p>
</body></html>
"""
    return {"subject": subject, "html": html.strip()}
```

- [ ] **Step 4: Run tests + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_email_templates.py -v
```

Expected: 4 passed.

```bash
git add backend/app/services/email/templates/ backend/tests/integration/onboarding/test_email_templates.py
git commit -m "feat(email): 4 transactional templates (waitlist received/approved/referral/admin notify)"
```

---

## Phase C — Public + admin waitlist API

### Task 7: `POST /public/waitlist` endpoint

**Files:**
- Create: `backend/app/api/routers/v1/public_waitlist.py`
- Modify: `backend/main.py` (register router)
- Test: `backend/tests/integration/onboarding/test_waitlist_public.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/integration/onboarding/test_waitlist_public.py
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_post_public_waitlist_creates_pending_entry():
    payload = {
        "email": "newperson@example.com",
        "full_name": "New Person",
        "self_reported_role": "Buyside Analyst",
        "self_reported_firm": "Example Capital",
        "note": "Coverage AI infra; want trustworthy fundamentals.",
    }
    r = client.post("/api/v1/public/waitlist", json=payload)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "pending"
    assert data["email"] == "newperson@example.com"


def test_post_public_waitlist_idempotent_on_duplicate_email():
    """Submitting same email twice returns 200 (not error) with existing status."""
    payload = {"email": "dup@example.com", "full_name": "Dup"}
    r1 = client.post("/api/v1/public/waitlist", json=payload)
    r2 = client.post("/api/v1/public/waitlist", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 200  # already exists
    assert r2.json()["status"] == "pending"


def test_post_public_waitlist_rejects_invalid_email():
    r = client.post("/api/v1/public/waitlist", json={"email": "not-an-email"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to fail**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_waitlist_public.py -v
```

Expected: 404 (route not registered) or import error.

- [ ] **Step 3: Implement the router**

```python
# backend/app/api/routers/v1/public_waitlist.py
"""
Public waitlist endpoint — no auth required. Anyone can apply for access.
Idempotent on email: re-submitting an existing email returns the existing status.
"""
from __future__ import annotations
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.waitlist_orm import WaitlistEntry
from backend.app.services.email.resend_client import send_email
from backend.app.services.email.templates.waitlist_received import render_waitlist_received
from backend.app.services.email.templates.admin_new_waitlist_signup import render_admin_new_waitlist_signup

router = APIRouter()
logger = logging.getLogger(__name__)


class WaitlistApplyIn(BaseModel):
    email:               EmailStr
    full_name:           Optional[str] = None
    self_reported_role:  Optional[str] = None
    self_reported_firm:  Optional[str] = None
    note:                Optional[str] = None
    referrer:            Optional[str] = None


class WaitlistApplyOut(BaseModel):
    email: str
    status: str


@router.post("", response_model=WaitlistApplyOut, status_code=201)
def apply_to_waitlist(
    payload: WaitlistApplyIn,
    response: "fastapi.Response",  # noqa: F821 — quoted for Pydantic forward ref
    db: Session = Depends(get_phase2_session),
):
    existing = (
        db.query(WaitlistEntry)
          .filter(WaitlistEntry.email == payload.email)
          .first()
    )
    if existing:
        # Idempotent: don't duplicate-create or error.
        response.status_code = 200
        return WaitlistApplyOut(email=existing.email, status=existing.status)

    entry = WaitlistEntry(
        email              = payload.email,
        full_name          = payload.full_name,
        self_reported_role = payload.self_reported_role,
        self_reported_firm = payload.self_reported_firm,
        note               = payload.note,
        referrer           = payload.referrer,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # Send confirmation to applicant + notify Sharon. Best-effort; don't fail
    # the request if email delivery has an issue (waitlist write is the source of truth).
    try:
        confirmation = render_waitlist_received(full_name=payload.full_name)
        send_email(to=payload.email, subject=confirmation["subject"], html=confirmation["html"])
        admin = render_admin_new_waitlist_signup(
            applicant_email=payload.email,
            applicant_name=payload.full_name,
            role=payload.self_reported_role,
            firm=payload.self_reported_firm,
        )
        from backend.app.core.config import settings
        if settings.ADMIN_EMAIL_BCC:
            send_email(to=settings.ADMIN_EMAIL_BCC, subject=admin["subject"], html=admin["html"])
    except Exception as e:  # noqa: BLE001
        logger.warning("waitlist email send failed (non-fatal): %s", e)

    return WaitlistApplyOut(email=entry.email, status=entry.status)
```

Need to fix the `Response` reference. Replace the function signature header with:

```python
from fastapi import APIRouter, Depends, HTTPException, Response

@router.post("", response_model=WaitlistApplyOut, status_code=201)
def apply_to_waitlist(
    payload: WaitlistApplyIn,
    response: Response,
    db: Session = Depends(get_phase2_session),
):
```

- [ ] **Step 4: Register router in `main.py`**

In `backend/main.py`, find the existing block of `from backend.app.api.routers.v1 import ...` and add `public_waitlist`. Then near the existing `app.include_router(...)` calls add:

```python
app.include_router(public_waitlist.router,
                   prefix=f"{settings.API_V1_STR}/public/waitlist",
                   tags=["public-waitlist"])
```

- [ ] **Step 5: Run tests + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_waitlist_public.py -v
```

Expected: 3 passed.

```bash
git add backend/app/api/routers/v1/public_waitlist.py backend/main.py \
        backend/tests/integration/onboarding/test_waitlist_public.py
git commit -m "feat(api): POST /public/waitlist with idempotent email + best-effort notify"
```

---

### Task 8: `GET /admin/waitlist` (queue list)

**Files:**
- Create: `backend/app/api/routers/v1/admin_waitlist.py`
- Modify: `backend/main.py` (register router)
- Test: `backend/tests/integration/onboarding/test_waitlist_admin.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/integration/onboarding/test_waitlist_admin.py
from fastapi.testclient import TestClient
from unittest.mock import patch
from backend.main import app


def _admin_user(db, email="admin@example.com"):
    """Create + return an admin AppUser. Tests run against a real Postgres."""
    from backend.app.models.orm.user_orm import AppUser
    user = AppUser(email=email, admin_role="admin")
    db.add(user); db.commit(); db.refresh(user)
    return user


def test_get_admin_waitlist_requires_admin(client_user_session):
    """A signed-in non-admin user gets 403."""
    r = client_user_session.get("/api/v1/admin/waitlist")
    assert r.status_code == 403


def test_get_admin_waitlist_lists_pending(client_admin_session, seeded_waitlist_entries):
    """Admin sees pending entries in newest-first order."""
    r = client_admin_session.get("/api/v1/admin/waitlist?status=pending")
    assert r.status_code == 200
    data = r.json()
    assert "entries" in data
    assert all(e["status"] == "pending" for e in data["entries"])
```

Note: this test uses fixtures (`client_admin_session`, `client_user_session`, `seeded_waitlist_entries`) that don't exist yet. Add them to `backend/tests/integration/onboarding/conftest.py`:

```python
# backend/tests/integration/onboarding/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from backend.main import app
from backend.app.db.phase2_session import Phase2SessionLocal, get_phase2_session


@pytest.fixture
def db_session() -> Session:
    s = Phase2SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def admin_user(db_session):
    from backend.app.models.orm.user_orm import AppUser
    u = AppUser(email="admin-test@example.com", admin_role="admin")
    db_session.add(u); db_session.commit(); db_session.refresh(u)
    yield u
    db_session.delete(u); db_session.commit()


@pytest.fixture
def regular_user(db_session):
    from backend.app.models.orm.user_orm import AppUser
    u = AppUser(email="regular-test@example.com", admin_role="user")
    db_session.add(u); db_session.commit(); db_session.refresh(u)
    yield u
    db_session.delete(u); db_session.commit()


@pytest.fixture
def client_admin_session(admin_user):
    """TestClient that authenticates as admin via dep override."""
    from backend.app.api.auth_deps import require_user
    app.dependency_overrides[require_user] = lambda: admin_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(require_user, None)


@pytest.fixture
def client_user_session(regular_user):
    from backend.app.api.auth_deps import require_user
    app.dependency_overrides[require_user] = lambda: regular_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(require_user, None)


@pytest.fixture
def seeded_waitlist_entries(db_session):
    from backend.app.models.orm.waitlist_orm import WaitlistEntry
    entries = [
        WaitlistEntry(email=f"applicant{i}@example.com", full_name=f"App {i}", status="pending")
        for i in range(3)
    ]
    for e in entries: db_session.add(e)
    db_session.commit()
    yield entries
    for e in entries:
        db_session.delete(e)
    db_session.commit()
```

- [ ] **Step 2: Run test to fail**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_waitlist_admin.py::test_get_admin_waitlist_requires_admin -v
```

Expected: 404.

- [ ] **Step 3: Implement admin router (list endpoint only for now)**

```python
# backend/app/api/routers/v1/admin_waitlist.py
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.api.auth_deps import require_admin
from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.waitlist_orm import WaitlistEntry

router = APIRouter()


class WaitlistEntryOut(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    self_reported_role: Optional[str] = None
    self_reported_firm: Optional[str] = None
    note: Optional[str] = None
    status: str
    requested_at: str
    referred_by_user_id: Optional[str] = None


class WaitlistListOut(BaseModel):
    entries: list[WaitlistEntryOut]
    total: int


@router.get("", response_model=WaitlistListOut)
def list_waitlist(
    status: Optional[str] = Query(default=None, regex="^(pending|approved|rejected|self_serve_attempt)$"),
    limit: int = Query(default=50, le=200),
    _admin = Depends(require_admin),
    db: Session = Depends(get_phase2_session),
):
    q = db.query(WaitlistEntry).order_by(WaitlistEntry.requested_at.desc())
    if status:
        q = q.filter(WaitlistEntry.status == status)
    total = q.count()
    rows = q.limit(limit).all()
    return WaitlistListOut(
        entries=[
            WaitlistEntryOut(
                id=str(r.id),
                email=r.email,
                full_name=r.full_name,
                self_reported_role=r.self_reported_role,
                self_reported_firm=r.self_reported_firm,
                note=r.note,
                status=r.status,
                requested_at=r.requested_at.isoformat(),
                referred_by_user_id=str(r.referred_by_user_id) if r.referred_by_user_id else None,
            )
            for r in rows
        ],
        total=total,
    )
```

Register in `main.py`:

```python
from backend.app.api.routers.v1 import admin_waitlist
app.include_router(admin_waitlist.router,
                   prefix=f"{settings.API_V1_STR}/admin/waitlist",
                   tags=["admin-waitlist"])
```

- [ ] **Step 4: Run tests + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_waitlist_admin.py -v
```

Expected: 2 passed.

```bash
git add backend/app/api/routers/v1/admin_waitlist.py backend/main.py \
        backend/tests/integration/onboarding/conftest.py \
        backend/tests/integration/onboarding/test_waitlist_admin.py
git commit -m "feat(api): GET /admin/waitlist (admin-gated list)"
```

---

### Task 9: `POST /admin/waitlist/{id}/approve` + `/reject`

**Files:**
- Modify: `backend/app/api/routers/v1/admin_waitlist.py`
- Test: extend `test_waitlist_admin.py`

- [ ] **Step 1: Write failing tests**

Append to `test_waitlist_admin.py`:

```python
def test_approve_marks_status_and_sends_email(client_admin_session, seeded_waitlist_entries):
    entry_id = str(seeded_waitlist_entries[0].id)
    r = client_admin_session.post(f"/api/v1/admin/waitlist/{entry_id}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


def test_reject_marks_status(client_admin_session, seeded_waitlist_entries):
    entry_id = str(seeded_waitlist_entries[0].id)
    r = client_admin_session.post(
        f"/api/v1/admin/waitlist/{entry_id}/reject",
        json={"reason": "Not aligned with ICP"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["rejected_reason"] == "Not aligned with ICP"


def test_approve_404_on_unknown_id(client_admin_session):
    fake = "00000000-0000-0000-0000-000000000000"
    r = client_admin_session.post(f"/api/v1/admin/waitlist/{fake}/approve")
    assert r.status_code == 404
```

- [ ] **Step 2: Implement approve + reject in `admin_waitlist.py`**

Append to `backend/app/api/routers/v1/admin_waitlist.py`:

```python
from datetime import datetime, timezone
from fastapi import HTTPException, Path
from pydantic import BaseModel
from backend.app.core.config import settings
from backend.app.services.email.resend_client import send_email
from backend.app.services.email.templates.waitlist_approved import render_waitlist_approved


class RejectIn(BaseModel):
    reason: Optional[str] = None


@router.post("/{entry_id}/approve", response_model=WaitlistEntryOut)
def approve_entry(
    entry_id: str = Path(...),
    admin = Depends(require_admin),
    db: Session = Depends(get_phase2_session),
):
    entry = db.query(WaitlistEntry).filter(WaitlistEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="not found")

    entry.status               = "approved"
    entry.approved_at          = datetime.now(timezone.utc)
    entry.approved_by_user_id  = admin.id
    entry.invite_email_sent_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(entry)

    # Send approval email (best-effort)
    try:
        signin_url = f"{settings.FRONTEND_URL.rstrip('/')}/signin"
        msg = render_waitlist_approved(full_name=entry.full_name, signin_url=signin_url)
        send_email(to=entry.email, subject=msg["subject"], html=msg["html"])
    except Exception as e:
        import logging; logging.getLogger(__name__).warning("approval email failed: %s", e)

    return WaitlistEntryOut(
        id=str(entry.id), email=entry.email, full_name=entry.full_name,
        self_reported_role=entry.self_reported_role,
        self_reported_firm=entry.self_reported_firm,
        note=entry.note, status=entry.status,
        requested_at=entry.requested_at.isoformat(),
        referred_by_user_id=str(entry.referred_by_user_id) if entry.referred_by_user_id else None,
    )


@router.post("/{entry_id}/reject", response_model=WaitlistEntryOut)
def reject_entry(
    payload: RejectIn,
    entry_id: str = Path(...),
    _admin = Depends(require_admin),
    db: Session = Depends(get_phase2_session),
):
    entry = db.query(WaitlistEntry).filter(WaitlistEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="not found")
    entry.status          = "rejected"
    entry.rejected_reason = payload.reason
    db.commit(); db.refresh(entry)
    out = WaitlistEntryOut(
        id=str(entry.id), email=entry.email, full_name=entry.full_name,
        self_reported_role=entry.self_reported_role,
        self_reported_firm=entry.self_reported_firm,
        note=entry.note, status=entry.status,
        requested_at=entry.requested_at.isoformat(),
        referred_by_user_id=str(entry.referred_by_user_id) if entry.referred_by_user_id else None,
    )
    # Add rejected_reason to response inline
    out_dict = out.model_dump()
    out_dict["rejected_reason"] = entry.rejected_reason
    return out_dict
```

Add `rejected_reason` to `WaitlistEntryOut`:

```python
class WaitlistEntryOut(BaseModel):
    # ... existing fields ...
    rejected_reason: Optional[str] = None
```

- [ ] **Step 3: Run tests + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_waitlist_admin.py -v
```

Expected: 5 passed.

```bash
git add backend/app/api/routers/v1/admin_waitlist.py backend/tests/integration/onboarding/test_waitlist_admin.py
git commit -m "feat(api): POST /admin/waitlist/:id/{approve,reject} + email send"
```

---

### Task 10: Frontend types + API client wrappers

**Files:**
- Create: `frontend/src/lib/onboarding/types.ts`
- Create: `frontend/src/lib/onboarding/client.ts`

This task is frontend-only setup; tests come at the wizard component level.

- [ ] **Step 1: Define TypeScript types matching backend Pydantic models**

```typescript
// frontend/src/lib/onboarding/types.ts
export type WaitlistStatus = "pending" | "approved" | "rejected" | "self_serve_attempt";

export interface WaitlistApplyIn {
  email: string;
  full_name?: string | null;
  self_reported_role?: string | null;
  self_reported_firm?: string | null;
  note?: string | null;
  referrer?: string | null;
}

export interface WaitlistApplyOut {
  email: string;
  status: WaitlistStatus;
}

export interface WaitlistEntry {
  id: string;
  email: string;
  full_name: string | null;
  self_reported_role: string | null;
  self_reported_firm: string | null;
  note: string | null;
  status: WaitlistStatus;
  requested_at: string;
  referred_by_user_id: string | null;
  rejected_reason?: string | null;
}

export type RoleId =
  | "buyside_analyst" | "buyside_pm" | "sell_side"
  | "wealth_manager" | "other";

export type FirmStrategyId =
  | "long_only" | "long_short" | "rel_value"
  | "macro" | "sell_side" | "other";

export interface UserProfile {
  user_id: string;
  role: RoleId | null;
  role_other: string | null;
  firm_strategy: FirmStrategyId | null;
  firm_strategy_other: string | null;
  firm_name: string | null;
  is_generalist: boolean;
  wizard_current_step: number;
  wizard_completed_at: string | null;
  wizard_skipped_at: string | null;
}

export interface SectorPick {
  sector_id: string;
  custom_label?: string;
}

export interface CountryPick {
  country_code: string;
  custom_label?: string;
}

export interface UserTheme {
  id?: string;
  sector_id: string | null; // null = cross-sector
  theme_text: string;
  sort_order: number;
}

export interface InviteIn {
  email: string;
  message?: string;
}

export interface OnboardingStatus {
  is_authenticated: boolean;
  has_profile: boolean;
  wizard_completed_at: string | null;
  wizard_skipped_at: string | null;
  wizard_current_step: number;
  next_route: "/dashboard" | "/onboarding" | "/signin";
}

export interface GicsSector {
  id: string;
  parent_sector_id: string | null;
  display_name: string;
  is_industry_group: boolean;
  is_synthetic: boolean;
  sort_order: number;
}
```

- [ ] **Step 2: Define API client wrappers**

```typescript
// frontend/src/lib/onboarding/client.ts
import { apiRequest } from "@/lib/api/base";
import type {
  WaitlistApplyIn, WaitlistApplyOut, WaitlistEntry,
  UserProfile, OnboardingStatus, SectorPick, CountryPick,
  UserTheme, InviteIn, GicsSector,
} from "./types";

export const onboardingClient = {
  // Public
  async submitWaitlist(payload: WaitlistApplyIn): Promise<WaitlistApplyOut> {
    return apiRequest<WaitlistApplyOut>("/public/waitlist", "POST", payload);
  },

  // Admin
  async listWaitlist(status?: string): Promise<{ entries: WaitlistEntry[]; total: number }> {
    const qs = status ? `?status=${status}` : "";
    return apiRequest(`/admin/waitlist${qs}`);
  },
  async approveEntry(id: string): Promise<WaitlistEntry> {
    return apiRequest(`/admin/waitlist/${id}/approve`, "POST");
  },
  async rejectEntry(id: string, reason?: string): Promise<WaitlistEntry> {
    return apiRequest(`/admin/waitlist/${id}/reject`, "POST", { reason });
  },

  // User onboarding
  async getOnboardingStatus(): Promise<OnboardingStatus> {
    return apiRequest("/me/onboarding-status");
  },
  async getProfile(): Promise<UserProfile> {
    return apiRequest("/me/profile");
  },
  async putProfile(patch: Partial<UserProfile>): Promise<UserProfile> {
    return apiRequest("/me/profile", "PUT", patch);
  },
  async finishWizard(payload: {
    sectors: SectorPick[];
    countries: CountryPick[];
    themes: UserTheme[];
    invitees: InviteIn[];
  }): Promise<{ status: "completed" }> {
    return apiRequest("/me/wizard/finish", "POST", payload);
  },
  async skipWizard(): Promise<{ status: "skipped" }> {
    return apiRequest("/me/wizard/skip", "POST");
  },

  // Catalogues
  async listSectors(): Promise<GicsSector[]> {
    return apiRequest("/sectors");
  },
  async listCountries(): Promise<{ code: string; display_name: string; flag_emoji?: string }[]> {
    return apiRequest("/countries");
  },
};
```

- [ ] **Step 3: TypeScript-check + commit**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors. (If existing project has unrelated errors, accept those — the new files should be clean.)

```bash
git add frontend/src/lib/onboarding/
git commit -m "feat(frontend): onboarding types + API client wrappers"
```

---

## Phase D — Profile + wizard API

### Task 11: Sector→universe-group mapping config

**Files:**
- Create: `backend/app/services/onboarding/__init__.py`
- Create: `backend/app/services/onboarding/sector_mapping.py`
- Test: `backend/tests/integration/onboarding/test_sector_mapping.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/integration/onboarding/test_sector_mapping.py
from backend.app.services.onboarding.sector_mapping import (
    sector_to_universe_groups, ALL_UNIVERSE_GROUPS,
)


def test_software_services_maps_to_software_groups():
    groups = sector_to_universe_groups("software_services")
    assert "ai_software_apps" in groups
    assert "ai_software_models" in groups


def test_semis_eq_maps_to_compute_layer():
    groups = sector_to_universe_groups("semiconductors_eq")
    assert "ai_compute_design" in groups
    assert "ai_compute_foundry" in groups
    assert "ai_compute_semi_cap_eq" in groups
    assert "ai_compute_hbm_memory" in groups


def test_unknown_sector_returns_empty():
    assert sector_to_universe_groups("doesnt_exist") == []


def test_all_universe_groups_is_complete():
    """ALL_UNIVERSE_GROUPS must include every group in the seed."""
    # at minimum the 31 thesis groups we have today
    assert len(ALL_UNIVERSE_GROUPS) >= 30
    assert "ai_compute_design" in ALL_UNIVERSE_GROUPS
    assert "ai_emerging_companies" in ALL_UNIVERSE_GROUPS
```

- [ ] **Step 2: Implement the mapping**

```python
# backend/app/services/onboarding/__init__.py — empty
```

```python
# backend/app/services/onboarding/sector_mapping.py
"""
Static mapping from GICS sector_id (Step 3 of wizard) to thesis universe_group_id(s).
When a user picks a sector, we auto-subscribe them to these groups.

This is intentionally explicit and easy to edit. If a sector should pull more
groups, edit this file. There's no DB-driven mapping table because the set is
small, stable, and benefits from being source-controlled.
"""
from __future__ import annotations
from typing import Dict, List

# Every thesis group that exists. Used when is_generalist=true OR when wizard is skipped.
ALL_UNIVERSE_GROUPS: List[str] = [
    # Compute
    "ai_compute_design", "ai_compute_foundry", "ai_compute_hbm_memory",
    "ai_compute_packaging", "ai_compute_semi_cap_eq", "ai_compute_eda_ip",
    # Infra
    "ai_infra_networking", "ai_infra_optical", "ai_infra_servers_oem",
    # Hosting
    "ai_hosting_hyperscalers", "ai_hosting_neoclouds", "ai_hosting_dc_reits",
    # Energy
    "ai_energy_utilities", "ai_energy_nuclear_smr",
    "ai_energy_grid_electric", "ai_energy_cooling_hvac",
    # Software
    "ai_software_models", "ai_software_apps",
    # China
    "cn_ai_internet", "cn_ai_consumer", "cn_ai_semi",
    # Japan
    "jp_ai_robotics", "jp_ai_components", "jp_consumer",
    # Industrial
    "industrial_dc_construction", "industrial_aerospace_def", "industrial_capgoods",
    # Materials
    "ai_materials_semi", "ai_materials_critical_minerals", "ai_materials_dc_build",
    # Emerging
    "ai_emerging_companies",
]


# Sector → list of thesis groups
_MAPPING: Dict[str, List[str]] = {
    # IT split into 3 industry groups — each maps to its own slice of universe
    "semiconductors_eq": [
        "ai_compute_design", "ai_compute_foundry", "ai_compute_hbm_memory",
        "ai_compute_packaging", "ai_compute_semi_cap_eq", "ai_compute_eda_ip",
        "ai_materials_semi",
    ],
    "tech_hardware_eq": [
        "ai_infra_networking", "ai_infra_optical", "ai_infra_servers_oem",
        "jp_ai_components",
    ],
    "software_services": [
        "ai_software_apps", "ai_software_models", "ai_hosting_hyperscalers",
    ],
    # Comm Services split
    "telecom_services": [
        "ai_infra_networking",  # telecom infra overlaps with networking
    ],
    "media_entertainment": [
        "ai_software_apps",  # streaming apps & content monetization sit here
    ],
    # Standard GICS sectors
    "energy": [
        "ai_energy_utilities", "ai_energy_nuclear_smr",
    ],
    "materials": [
        "ai_materials_semi", "ai_materials_critical_minerals", "ai_materials_dc_build",
    ],
    "industrials": [
        "industrial_dc_construction", "industrial_capgoods", "industrial_aerospace_def",
        "ai_energy_grid_electric", "ai_energy_cooling_hvac",
        "jp_ai_robotics",
    ],
    "consumer_discretionary": [
        "cn_ai_consumer", "jp_consumer",
    ],
    "consumer_staples": [],  # no thesis-group overlap; user can add manually
    "health_care": [],       # likewise
    "financials": [],
    "utilities": [
        "ai_energy_utilities", "ai_energy_nuclear_smr",
        "ai_energy_grid_electric", "ai_energy_cooling_hvac",
    ],
    "real_estate": [
        "ai_hosting_dc_reits",
    ],
}


def sector_to_universe_groups(sector_id: str) -> List[str]:
    """Returns the thesis groups associated with a GICS sector. Returns
    empty list for unknown sectors (don't crash, just no auto-subscription)."""
    return _MAPPING.get(sector_id, [])
```

- [ ] **Step 3: Run tests + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_sector_mapping.py -v
```

Expected: 4 passed.

```bash
git add backend/app/services/onboarding/ backend/tests/integration/onboarding/test_sector_mapping.py
git commit -m "feat(onboarding): static sector→universe-group mapping config"
```

---

### Task 12: `GET /me/profile` + `GET /me/onboarding-status` + `GET /sectors` + `GET /countries`

**Files:**
- Create: `backend/app/api/routers/v1/onboarding.py`
- Modify: `backend/main.py`
- Test: `backend/tests/integration/onboarding/test_profile.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/integration/onboarding/test_profile.py
def test_get_onboarding_status_unauth_returns_signin(client_user_session):
    """Even authenticated test users return next_route — let's check shape."""
    r = client_user_session.get("/api/v1/me/onboarding-status")
    assert r.status_code == 200
    body = r.json()
    assert "next_route" in body
    assert body["next_route"] in ("/dashboard", "/onboarding")


def test_get_profile_returns_default_for_new_user(client_user_session, regular_user):
    r = client_user_session.get("/api/v1/me/profile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wizard_current_step"] == 1
    assert body["wizard_completed_at"] is None


def test_get_sectors_returns_seed(client_user_session):
    r = client_user_session.get("/api/v1/sectors")
    assert r.status_code == 200
    sectors = r.json()
    ids = [s["id"] for s in sectors]
    assert "semiconductors_eq" in ids
    assert "generalist" in ids
    # Generalist + Other are synthetic
    assert any(s["id"] == "generalist" and s["is_synthetic"] for s in sectors)


def test_get_countries_returns_9_plus_other(client_user_session):
    r = client_user_session.get("/api/v1/countries")
    assert r.status_code == 200
    countries = r.json()
    codes = [c["code"] for c in countries]
    for expected in ["US", "EU", "JP", "KR", "CN", "HK", "TW", "IN", "AU", "OTHER"]:
        assert expected in codes, f"missing {expected}"
```

- [ ] **Step 2: Implement the router**

```python
# backend/app/api/routers/v1/onboarding.py
"""
Onboarding endpoints — /me/profile, /me/sectors, /me/countries, /me/themes,
/me/wizard/{finish,skip}, /me/onboarding-status, /sectors, /countries.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.api.auth_deps import require_user
from backend.app.db.phase2_session import get_phase2_session
from backend.app.models.orm.user_profile_orm import UserProfile
from backend.app.models.orm.user_orm import AppUser
from backend.app.models.orm.gics_sector_orm import GicsSector
from backend.app.models.orm.user_sector_orm import UserSector
from backend.app.models.orm.user_country_orm import UserCountry
from backend.app.models.orm.user_theme_orm import UserTheme

router = APIRouter()


# ---- Schemas ---------------------------------------------------------------

class ProfileOut(BaseModel):
    user_id: str
    role: Optional[str] = None
    role_other: Optional[str] = None
    firm_strategy: Optional[str] = None
    firm_strategy_other: Optional[str] = None
    firm_name: Optional[str] = None
    is_generalist: bool
    wizard_current_step: int
    wizard_completed_at: Optional[str] = None
    wizard_skipped_at: Optional[str] = None


class ProfilePatchIn(BaseModel):
    role: Optional[str] = None
    role_other: Optional[str] = None
    firm_strategy: Optional[str] = None
    firm_strategy_other: Optional[str] = None
    firm_name: Optional[str] = None
    is_generalist: Optional[bool] = None
    wizard_current_step: Optional[int] = None


class OnboardingStatusOut(BaseModel):
    is_authenticated: bool
    has_profile: bool
    wizard_completed_at: Optional[str] = None
    wizard_skipped_at: Optional[str] = None
    wizard_current_step: int
    next_route: str


class GicsSectorOut(BaseModel):
    id: str
    parent_sector_id: Optional[str] = None
    display_name: str
    is_industry_group: bool
    is_synthetic: bool
    sort_order: int


class CountryOut(BaseModel):
    code: str
    display_name: str
    flag_emoji: Optional[str] = None


# ---- Helpers ---------------------------------------------------------------

def _get_or_create_profile(db: Session, user: AppUser) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile); db.commit(); db.refresh(profile)
    return profile


def _profile_to_out(p: UserProfile) -> ProfileOut:
    return ProfileOut(
        user_id=str(p.user_id),
        role=p.role, role_other=p.role_other,
        firm_strategy=p.firm_strategy, firm_strategy_other=p.firm_strategy_other,
        firm_name=p.firm_name,
        is_generalist=p.is_generalist,
        wizard_current_step=p.wizard_current_step,
        wizard_completed_at=p.wizard_completed_at.isoformat() if p.wizard_completed_at else None,
        wizard_skipped_at=p.wizard_skipped_at.isoformat() if p.wizard_skipped_at else None,
    )


# ---- /me/onboarding-status -------------------------------------------------

@router.get("/onboarding-status", response_model=OnboardingStatusOut)
def get_onboarding_status(
    user = Depends(require_user),
    db: Session = Depends(get_phase2_session),
):
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    if profile and profile.wizard_completed_at:
        next_route = "/dashboard"
    elif profile and profile.wizard_skipped_at:
        next_route = "/dashboard"
    else:
        next_route = "/onboarding"
    return OnboardingStatusOut(
        is_authenticated=True,
        has_profile=profile is not None,
        wizard_completed_at=profile.wizard_completed_at.isoformat() if profile and profile.wizard_completed_at else None,
        wizard_skipped_at=profile.wizard_skipped_at.isoformat() if profile and profile.wizard_skipped_at else None,
        wizard_current_step=profile.wizard_current_step if profile else 1,
        next_route=next_route,
    )


# ---- /me/profile -----------------------------------------------------------

@router.get("/profile", response_model=ProfileOut)
def get_profile(
    user = Depends(require_user),
    db: Session = Depends(get_phase2_session),
):
    profile = _get_or_create_profile(db, user)
    return _profile_to_out(profile)


# ---- catalogues -----------------------------------------------------------

@router.get("/sectors", response_model=list[GicsSectorOut])
def list_sectors(
    _user = Depends(require_user),
    db: Session = Depends(get_phase2_session),
):
    rows = db.query(GicsSector).order_by(GicsSector.sort_order).all()
    # Filter out the non-selectable parent rows (information_technology, communication_services)
    out = []
    for r in rows:
        if r.id in ("information_technology", "communication_services"):
            continue
        out.append(GicsSectorOut(
            id=r.id, parent_sector_id=r.parent_sector_id,
            display_name=r.display_name,
            is_industry_group=r.is_industry_group,
            is_synthetic=r.is_synthetic,
            sort_order=r.sort_order,
        ))
    return out


_COUNTRIES = [
    {"code": "US",    "display_name": "United States",          "flag_emoji": "🇺🇸"},
    {"code": "EU",    "display_name": "Europe (incl. UK)",      "flag_emoji": "🇪🇺"},
    {"code": "JP",    "display_name": "Japan",                  "flag_emoji": "🇯🇵"},
    {"code": "KR",    "display_name": "Korea",                  "flag_emoji": "🇰🇷"},
    {"code": "CN",    "display_name": "China (A-shares)",       "flag_emoji": "🇨🇳"},
    {"code": "HK",    "display_name": "Hong Kong",              "flag_emoji": "🇭🇰"},
    {"code": "TW",    "display_name": "Taiwan",                 "flag_emoji": "🇹🇼"},
    {"code": "IN",    "display_name": "India",                  "flag_emoji": "🇮🇳"},
    {"code": "AU",    "display_name": "Australia",              "flag_emoji": "🇦🇺"},
    {"code": "OTHER", "display_name": "Other",                  "flag_emoji": None},
]


@router.get("/countries", response_model=list[CountryOut])
def list_countries(_user = Depends(require_user)):
    return [CountryOut(**c) for c in _COUNTRIES]
```

- [ ] **Step 3: Register router in `main.py`**

In `backend/main.py`:

```python
from backend.app.api.routers.v1 import onboarding

app.include_router(onboarding.router, prefix=f"{settings.API_V1_STR}/me",
                   tags=["onboarding-me"])
# Catalogues are mounted at /api/v1 directly (not under /me)
app.include_router(onboarding.router, prefix=f"{settings.API_V1_STR}",
                   tags=["onboarding-catalogues"])
```

Note: this double-mount will conflict on `/me/sectors` etc. Better approach — split the router into `me_router` and `catalogue_router`. Refactor:

In `onboarding.py` change `router = APIRouter()` to:

```python
me_router         = APIRouter()
catalogue_router  = APIRouter()
```

Then change decorators on `/onboarding-status` and `/profile` to `@me_router.get(...)`, and on `/sectors` and `/countries` to `@catalogue_router.get(...)`.

In `main.py`:

```python
app.include_router(onboarding.me_router,        prefix=f"{settings.API_V1_STR}/me", tags=["onboarding-me"])
app.include_router(onboarding.catalogue_router, prefix=settings.API_V1_STR,         tags=["catalogues"])
```

- [ ] **Step 4: Run tests + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_profile.py -v
```

Expected: 4 passed.

```bash
git add backend/app/api/routers/v1/onboarding.py backend/main.py \
        backend/tests/integration/onboarding/test_profile.py
git commit -m "feat(api): /me/profile, /me/onboarding-status, /sectors, /countries"
```

---

### Task 13: `PUT /me/profile` (per-step save)

**Files:**
- Modify: `backend/app/api/routers/v1/onboarding.py`
- Test: extend `test_profile.py`

- [ ] **Step 1: Write failing test**

Append to `test_profile.py`:

```python
def test_put_profile_updates_role_and_step(client_user_session):
    r = client_user_session.put("/api/v1/me/profile", json={
        "role": "buyside_analyst",
        "wizard_current_step": 2,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "buyside_analyst"
    assert body["wizard_current_step"] == 2


def test_put_profile_partial_doesnt_clobber_other_fields(client_user_session):
    # First save
    client_user_session.put("/api/v1/me/profile", json={
        "role": "buyside_analyst",
        "firm_strategy": "long_short",
        "wizard_current_step": 3,
    })
    # Then update only step
    r = client_user_session.put("/api/v1/me/profile", json={"wizard_current_step": 4})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "buyside_analyst"  # preserved
    assert body["firm_strategy"] == "long_short"  # preserved
    assert body["wizard_current_step"] == 4
```

- [ ] **Step 2: Implement PUT in `onboarding.py`**

Add to `me_router`:

```python
@me_router.put("/profile", response_model=ProfileOut)
def put_profile(
    patch: ProfilePatchIn,
    user = Depends(require_user),
    db: Session = Depends(get_phase2_session),
):
    profile = _get_or_create_profile(db, user)
    # Only set fields that are present (not None) in patch
    data = patch.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(profile, k, v)
    profile.updated_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(profile)
    return _profile_to_out(profile)
```

- [ ] **Step 3: Run + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_profile.py -v
```

Expected: 6 passed.

```bash
git add backend/app/api/routers/v1/onboarding.py backend/tests/integration/onboarding/test_profile.py
git commit -m "feat(api): PUT /me/profile per-step partial save"
```

---

### Task 14: `POST /me/wizard/finish` (with side-effects) + `POST /me/wizard/skip`

**Files:**
- Modify: `backend/app/api/routers/v1/onboarding.py`
- Create: `backend/app/services/onboarding/finalize_wizard.py`
- Test: `backend/tests/integration/onboarding/test_wizard_finish.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/integration/onboarding/test_wizard_finish.py
def test_finish_subscribes_to_thesis_groups(client_user_session, regular_user, db_session):
    payload = {
        "sectors": [{"sector_id": "software_services"}],
        "countries": [{"country_code": "US"}],
        "themes": [{"sector_id": "software_services", "theme_text": "AI pricing power", "sort_order": 0}],
        "invitees": [],
    }
    r = client_user_session.post("/api/v1/me/wizard/finish", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"

    # Verify auto-subscription: software_services maps to ai_software_apps + ai_software_models + ai_hosting_hyperscalers
    from backend.app.models.orm.universe_v2_orm import UserUniverseGroup
    subs = db_session.query(UserUniverseGroup).filter(
        UserUniverseGroup.user_id == regular_user.id
    ).all()
    group_ids = {s.group_id for s in subs}
    assert "ai_software_apps" in group_ids
    assert "ai_software_models" in group_ids


def test_finish_with_generalist_subscribes_all_groups(client_user_session, regular_user, db_session):
    payload = {
        "sectors": [{"sector_id": "generalist"}],
        "countries": [{"country_code": "US"}],
        "themes": [],
        "invitees": [],
    }
    # First mark profile as generalist via PUT
    client_user_session.put("/api/v1/me/profile", json={"is_generalist": True})
    r = client_user_session.post("/api/v1/me/wizard/finish", json=payload)
    assert r.status_code == 200

    from backend.app.models.orm.universe_v2_orm import UserUniverseGroup
    subs = db_session.query(UserUniverseGroup).filter(
        UserUniverseGroup.user_id == regular_user.id
    ).count()
    assert subs >= 30  # all 31 thesis groups subscribed


def test_skip_subscribes_to_all_groups(client_user_session, regular_user, db_session):
    r = client_user_session.post("/api/v1/me/wizard/skip")
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"

    from backend.app.models.orm.universe_v2_orm import UserUniverseGroup
    subs = db_session.query(UserUniverseGroup).filter(
        UserUniverseGroup.user_id == regular_user.id
    ).count()
    assert subs >= 30


def test_finish_creates_referral_waitlist_entry(client_user_session, regular_user, db_session):
    payload = {
        "sectors": [{"sector_id": "software_services"}],
        "countries": [{"country_code": "US"}],
        "themes": [],
        "invitees": [{"email": "peer@example.com", "message": "thought you'd like this"}],
    }
    r = client_user_session.post("/api/v1/me/wizard/finish", json=payload)
    assert r.status_code == 200

    from backend.app.models.orm.waitlist_orm import WaitlistEntry
    invitee = db_session.query(WaitlistEntry).filter(WaitlistEntry.email == "peer@example.com").first()
    assert invitee is not None
    assert invitee.status == "approved"  # auto-approved via referral
    assert invitee.referred_by_user_id == regular_user.id
```

- [ ] **Step 2: Implement `finalize_wizard.py`**

```python
# backend/app/services/onboarding/finalize_wizard.py
"""
The side-effect logic of POST /me/wizard/finish:
  1. mark profile.wizard_completed_at
  2. subscribe to thesis groups (per sectors OR all if generalist)
  3. write user_sector / user_country / user_theme rows
  4. create referral waitlist entries + send invite emails
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import List
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.orm.user_orm import AppUser
from backend.app.models.orm.user_profile_orm import UserProfile
from backend.app.models.orm.user_sector_orm import UserSector
from backend.app.models.orm.user_country_orm import UserCountry
from backend.app.models.orm.user_theme_orm import UserTheme
from backend.app.models.orm.universe_v2_orm import UserUniverseGroup
from backend.app.models.orm.waitlist_orm import WaitlistEntry
from backend.app.services.onboarding.sector_mapping import (
    sector_to_universe_groups, ALL_UNIVERSE_GROUPS,
)
from backend.app.services.email.resend_client import send_email
from backend.app.services.email.templates.waitlist_referral_invite import render_waitlist_referral_invite

logger = logging.getLogger(__name__)


def _user_display_name(user: AppUser) -> str:
    return getattr(user, "name", None) or user.email.split("@")[0]


def finalize_wizard(
    db: Session,
    user: AppUser,
    profile: UserProfile,
    sectors: List[dict],
    countries: List[dict],
    themes: List[dict],
    invitees: List[dict],
) -> None:
    # 1) wipe existing wizard rows in case user is re-running (rare but defensive)
    db.query(UserSector).filter(UserSector.user_id == user.id).delete()
    db.query(UserCountry).filter(UserCountry.user_id == user.id).delete()
    db.query(UserTheme).filter(UserTheme.user_id == user.id).delete()

    # 2) write user_sector rows + collect group IDs to subscribe
    groups_to_subscribe: set[str] = set()
    for s in sectors:
        sid = s["sector_id"]
        db.add(UserSector(user_id=user.id, sector_id=sid, custom_label=s.get("custom_label")))
        if sid != "generalist" and sid != "other":
            groups_to_subscribe.update(sector_to_universe_groups(sid))

    if profile.is_generalist or any(s["sector_id"] == "generalist" for s in sectors):
        groups_to_subscribe.update(ALL_UNIVERSE_GROUPS)

    # 3) write user_country rows
    for c in countries:
        db.add(UserCountry(
            user_id=user.id,
            country_code=c["country_code"],
            custom_label=c.get("custom_label"),
        ))

    # 4) write user_theme rows
    for i, t in enumerate(themes):
        if not t.get("theme_text"):  # skip empties
            continue
        db.add(UserTheme(
            user_id=user.id,
            sector_id=t.get("sector_id"),  # None = cross-sector
            theme_text=t["theme_text"],
            sort_order=t.get("sort_order", i),
        ))

    # 5) subscribe to thesis groups (idempotent — UPSERT-like via ON CONFLICT)
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    for gid in groups_to_subscribe:
        stmt = pg_insert(UserUniverseGroup).values(
            user_id=user.id, group_id=gid,
        ).on_conflict_do_nothing()
        db.execute(stmt)

    # 6) create referral waitlist entries + send invite emails
    inviter_name = _user_display_name(user)
    for inv in invitees:
        email = (inv.get("email") or "").strip()
        if not email:
            continue
        existing = db.query(WaitlistEntry).filter(WaitlistEntry.email == email).first()
        if existing:
            continue  # don't overwrite existing entries (whatever status)
        invitee = WaitlistEntry(
            email=email,
            status="approved",
            referred_by_user_id=user.id,
            approved_at=datetime.now(timezone.utc),
            approved_by_user_id=user.id,
        )
        db.add(invitee)
        try:
            signin_url = f"{settings.FRONTEND_URL.rstrip('/')}/signin"
            msg = render_waitlist_referral_invite(
                invitee_name=None,
                inviter_name=inviter_name,
                inviter_message=inv.get("message"),
                signin_url=signin_url,
            )
            send_email(to=email, subject=msg["subject"], html=msg["html"])
        except Exception as e:  # noqa: BLE001
            logger.warning("referral invite email failed (non-fatal): %s", e)

    # 7) finalize profile
    profile.wizard_completed_at = datetime.now(timezone.utc)
    profile.updated_at = datetime.now(timezone.utc)
    db.commit()
```

- [ ] **Step 3: Add `/me/wizard/finish` and `/me/wizard/skip` endpoints**

Append to `onboarding.py` (in `me_router`):

```python
class FinishWizardIn(BaseModel):
    sectors:   list[dict] = []
    countries: list[dict] = []
    themes:    list[dict] = []
    invitees:  list[dict] = []


@me_router.post("/wizard/finish")
def finish_wizard(
    payload: FinishWizardIn,
    user = Depends(require_user),
    db: Session = Depends(get_phase2_session),
):
    from backend.app.services.onboarding.finalize_wizard import finalize_wizard
    profile = _get_or_create_profile(db, user)
    finalize_wizard(
        db, user, profile,
        payload.sectors, payload.countries, payload.themes, payload.invitees,
    )
    return {"status": "completed"}


@me_router.post("/wizard/skip")
def skip_wizard(
    user = Depends(require_user),
    db: Session = Depends(get_phase2_session),
):
    from backend.app.services.onboarding.sector_mapping import ALL_UNIVERSE_GROUPS
    from backend.app.models.orm.universe_v2_orm import UserUniverseGroup
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    profile = _get_or_create_profile(db, user)
    profile.wizard_skipped_at = datetime.now(timezone.utc)
    profile.is_generalist     = True
    profile.updated_at        = datetime.now(timezone.utc)
    for gid in ALL_UNIVERSE_GROUPS:
        stmt = pg_insert(UserUniverseGroup).values(user_id=user.id, group_id=gid).on_conflict_do_nothing()
        db.execute(stmt)
    db.commit()
    return {"status": "skipped"}
```

- [ ] **Step 4: Run + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_wizard_finish.py -v
```

Expected: 4 passed.

```bash
git add backend/app/api/routers/v1/onboarding.py \
        backend/app/services/onboarding/finalize_wizard.py \
        backend/tests/integration/onboarding/test_wizard_finish.py
git commit -m "feat(api): /me/wizard/{finish,skip} + thesis-group subscription side-effects"
```

---

## Phase E — OAuth callback integration

### Task 15: OAuth callback consults waitlist (allow approved, auto-add self-serve attempts)

**Files:**
- Modify: `backend/app/api/routers/v1/auth.py` (the `{provider}/callback` handler)
- Test: `backend/tests/integration/onboarding/test_oauth_callback_routing.py`

- [ ] **Step 1: Find the callback handler in `auth.py`**

The existing handler upserts `app_user` regardless of waitlist status. We need to add a waitlist gate AFTER OAuth succeeds but BEFORE creating the AppUser session.

Current flow (existing): IdP callback → exchange code → upsert AppUser → set cookie → redirect to FRONTEND_URL.

New flow: IdP callback → exchange code → check waitlist → if approved → create AppUser + session + redirect; if not approved → upsert as `self_serve_attempt` waitlist entry + redirect to `/waitlist/access-pending` (no session created).

- [ ] **Step 2: Write failing test**

```python
# backend/tests/integration/onboarding/test_oauth_callback_routing.py
"""
Test the OAuth callback waitlist gate.

We can't easily TestClient the full Authlib flow, so we test the GATE LOGIC
in isolation. Refactor the gate into a pure function `check_waitlist_status(db, email)`
that the callback uses.
"""
from backend.app.services.onboarding.waitlist_gate import (
    check_waitlist_status, WaitlistGateResult,
)


def test_unknown_email_creates_self_serve_attempt(db_session):
    result = check_waitlist_status(db_session, "stranger@example.com")
    assert result.outcome == "rejected_self_serve"
    # Verify a row was created
    from backend.app.models.orm.waitlist_orm import WaitlistEntry
    row = db_session.query(WaitlistEntry).filter(
        WaitlistEntry.email == "stranger@example.com"
    ).first()
    assert row is not None
    assert row.status == "self_serve_attempt"
    db_session.delete(row); db_session.commit()


def test_approved_email_passes_gate(db_session):
    from backend.app.models.orm.waitlist_orm import WaitlistEntry
    entry = WaitlistEntry(email="ok@example.com", status="approved")
    db_session.add(entry); db_session.commit()
    try:
        result = check_waitlist_status(db_session, "ok@example.com")
        assert result.outcome == "allowed"
    finally:
        db_session.delete(entry); db_session.commit()


def test_pending_email_blocks_gate(db_session):
    from backend.app.models.orm.waitlist_orm import WaitlistEntry
    entry = WaitlistEntry(email="pending@example.com", status="pending")
    db_session.add(entry); db_session.commit()
    try:
        result = check_waitlist_status(db_session, "pending@example.com")
        assert result.outcome == "rejected_pending"
    finally:
        db_session.delete(entry); db_session.commit()
```

- [ ] **Step 3: Implement `waitlist_gate.py`**

```python
# backend/app/services/onboarding/waitlist_gate.py
"""
The OAuth-callback gate. After the IdP exchange returns a verified email,
we consult the waitlist:
  - status='approved'           → allow sign-in (proceed to AppUser upsert)
  - status='pending'/'rejected' → block (redirect to /waitlist/access-pending)
  - email not in waitlist       → auto-create as 'self_serve_attempt' + block
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from sqlalchemy.orm import Session
from backend.app.models.orm.waitlist_orm import WaitlistEntry


GateOutcome = Literal[
    "allowed",
    "rejected_pending",
    "rejected_rejected",
    "rejected_self_serve",
]


@dataclass
class WaitlistGateResult:
    outcome: GateOutcome
    waitlist_entry: WaitlistEntry | None = None


def check_waitlist_status(db: Session, email: str) -> WaitlistGateResult:
    """Inspect the waitlist for `email`. Auto-creates self_serve_attempt rows
    for unknown emails (so Sharon can review them later)."""
    entry = db.query(WaitlistEntry).filter(WaitlistEntry.email == email).first()
    if entry is None:
        entry = WaitlistEntry(email=email, status="self_serve_attempt")
        db.add(entry); db.commit(); db.refresh(entry)
        return WaitlistGateResult(outcome="rejected_self_serve", waitlist_entry=entry)

    if entry.status == "approved":
        return WaitlistGateResult(outcome="allowed", waitlist_entry=entry)
    if entry.status == "pending":
        return WaitlistGateResult(outcome="rejected_pending", waitlist_entry=entry)
    if entry.status == "rejected":
        return WaitlistGateResult(outcome="rejected_rejected", waitlist_entry=entry)
    # self_serve_attempt — re-attempted; same outcome
    return WaitlistGateResult(outcome="rejected_self_serve", waitlist_entry=entry)
```

- [ ] **Step 4: Wire gate into the OAuth callback in `auth.py`**

Open `backend/app/api/routers/v1/auth.py` and find the function handling `{provider}/callback` (probably named `oauth_callback`). After the line that extracts `email` from the IdP response, BEFORE the AppUser upsert, insert:

```python
from backend.app.services.onboarding.waitlist_gate import check_waitlist_status

gate = check_waitlist_status(db, email)
if gate.outcome != "allowed":
    # Block sign-in; redirect to a pending-access page without creating session.
    target = f"{settings.FRONTEND_URL.rstrip('/')}/waitlist/access-pending?reason={gate.outcome}"
    return RedirectResponse(target)
```

After successful AppUser creation + cookie set, change the final redirect from `FRONTEND_URL` to a route-by-onboarding-status URL:

```python
# Determine where to send them after sign-in
profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
if profile and profile.wizard_completed_at:
    redirect_target = f"{settings.FRONTEND_URL.rstrip('/')}/dashboard"
elif profile and profile.wizard_skipped_at:
    redirect_target = f"{settings.FRONTEND_URL.rstrip('/')}/dashboard"
else:
    redirect_target = f"{settings.FRONTEND_URL.rstrip('/')}/onboarding"
return RedirectResponse(redirect_target)
```

Add the import at the top of `auth.py`:

```python
from backend.app.models.orm.user_profile_orm import UserProfile
```

- [ ] **Step 5: Run gate-only tests + commit**

```bash
cd backend && PYTHONIOENCODING=utf-8 PYTHONPATH=. pytest tests/integration/onboarding/test_oauth_callback_routing.py -v
```

Expected: 3 passed.

The full callback redirection is hard to unit-test (involves Authlib state). We test it manually via the smoke test in Phase J.

```bash
git add backend/app/services/onboarding/waitlist_gate.py \
        backend/app/api/routers/v1/auth.py \
        backend/tests/integration/onboarding/test_oauth_callback_routing.py
git commit -m "feat(auth): waitlist gate on OAuth callback + onboarding-aware post-signin redirect"
```

---

### Task 16: Backend smoke — full sign-in to wizard journey via curl

**Files:** none new; this is a smoke-verification task.

- [ ] **Step 1: Apply migration to local Postgres + restart uvicorn**

```bash
cd /c/Users/Sharo/AI_projects/AlphaGraph_new/backend && \
    POSTGRES_URI="postgresql+psycopg2://alphagraph:alphagraph_dev@localhost:5432/alphagraph" \
    alembic upgrade head
```

```bash
# In a separate terminal at project root:
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: Submit waitlist via curl**

```bash
curl -s -X POST http://localhost:8000/api/v1/public/waitlist \
  -H 'Content-Type: application/json' \
  -d '{"email":"smoketest@example.com","full_name":"Smoke Test","self_reported_role":"Buyside Analyst"}' | python -m json.tool
```

Expected: `{"email": "smoketest@example.com", "status": "pending"}`.

- [ ] **Step 3: Promote yourself to admin (if you've signed in once before)**

```bash
PYTHONIOENCODING=utf-8 PYTHONPATH=. python -m backend.scripts.seed_admin_user --email <your-existing-app_user-email>
```

- [ ] **Step 4: Visit `/api/v1/admin/waitlist?status=pending` in a browser (signed in)**

Should show JSON list with the smoketest entry.

- [ ] **Step 5: Approve via curl using your session cookie**

```bash
# Get cookie from browser devtools, then:
curl -s -X POST http://localhost:8000/api/v1/admin/waitlist/<id>/approve \
  -H "Cookie: ag_session=<your-cookie>" | python -m json.tool
```

Expected: status updates to `approved`. (Email is "logged not sent" since RESEND_API_KEY isn't set in dev — check uvicorn logs for "[EMAIL not sent — ...]".)

- [ ] **Step 6: Commit any cleanup (if needed)**

No code changes typically. If you found any bugs during smoke, fix and commit. Otherwise this task is just verification.

---

## Phase F — Frontend design system

### Task 17: Linear-light Tailwind tokens + utility classes

**Files:**
- Modify: `frontend/tailwind.config.ts` (extend theme.colors)
- Create: `frontend/src/styles/tokens.css` (CSS variables for non-tailwind use)
- Modify: `frontend/src/app/globals.css` (import tokens)

- [ ] **Step 1: Extend Tailwind config**

In `frontend/tailwind.config.ts`, find `theme.extend` and add:

```typescript
const config = {
  // ... existing config ...
  theme: {
    extend: {
      // ... existing extensions ...
      colors: {
        // Linear-light palette (used by onboarding wizard + sign-in + admin queue)
        ag: {
          ink:      "#0f172a",   // primary text
          body:     "#475569",   // secondary text
          muted:    "#64748b",   // tertiary text
          subtle:   "#94a3b8",   // placeholder text
          line:     "#e5e7eb",   // default border
          hairline: "#f1f5f9",   // divider line
          page:     "#f6f7f9",   // page background
          card:     "#ffffff",   // card background
          accent:   "#5b6cff",   // primary accent (CTA, selected)
          "accent-fill":   "#eef0ff",  // selected chip bg
          "accent-text":   "#3b46c0",  // selected chip text
          generalist:      "#7c3aed",  // generalist purple
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['SF Mono', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        'ag-card': '0 1px 2px rgba(15,23,42,0.04), 0 4px 12px rgba(15,23,42,0.04)',
        'ag-cta':  '0 1px 2px rgba(91,108,255,0.2)',
      },
      borderRadius: {
        'ag-card': '10px',
      },
    },
  },
};
```

- [ ] **Step 2: Create the tokens.css file (used outside tailwind contexts)**

```css
/* frontend/src/styles/tokens.css */
:root {
  --ag-ink: #0f172a;
  --ag-body: #475569;
  --ag-muted: #64748b;
  --ag-subtle: #94a3b8;
  --ag-line: #e5e7eb;
  --ag-hairline: #f1f5f9;
  --ag-page: #f6f7f9;
  --ag-card: #ffffff;
  --ag-accent: #5b6cff;
  --ag-accent-fill: #eef0ff;
  --ag-accent-text: #3b46c0;
  --ag-generalist: #7c3aed;
}
```

In `frontend/src/app/globals.css`, add at top:

```css
@import './tokens.css';
```

(Path may need adjustment depending on actual structure.)

- [ ] **Step 3: Smoke-test build still works**

```bash
cd /c/Users/Sharo/AI_projects/AlphaGraph_new/frontend && npm run build 2>&1 | tail -10
```

Expected: build succeeds, all 16 routes still build.

- [ ] **Step 4: Commit**

```bash
git add frontend/tailwind.config.ts frontend/src/styles/tokens.css frontend/src/app/globals.css
git commit -m "feat(frontend): Linear-light design tokens (Tailwind + CSS vars)"
```

---

### Task 18: Reusable wizard primitives

**Files:**
- Create: `frontend/src/components/wizard/WizardShell.tsx`
- Create: `frontend/src/components/wizard/ProgressBar.tsx`
- Create: `frontend/src/components/wizard/ChipSingleSelect.tsx`
- Create: `frontend/src/components/wizard/ChipMultiSelect.tsx`

These components are pure (no state ownership) — props in, callbacks out. Wizard step components consume them.

- [ ] **Step 1: WizardShell + ProgressBar**

```tsx
// frontend/src/components/wizard/ProgressBar.tsx
import React from "react";

export function ProgressBar({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex gap-1.5">
      {Array.from({ length: total }).map((_, i) => (
        <span
          key={i}
          className="inline-block w-[18px] h-[3px] rounded-sm"
          style={{ background: i < current ? "var(--ag-accent)" : "var(--ag-line)" }}
        />
      ))}
    </div>
  );
}
```

```tsx
// frontend/src/components/wizard/WizardShell.tsx
import React from "react";
import { ProgressBar } from "./ProgressBar";

interface Props {
  step: number;          // 1-based
  total: number;
  title: string;
  subtitle?: string;
  onBack?: () => void;
  onContinue?: () => void;
  onSkip?: () => void;
  continueDisabled?: boolean;
  showKeyboardHint?: boolean;
  children: React.ReactNode;
}

export function WizardShell({
  step, total, title, subtitle,
  onBack, onContinue, onSkip,
  continueDisabled, showKeyboardHint = true, children,
}: Props) {
  // Enter to continue
  React.useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === "Enter" && !continueDisabled && onContinue) {
        onContinue();
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [continueDisabled, onContinue]);

  return (
    <div className="min-h-screen flex items-center justify-center px-6"
         style={{ background: "var(--ag-page)", fontFamily: "Inter, sans-serif" }}>
      <div className="w-full max-w-[560px] bg-white border border-ag-line rounded-ag-card p-9 shadow-ag-card"
           style={{ borderColor: "var(--ag-line)" }}>
        <div className="flex justify-between items-center mb-7">
          <span className="text-[11px] font-medium tracking-[0.04em]"
                style={{ color: "var(--ag-muted)" }}>
            STEP {step} OF {total}
          </span>
          <ProgressBar current={step} total={total} />
        </div>

        <h2 className="text-[22px] font-semibold tracking-[-0.012em] leading-tight"
            style={{ color: "var(--ag-ink)" }}>
          {title}
        </h2>
        {subtitle && (
          <p className="mt-1.5 text-[13px] leading-relaxed mb-5"
             style={{ color: "var(--ag-muted)" }}>
            {subtitle}
          </p>
        )}

        <div className="my-6">{children}</div>

        <div className="flex justify-between items-center pt-4 border-t"
             style={{ borderColor: "var(--ag-hairline)" }}>
          {onBack ? (
            <button onClick={onBack}
                    className="text-[13px] py-1.5 px-0"
                    style={{ color: "var(--ag-muted)" }}>
              ← Back
            </button>
          ) : <span />}
          <div className="flex items-center gap-3">
            {showKeyboardHint && onContinue && (
              <span className="text-[11px]" style={{ color: "var(--ag-subtle)" }}>
                or press <kbd className="font-mono text-[10px] px-1.5 py-0.5 rounded border"
                              style={{
                                background: "var(--ag-hairline)",
                                borderColor: "var(--ag-line)",
                                color: "var(--ag-body)",
                              }}>↵</kbd>
              </span>
            )}
            {onSkip && (
              <button onClick={onSkip}
                      className="text-[13px] px-3.5 py-2 border rounded-md"
                      style={{
                        background: "var(--ag-card)",
                        borderColor: "var(--ag-line)",
                        color: "var(--ag-body)",
                      }}>
                Skip
              </button>
            )}
            {onContinue && (
              <button onClick={onContinue}
                      disabled={continueDisabled}
                      className="text-[13px] px-4.5 py-2 rounded-md font-medium disabled:opacity-40"
                      style={{
                        background: "var(--ag-accent)",
                        color: "#fff",
                        boxShadow: "var(--tw-shadow, 0 1px 2px rgba(91,108,255,0.2))",
                      }}>
                Continue
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: ChipSingleSelect**

```tsx
// frontend/src/components/wizard/ChipSingleSelect.tsx
import React from "react";

export interface ChipOption {
  id: string;
  label: string;
  isOther?: boolean;       // shows dashed border + opens custom input
  isGeneralist?: boolean;  // purple accent
}

interface Props {
  options: ChipOption[];
  value: string | null;
  customText: string;
  onChange: (id: string, customText?: string) => void;
}

export function ChipSingleSelect({ options, value, customText, onChange }: Props) {
  const otherOpen = value === "other";

  return (
    <div>
      <div className="flex flex-col gap-1.5 mb-3">
        {options.map((opt, i) => {
          const selected = value === opt.id;
          const isDashed = opt.isOther && !selected;
          return (
            <button
              key={opt.id}
              onClick={() => onChange(opt.id)}
              className="text-left px-3.5 py-2.5 rounded-md flex justify-between items-center transition-colors"
              style={{
                background: selected ? "var(--ag-accent-fill)" : "var(--ag-card)",
                border: isDashed
                  ? "1px dashed var(--ag-line)"
                  : selected
                    ? "1px solid var(--ag-accent)"
                    : "1px solid var(--ag-line)",
                color: selected
                  ? "var(--ag-accent-text)"
                  : opt.isOther ? "var(--ag-subtle)" : "var(--ag-body)",
                fontSize: "13px",
                fontWeight: selected ? 500 : 400,
              }}
            >
              <span>{opt.isOther ? "+ " + opt.label : opt.label}</span>
              {!opt.isOther && (
                <span className="font-mono text-[11px]"
                      style={{ color: selected ? "var(--ag-accent)" : "var(--ag-subtle)" }}>
                  {i + 1}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {otherOpen && (
        <input
          autoFocus
          value={customText}
          onChange={(e) => onChange("other", e.target.value)}
          placeholder="Type your role"
          className="w-full px-3 py-2 rounded-md text-[13px]"
          style={{
            border: "1px solid var(--ag-accent)",
            color: "var(--ag-ink)",
            outline: "none",
          }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: ChipMultiSelect**

```tsx
// frontend/src/components/wizard/ChipMultiSelect.tsx
import React from "react";
import type { ChipOption } from "./ChipSingleSelect";

interface Props {
  options: ChipOption[];
  selected: string[];
  customText: string;             // for "other" picks
  maxSelected?: number;            // step 3 uses 3; step 4 uses Infinity
  onToggle: (id: string) => void;
  onCustomTextChange: (text: string) => void;
}

export function ChipMultiSelect({
  options, selected, customText, maxSelected, onToggle, onCustomTextChange,
}: Props) {
  const atMax = maxSelected !== undefined && selected.length >= maxSelected
                && !(selected.length === maxSelected && selected.includes("generalist"));
  const otherOpen = selected.includes("other");

  return (
    <div>
      {maxSelected !== undefined && (
        <p className="text-[11px] font-medium mb-2.5"
           style={{ color: "var(--ag-accent)", letterSpacing: "0.02em" }}>
          {selected.filter(s => s !== "generalist").length} of {maxSelected} selected
          {selected.length < maxSelected ? ` · ${maxSelected - selected.length} left` : ""}
        </p>
      )}

      <div className="flex flex-wrap gap-1.5 mb-3">
        {options.map((opt) => {
          const isSelected = selected.includes(opt.id);
          const dimmed = atMax && !isSelected && !opt.isOther && !opt.isGeneralist;
          const isDashed = opt.isOther && !isSelected;

          return (
            <button
              key={opt.id}
              onClick={() => !dimmed && onToggle(opt.id)}
              title={dimmed ? `Already at ${maxSelected} — pick Generalist or remove a current pick` : undefined}
              disabled={dimmed}
              className="px-2.5 py-1.5 rounded-md text-[12.5px] inline-flex items-center gap-1 transition-colors"
              style={{
                background: isSelected
                  ? "var(--ag-accent-fill)"
                  : opt.isGeneralist ? "#fff" : "var(--ag-card)",
                border: isDashed
                  ? "1px dashed var(--ag-line)"
                  : isSelected
                    ? "1px solid var(--ag-accent)"
                    : opt.isGeneralist
                      ? "1px solid var(--ag-line)"
                      : "1px solid var(--ag-line)",
                color: isSelected
                  ? "var(--ag-accent-text)"
                  : opt.isGeneralist
                    ? "var(--ag-generalist)"
                    : opt.isOther
                      ? "var(--ag-subtle)"
                      : "var(--ag-body)",
                fontWeight: isSelected || opt.isGeneralist ? 500 : 400,
                opacity: dimmed ? 0.4 : 1,
                cursor: dimmed ? "not-allowed" : "pointer",
              }}
            >
              {opt.isGeneralist ? "⭐ " : ""}{opt.isOther ? "+ " + opt.label : opt.label}
              {isSelected && !opt.isOther && <span className="text-[14px]">×</span>}
            </button>
          );
        })}
      </div>

      {otherOpen && (
        <input
          autoFocus
          value={customText}
          onChange={(e) => onCustomTextChange(e.target.value)}
          placeholder="Type your option"
          className="w-full px-3 py-2 rounded-md text-[13px]"
          style={{ border: "1px solid var(--ag-accent)", outline: "none" }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: TypeScript-check + commit**

```bash
cd /c/Users/Sharo/AI_projects/AlphaGraph_new/frontend && npx tsc --noEmit 2>&1 | tail -10
```

Expected: no new errors in the wizard component files.

```bash
git add frontend/src/components/wizard/
git commit -m "feat(frontend): wizard primitives — Shell, ProgressBar, ChipSingleSelect, ChipMultiSelect"
```

---

## Phase G — Public pages + sign-in

### Task 19: Landing page + waitlist form

**Files:**
- Create: `frontend/src/app/(public)/layout.tsx`
- Create: `frontend/src/app/(public)/page.tsx`
- Create: `frontend/src/app/(public)/waitlist/page.tsx`
- Create: `frontend/src/app/(public)/waitlist/thanks/page.tsx`
- Create: `frontend/src/app/(public)/waitlist/access-pending/page.tsx`

The `(public)` route group is unauthenticated. Server-side, these routes don't require a session cookie.

- [ ] **Step 1: Public layout (no UserMenu)**

```tsx
// frontend/src/app/(public)/layout.tsx
import React from "react";

export default function PublicLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ background: "var(--ag-page)", minHeight: "100vh" }}>
      {children}
    </div>
  );
}
```

- [ ] **Step 2: Landing page (`/`)**

Replace existing `frontend/src/app/page.tsx` (or wherever the root page lives — verify with `git ls-files frontend/src/app/page.tsx`). If the dashboard currently lives at `/`, move it to `(dashboard)/page.tsx` first.

```tsx
// frontend/src/app/(public)/page.tsx
"use client";
import Link from "next/link";

export default function LandingPage() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-6"
          style={{ fontFamily: "Inter, sans-serif" }}>
      <div className="max-w-[640px] w-full bg-white border border-ag-line rounded-ag-card p-12 shadow-ag-card"
           style={{ borderColor: "var(--ag-line)" }}>
        <h1 className="text-[32px] font-semibold tracking-[-0.02em] leading-tight"
            style={{ color: "var(--ag-ink)" }}>
          AlphaGraph
        </h1>
        <p className="mt-2 text-[15px] leading-relaxed"
           style={{ color: "var(--ag-body)" }}>
          The AI-bottleneck research platform for buyside analysts.
          Source-traced fundamentals · multilingual transcripts · zero-hallucination chat.
        </p>
        <p className="mt-6 text-[13px]" style={{ color: "var(--ag-muted)" }}>
          Currently invite-only. Request access to join the founding cohort.
        </p>
        <div className="mt-8 flex gap-3">
          <Link href="/waitlist"
                className="text-[14px] px-5 py-2.5 rounded-md font-medium"
                style={{ background: "var(--ag-accent)", color: "#fff",
                         boxShadow: "0 1px 2px rgba(91,108,255,0.2)" }}>
            Request access
          </Link>
          <Link href="/signin"
                className="text-[14px] px-5 py-2.5 rounded-md border"
                style={{ borderColor: "var(--ag-line)", color: "var(--ag-body)" }}>
            Already approved? Sign in
          </Link>
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 3: Waitlist form**

```tsx
// frontend/src/app/(public)/waitlist/page.tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { onboardingClient } from "@/lib/onboarding/client";

export default function WaitlistFormPage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null); setSubmitting(true);
    const fd = new FormData(e.currentTarget);
    try {
      await onboardingClient.submitWaitlist({
        email:                String(fd.get("email")),
        full_name:            String(fd.get("full_name") || "") || null,
        self_reported_role:   String(fd.get("role")      || "") || null,
        self_reported_firm:   String(fd.get("firm")      || "") || null,
        note:                 String(fd.get("note")      || "") || null,
      });
      router.push("/waitlist/thanks");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submission failed");
    } finally {
      setSubmitting(false);
    }
  }

  const inputCls = "w-full px-3 py-2 rounded-md text-[13px]";
  const inputStyle = { border: "1px solid var(--ag-line)", outline: "none",
                        color: "var(--ag-ink)", background: "var(--ag-card)" };

  return (
    <main className="min-h-screen flex items-center justify-center px-6"
          style={{ fontFamily: "Inter, sans-serif" }}>
      <form onSubmit={handleSubmit}
            className="w-full max-w-[520px] bg-white border rounded-ag-card p-9 shadow-ag-card"
            style={{ borderColor: "var(--ag-line)" }}>
        <h2 className="text-[22px] font-semibold tracking-[-0.012em]"
            style={{ color: "var(--ag-ink)" }}>Request access</h2>
        <p className="mt-1.5 text-[13px]" style={{ color: "var(--ag-muted)" }}>
          We review every application personally. Usually within 1 business day.
        </p>

        <div className="mt-6 space-y-3">
          <label className="block">
            <span className="text-[12px]" style={{ color: "var(--ag-body)" }}>Email *</span>
            <input name="email" type="email" required className={`${inputCls} mt-1`} style={inputStyle} />
          </label>
          <label className="block">
            <span className="text-[12px]" style={{ color: "var(--ag-body)" }}>Full name</span>
            <input name="full_name" className={`${inputCls} mt-1`} style={inputStyle} />
          </label>
          <label className="block">
            <span className="text-[12px]" style={{ color: "var(--ag-body)" }}>Role</span>
            <input name="role" placeholder="Buyside Analyst, PM, Sell-side..." className={`${inputCls} mt-1`} style={inputStyle} />
          </label>
          <label className="block">
            <span className="text-[12px]" style={{ color: "var(--ag-body)" }}>Firm</span>
            <input name="firm" className={`${inputCls} mt-1`} style={inputStyle} />
          </label>
          <label className="block">
            <span className="text-[12px]" style={{ color: "var(--ag-body)" }}>Why AlphaGraph?</span>
            <textarea name="note" rows={3} className={`${inputCls} mt-1`} style={inputStyle} />
          </label>
        </div>

        {error && <p className="mt-3 text-[12px] text-red-600">{error}</p>}

        <button type="submit" disabled={submitting}
                className="mt-6 px-5 py-2.5 rounded-md text-[14px] font-medium disabled:opacity-50"
                style={{ background: "var(--ag-accent)", color: "#fff" }}>
          {submitting ? "Submitting..." : "Request access"}
        </button>
      </form>
    </main>
  );
}
```

- [ ] **Step 4: Thanks + access-pending pages**

```tsx
// frontend/src/app/(public)/waitlist/thanks/page.tsx
"use client";
export default function WaitlistThanks() {
  return (
    <main className="min-h-screen flex items-center justify-center px-6"
          style={{ fontFamily: "Inter, sans-serif" }}>
      <div className="max-w-[480px] bg-white border rounded-ag-card p-10 shadow-ag-card text-center"
           style={{ borderColor: "var(--ag-line)" }}>
        <div className="text-[40px] mb-3">📨</div>
        <h2 className="text-[22px] font-semibold tracking-[-0.012em]"
            style={{ color: "var(--ag-ink)" }}>Thanks — application received</h2>
        <p className="mt-2 text-[13px]" style={{ color: "var(--ag-muted)" }}>
          We review every application personally, usually within 1 business day. You'll get an email once you're approved.
        </p>
      </div>
    </main>
  );
}
```

```tsx
// frontend/src/app/(public)/waitlist/access-pending/page.tsx
"use client";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

export default function AccessPending() {
  const reason = useSearchParams().get("reason") || "rejected_self_serve";
  const messages: Record<string, { title: string; body: string }> = {
    rejected_self_serve: {
      title: "We need to review your access first",
      body: "We've added your email to our queue. Want to tell us a bit more so we can prioritize?",
    },
    rejected_pending: {
      title: "Application is in review",
      body: "We've already received your application. We'll email you when access is approved.",
    },
    rejected_rejected: {
      title: "Sorry — access was not granted",
      body: "If circumstances change, feel free to apply again with more context.",
    },
  };
  const m = messages[reason] || messages.rejected_self_serve;
  return (
    <main className="min-h-screen flex items-center justify-center px-6"
          style={{ fontFamily: "Inter, sans-serif" }}>
      <div className="max-w-[480px] bg-white border rounded-ag-card p-10 shadow-ag-card text-center"
           style={{ borderColor: "var(--ag-line)" }}>
        <h2 className="text-[20px] font-semibold tracking-[-0.012em]"
            style={{ color: "var(--ag-ink)" }}>{m.title}</h2>
        <p className="mt-2 text-[13px]" style={{ color: "var(--ag-muted)" }}>{m.body}</p>
        <Link href="/waitlist" className="inline-block mt-5 text-[13px]"
              style={{ color: "var(--ag-accent)" }}>
          Tell us more →
        </Link>
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

Expected: build clean.

```bash
git add 'frontend/src/app/(public)/'
git commit -m "feat(frontend): public pages — landing, waitlist form, thanks, access-pending"
```

---

### Task 20: Sign-in page

**Files:**
- Create: `frontend/src/app/(public)/signin/page.tsx`

- [ ] **Step 1: Implement**

```tsx
// frontend/src/app/(public)/signin/page.tsx
"use client";
import { API_BASE_URL } from "@/lib/api/base";

export default function SignInPage() {
  const apiBase = API_BASE_URL.replace(/\/api\/v1$/, "");

  return (
    <main className="min-h-screen flex items-center justify-center px-6"
          style={{ fontFamily: "Inter, sans-serif" }}>
      <div className="w-full max-w-[420px] bg-white border rounded-ag-card p-9 shadow-ag-card"
           style={{ borderColor: "var(--ag-line)" }}>
        <h2 className="text-[22px] font-semibold tracking-[-0.012em]"
            style={{ color: "var(--ag-ink)" }}>Sign in to AlphaGraph</h2>
        <p className="mt-1.5 text-[13px]" style={{ color: "var(--ag-muted)" }}>
          Use the Google or Microsoft account you applied with.
        </p>

        <div className="mt-7 space-y-3">
          <a href={`${apiBase}/api/v1/auth/google/login`}
             className="flex items-center justify-center gap-3 px-4 py-2.5 border rounded-md text-[14px]"
             style={{ borderColor: "var(--ag-line)", color: "var(--ag-ink)", background: "var(--ag-card)" }}>
            <span>Sign in with Google</span>
          </a>
          <a href={`${apiBase}/api/v1/auth/microsoft/login`}
             className="flex items-center justify-center gap-3 px-4 py-2.5 border rounded-md text-[14px]"
             style={{ borderColor: "var(--ag-line)", color: "var(--ag-ink)", background: "var(--ag-card)" }}>
            <span>Sign in with Microsoft</span>
          </a>
        </div>

        <p className="mt-7 text-[12px]" style={{ color: "var(--ag-subtle)" }}>
          Not approved yet? <a href="/waitlist" style={{ color: "var(--ag-accent)" }}>Request access</a>.
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -8
git add 'frontend/src/app/(public)/signin/'
git commit -m "feat(frontend): /signin page with Google + Microsoft buttons"
```

---

### Task 21: Auth-gated routing middleware

**Files:**
- Create: `frontend/src/middleware.ts`

Next.js middleware runs on every request. We use it to redirect:
- Unauthenticated users hitting `(dashboard)` or `(onboarding)` routes → `/signin`
- Authenticated users with incomplete wizard → `/onboarding`
- Authenticated users at `/signin` → `/dashboard`

The session cookie is `ag_session`. We don't decode it client-side; we check existence + delegate full validation to backend `/api/v1/auth/me`.

- [ ] **Step 1: Create middleware**

```typescript
// frontend/src/middleware.ts
import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = [
  "/", "/signin", "/waitlist", "/waitlist/thanks", "/waitlist/access-pending",
];
const ONBOARDING_PATH = "/onboarding";

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some(p => pathname === p || pathname.startsWith(p + "/"));
}

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const sessionCookie = req.cookies.get("ag_session");

  // Static / API / Next assets: skip
  if (pathname.startsWith("/_next") || pathname.startsWith("/api") ||
      pathname.includes(".")) {
    return NextResponse.next();
  }

  // Public routes: always allow (but if signed in, optionally redirect from /signin)
  if (isPublic(pathname)) {
    if (pathname === "/signin" && sessionCookie) {
      // Already signed in — let the page decide where to send them
      // (the page calls /me/onboarding-status and redirects accordingly)
    }
    return NextResponse.next();
  }

  // Protected routes: require session cookie
  if (!sessionCookie) {
    const url = req.nextUrl.clone();
    url.pathname = "/signin";
    url.searchParams.set("from", pathname);
    return NextResponse.redirect(url);
  }

  // Has cookie — let request through. The dashboard layout itself
  // calls /me/onboarding-status and redirects to /onboarding if incomplete.
  return NextResponse.next();
}

export const config = {
  matcher: [
    // Match all routes except _next, api, and static files
    "/((?!_next|api|.*\\..*).*)",
  ],
};
```

- [ ] **Step 2: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -5
git add frontend/src/middleware.ts
git commit -m "feat(frontend): auth-gated routing middleware"
```

---

## Phase H — Wizard frontend

### Task 22: Onboarding state store + save hook

**Files:**
- Modify: `frontend/package.json` (add `zustand`)
- Create: `frontend/src/lib/onboarding/store.ts`
- Create: `frontend/src/lib/onboarding/useWizardSave.ts`

- [ ] **Step 1: Install zustand**

```bash
cd /c/Users/Sharo/AI_projects/AlphaGraph_new/frontend && npm install zustand
```

- [ ] **Step 2: Create the Zustand store**

```typescript
// frontend/src/lib/onboarding/store.ts
import { create } from "zustand";
import type {
  RoleId, FirmStrategyId, SectorPick, CountryPick, UserTheme, InviteIn,
} from "./types";

interface OnboardingState {
  currentStep: number;

  // Step 1
  role:           { value: RoleId | null; customText: string };
  setRole:        (id: RoleId, customText?: string) => void;

  // Step 2
  firmStrategy:    { value: FirmStrategyId | null; customText: string };
  setFirmStrategy: (id: FirmStrategyId, customText?: string) => void;

  // Step 3
  sectors:        SectorPick[];
  isGeneralist:   boolean;
  toggleSector:   (id: string, customText?: string) => void;

  // Step 4
  countries:      CountryPick[];
  toggleCountry:  (code: string, customText?: string) => void;

  // Step 5
  themes:         UserTheme[];
  setThemes:      (themes: UserTheme[]) => void;

  // Step 6
  invitees:       InviteIn[];
  setInvitees:    (list: InviteIn[]) => void;

  // Navigation
  goToStep:       (n: number) => void;
  nextStep:       () => void;
  prevStep:       () => void;

  // Hydration (called on mount with server state)
  hydrate:        (initial: Partial<OnboardingState>) => void;
}

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  currentStep: 1,
  role:        { value: null, customText: "" },
  firmStrategy: { value: null, customText: "" },
  sectors: [],
  isGeneralist: false,
  countries: [],
  themes: [],
  invitees: [],

  setRole: (id, customText = "") =>
    set({ role: { value: id, customText: id === "other" ? customText : "" } }),

  setFirmStrategy: (id, customText = "") =>
    set({ firmStrategy: { value: id, customText: id === "other" ? customText : "" } }),

  toggleSector: (id, customText = "") => {
    const s = get();
    if (id === "generalist") {
      set({ isGeneralist: !s.isGeneralist });
      return;
    }
    const exists = s.sectors.find(x => x.sector_id === id);
    if (exists) {
      set({ sectors: s.sectors.filter(x => x.sector_id !== id) });
      return;
    }
    // Max 3 (excluding generalist)
    const nonGeneralistCount = s.sectors.length;
    if (nonGeneralistCount >= 3) return;
    set({
      sectors: [...s.sectors, {
        sector_id: id,
        ...(id === "other" && customText ? { custom_label: customText } : {}),
      }],
    });
  },

  toggleCountry: (code, customText = "") => {
    const s = get();
    const exists = s.countries.find(x => x.country_code === code);
    if (exists) {
      set({ countries: s.countries.filter(x => x.country_code !== code) });
      return;
    }
    set({
      countries: [...s.countries, {
        country_code: code,
        ...(code === "OTHER" && customText ? { custom_label: customText } : {}),
      }],
    });
  },

  setThemes:    (themes) => set({ themes }),
  setInvitees:  (list)   => set({ invitees: list }),

  goToStep: (n) => set({ currentStep: Math.max(1, Math.min(6, n)) }),
  nextStep: () => set({ currentStep: Math.min(6, get().currentStep + 1) }),
  prevStep: () => set({ currentStep: Math.max(1, get().currentStep - 1) }),

  hydrate: (initial) => set(initial),
}));
```

- [ ] **Step 3: Create save hook (per-step debounced PUT /me/profile)**

```typescript
// frontend/src/lib/onboarding/useWizardSave.ts
import { useEffect, useRef } from "react";
import { onboardingClient } from "./client";
import { useOnboardingStore } from "./store";

export function useWizardSave() {
  const { currentStep, role, firmStrategy, isGeneralist } = useOnboardingStore();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      onboardingClient.putProfile({
        role:                role.value || undefined,
        role_other:          role.customText || undefined,
        firm_strategy:       firmStrategy.value || undefined,
        firm_strategy_other: firmStrategy.customText || undefined,
        is_generalist:       isGeneralist,
        wizard_current_step: currentStep,
      } as any).catch((e) => {
        // Don't block UI; surface to console for debugging
        console.warn("wizard save failed:", e);
      });
    }, 500);

    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [currentStep, role.value, role.customText, firmStrategy.value,
      firmStrategy.customText, isGeneralist]);
}
```

- [ ] **Step 4: TypeScript check + commit**

```bash
cd frontend && npx tsc --noEmit 2>&1 | tail -5
git add frontend/package.json frontend/package-lock.json frontend/src/lib/onboarding/store.ts frontend/src/lib/onboarding/useWizardSave.ts
git commit -m "feat(frontend): onboarding Zustand store + debounced save hook"
```

---

### Task 23: Step 1 (Role) + Step 2 (Firm strategy) — single-select steps

**Files:**
- Create: `frontend/src/app/(onboarding)/onboarding/components/Step1Role.tsx`
- Create: `frontend/src/app/(onboarding)/onboarding/components/Step2FirmStrategy.tsx`

These are mostly identical (single-select chip lists, fed by static option arrays).

- [ ] **Step 1: Step 1 — Role**

```tsx
// frontend/src/app/(onboarding)/onboarding/components/Step1Role.tsx
"use client";
import { ChipSingleSelect, ChipOption } from "@/components/wizard/ChipSingleSelect";
import { useOnboardingStore } from "@/lib/onboarding/store";

const OPTIONS: ChipOption[] = [
  { id: "buyside_analyst", label: "Buyside Analyst" },
  { id: "buyside_pm",      label: "Buyside Portfolio Manager" },
  { id: "sell_side",       label: "Sell-side Analyst" },
  { id: "wealth_manager",  label: "Wealth Manager / RIA" },
  { id: "other",           label: "Other (type your role)", isOther: true },
];

export function Step1Role() {
  const { role, setRole } = useOnboardingStore();
  return (
    <ChipSingleSelect
      options={OPTIONS}
      value={role.value}
      customText={role.customText}
      onChange={(id, txt) => setRole(id as any, txt)}
    />
  );
}
```

- [ ] **Step 2: Step 2 — Firm strategy**

```tsx
// frontend/src/app/(onboarding)/onboarding/components/Step2FirmStrategy.tsx
"use client";
import { ChipSingleSelect, ChipOption } from "@/components/wizard/ChipSingleSelect";
import { useOnboardingStore } from "@/lib/onboarding/store";

const OPTIONS: ChipOption[] = [
  { id: "long_only",  label: "Long-only / Mutual / Pension" },
  { id: "long_short", label: "Long/Short Fundamental" },
  { id: "rel_value",  label: "Rebal / Relative Value" },
  { id: "macro",      label: "Macro" },
  { id: "sell_side",  label: "Investment Bank / Sell-side" },
  { id: "other",      label: "Other (type your strategy)", isOther: true },
];

export function Step2FirmStrategy() {
  const { firmStrategy, setFirmStrategy } = useOnboardingStore();
  return (
    <ChipSingleSelect
      options={OPTIONS}
      value={firmStrategy.value}
      customText={firmStrategy.customText}
      onChange={(id, txt) => setFirmStrategy(id as any, txt)}
    />
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add 'frontend/src/app/(onboarding)/onboarding/components/Step1Role.tsx' \
        'frontend/src/app/(onboarding)/onboarding/components/Step2FirmStrategy.tsx'
git commit -m "feat(frontend): wizard Steps 1 (Role) + 2 (Firm strategy)"
```

---

### Task 24: Step 3 (Sectors, max 3) + Step 4 (Countries)

**Files:**
- Create: `frontend/src/app/(onboarding)/onboarding/components/Step3Sectors.tsx`
- Create: `frontend/src/app/(onboarding)/onboarding/components/Step4Countries.tsx`

- [ ] **Step 1: Step 3 — Sectors**

```tsx
// frontend/src/app/(onboarding)/onboarding/components/Step3Sectors.tsx
"use client";
import { useEffect, useState } from "react";
import { ChipMultiSelect, ChipOption } from "@/components/wizard/ChipMultiSelect";
import { useOnboardingStore } from "@/lib/onboarding/store";
import { onboardingClient } from "@/lib/onboarding/client";
import type { GicsSector } from "@/lib/onboarding/types";

export function Step3Sectors() {
  const { sectors, isGeneralist, toggleSector } = useOnboardingStore();
  const [catalogue, setCatalogue] = useState<GicsSector[]>([]);
  const [otherText, setOtherText] = useState("");

  useEffect(() => {
    onboardingClient.listSectors().then(setCatalogue).catch(console.error);
  }, []);

  const options: ChipOption[] = catalogue.map(s => ({
    id: s.id,
    label: s.display_name,
    isOther: s.id === "other",
    isGeneralist: s.id === "generalist",
  }));

  const selected = [
    ...sectors.map(s => s.sector_id),
    ...(isGeneralist ? ["generalist"] : []),
  ];

  return (
    <ChipMultiSelect
      options={options}
      selected={selected}
      customText={otherText}
      maxSelected={3}
      onToggle={toggleSector}
      onCustomTextChange={setOtherText}
    />
  );
}
```

- [ ] **Step 2: Step 4 — Countries**

```tsx
// frontend/src/app/(onboarding)/onboarding/components/Step4Countries.tsx
"use client";
import { useEffect, useState } from "react";
import { ChipMultiSelect, ChipOption } from "@/components/wizard/ChipMultiSelect";
import { useOnboardingStore } from "@/lib/onboarding/store";
import { onboardingClient } from "@/lib/onboarding/client";

export function Step4Countries() {
  const { countries, toggleCountry } = useOnboardingStore();
  const [catalogue, setCatalogue] = useState<{code: string; display_name: string; flag_emoji?: string}[]>([]);
  const [otherText, setOtherText] = useState("");

  useEffect(() => {
    onboardingClient.listCountries().then(setCatalogue).catch(console.error);
  }, []);

  const options: ChipOption[] = catalogue.map(c => ({
    id: c.code,
    label: c.flag_emoji ? `${c.flag_emoji} ${c.display_name}` : c.display_name,
    isOther: c.code === "OTHER",
  }));

  return (
    <ChipMultiSelect
      options={options}
      selected={countries.map(c => c.country_code)}
      customText={otherText}
      onToggle={toggleCountry}
      onCustomTextChange={setOtherText}
    />
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add 'frontend/src/app/(onboarding)/onboarding/components/Step3Sectors.tsx' \
        'frontend/src/app/(onboarding)/onboarding/components/Step4Countries.tsx'
git commit -m "feat(frontend): wizard Steps 3 (Sectors max-3) + 4 (Countries)"
```

---

### Task 25: Step 5 (Themes free-text per sector) + Step 6 (Invite)

**Files:**
- Create: `frontend/src/components/wizard/ThemeInput.tsx`
- Create: `frontend/src/app/(onboarding)/onboarding/components/Step5Themes.tsx`
- Create: `frontend/src/app/(onboarding)/onboarding/components/Step6Invite.tsx`

- [ ] **Step 1: Step 5 — Themes**

```tsx
// frontend/src/app/(onboarding)/onboarding/components/Step5Themes.tsx
"use client";
import { useEffect, useState } from "react";
import { useOnboardingStore } from "@/lib/onboarding/store";
import { onboardingClient } from "@/lib/onboarding/client";
import type { GicsSector, UserTheme } from "@/lib/onboarding/types";

interface SectorBlock {
  sectorId: string | null;        // null = generalist
  label: string;
  inputs: string[];
  placeholder: string;
}

const PLACEHOLDERS: Record<string, string> = {
  semiconductors_eq:  "+ Theme in Semis (e.g. HBM tightness through 2026)",
  software_services:  "+ Theme in Software (e.g. AI pricing power vs. seat-based revenue)",
  utilities:          "+ Theme in Utilities (e.g. data-center power demand)",
  // ... other sectors get a generic placeholder by default
};

export function Step5Themes() {
  const { sectors, isGeneralist, themes, setThemes } = useOnboardingStore();
  const [catalogue, setCatalogue] = useState<GicsSector[]>([]);

  useEffect(() => {
    onboardingClient.listSectors().then(setCatalogue).catch(console.error);
  }, []);

  // Build the per-sector blocks based on what the user picked in step 3
  const blocks: SectorBlock[] = sectors.map(s => {
    const sec = catalogue.find(c => c.id === s.sector_id);
    return {
      sectorId: s.sector_id,
      label: sec?.display_name || s.sector_id,
      inputs: themes.filter(t => t.sector_id === s.sector_id).map(t => t.theme_text),
      placeholder: PLACEHOLDERS[s.sector_id] || `+ Theme in ${sec?.display_name || s.sector_id}`,
    };
  });
  if (isGeneralist) {
    blocks.push({
      sectorId: null,
      label: "Generalist / cross-sector",
      inputs: themes.filter(t => t.sector_id === null).map(t => t.theme_text),
      placeholder: "+ Macro / cross-sector theme (e.g. US-China tech decoupling)",
    });
  }

  function updateTheme(sectorId: string | null, idx: number, text: string) {
    const others = themes.filter(t => t.sector_id !== sectorId);
    const sectorThemes = themes.filter(t => t.sector_id === sectorId);
    const updated = [...sectorThemes];
    updated[idx] = { sector_id: sectorId, theme_text: text, sort_order: idx };
    setThemes([...others, ...updated.filter(t => t.theme_text.trim())]);
  }

  function addTheme(sectorId: string | null) {
    const sectorThemes = themes.filter(t => t.sector_id === sectorId);
    setThemes([
      ...themes,
      { sector_id: sectorId, theme_text: "", sort_order: sectorThemes.length },
    ]);
  }

  const inputCls = "w-full px-3 py-2 rounded-md text-[13px]";
  const inputStyle = { border: "1px solid var(--ag-line)", outline: "none",
                        color: "var(--ag-ink)", background: "var(--ag-card)" };

  return (
    <div className="space-y-5">
      {blocks.map(block => (
        <div key={block.sectorId || "generalist"}>
          <p className="text-[11px] font-semibold tracking-[0.04em] uppercase mb-1.5"
             style={{ color: block.sectorId === null ? "var(--ag-generalist)" : "var(--ag-body)" }}>
            {block.label}
          </p>
          {(block.inputs.length > 0 ? block.inputs : [""]).map((val, idx) => (
            <input
              key={idx}
              value={val}
              placeholder={block.placeholder}
              onChange={(e) => updateTheme(block.sectorId, idx, e.target.value)}
              className={`${inputCls} ${idx > 0 ? "mt-1.5" : ""}`}
              style={inputStyle}
            />
          ))}
          <button
            onClick={() => addTheme(block.sectorId)}
            className="text-[12px] mt-1.5"
            style={{ color: "var(--ag-accent)" }}
          >
            + Add another in {block.label}
          </button>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Step 6 — Invite**

```tsx
// frontend/src/app/(onboarding)/onboarding/components/Step6Invite.tsx
"use client";
import { useState } from "react";
import { useOnboardingStore } from "@/lib/onboarding/store";

export function Step6Invite() {
  const { invitees, setInvitees } = useOnboardingStore();
  const [showMessage, setShowMessage] = useState(false);
  const [message, setMessage] = useState(
    "I'm using AlphaGraph for AI-bottleneck research — Bloomberg-meets-Notion for buyside. Use my link to skip the waitlist."
  );

  const rows = invitees.length > 0 ? invitees : [{ email: "" }, { email: "" }];

  function updateRow(idx: number, email: string) {
    const next = [...rows];
    next[idx] = { ...next[idx], email, message: showMessage ? message : undefined };
    setInvitees(next.filter(r => r.email.trim()));
  }

  function addRow() {
    setInvitees([...rows.filter(r => r.email.trim()), { email: "" }]);
  }

  const inputCls = "w-full px-3 py-2 rounded-md text-[13px]";
  const inputStyle = { border: "1px solid var(--ag-line)", outline: "none",
                        color: "var(--ag-ink)", background: "var(--ag-card)" };

  return (
    <div>
      <div className="bg-gradient-to-br p-5 rounded-lg"
           style={{ background: "linear-gradient(135deg, #eff6ff 0%, #f3e8ff 100%)" }}>
        <p className="text-[14px] font-semibold mb-1" style={{ color: "var(--ag-ink)" }}>
          Know someone who'd love this?
        </p>
        <p className="text-[12px] mb-3" style={{ color: "var(--ag-body)" }}>
          Invite peers who cover semis, AI infra, or buyside research. They jump the waitlist.
        </p>

        <div className="space-y-1.5 bg-white rounded-md p-3">
          {rows.map((row, i) => (
            <input
              key={i}
              value={row.email}
              placeholder="colleague@anywhere.com (personal or work)"
              onChange={(e) => updateRow(i, e.target.value)}
              className={inputCls}
              style={inputStyle}
            />
          ))}
          <button onClick={addRow}
                  className="text-[11px] mt-1"
                  style={{ color: "var(--ag-accent)" }}>+ Add another</button>
        </div>

        <details className="mt-2.5">
          <summary className="text-[11px] cursor-pointer"
                   style={{ color: "var(--ag-body)" }}
                   onClick={() => setShowMessage(s => !s)}>
            ✏️ Personalize the invite message (optional)
          </summary>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={3}
            className="w-full mt-1.5 px-3 py-2 rounded-md text-[12px]"
            style={inputStyle}
          />
        </details>

        <div className="mt-3 px-3 py-2 rounded-md text-[11px] flex items-center gap-1.5"
             style={{ background: "#fef9c3", color: "#854d0e" }}>
          <span>🎁</span>
          <span><strong>Founding-member perk:</strong> invite 3 peers who sign up → priority support + early access for life.</span>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add 'frontend/src/app/(onboarding)/onboarding/components/Step5Themes.tsx' \
        'frontend/src/app/(onboarding)/onboarding/components/Step6Invite.tsx'
git commit -m "feat(frontend): wizard Steps 5 (Themes free-text) + 6 (Invite)"
```

---

### Task 26: Wizard orchestrator page (combines Steps 1-6 + WizardShell)

**Files:**
- Create: `frontend/src/app/(onboarding)/layout.tsx`
- Create: `frontend/src/app/(onboarding)/onboarding/page.tsx`

- [ ] **Step 1: Onboarding layout (minimal)**

```tsx
// frontend/src/app/(onboarding)/layout.tsx
export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  return <div style={{ background: "var(--ag-page)", minHeight: "100vh" }}>{children}</div>;
}
```

- [ ] **Step 2: Wizard orchestrator**

```tsx
// frontend/src/app/(onboarding)/onboarding/page.tsx
"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { WizardShell } from "@/components/wizard/WizardShell";
import { useOnboardingStore } from "@/lib/onboarding/store";
import { useWizardSave } from "@/lib/onboarding/useWizardSave";
import { onboardingClient } from "@/lib/onboarding/client";

import { Step1Role }         from "./components/Step1Role";
import { Step2FirmStrategy } from "./components/Step2FirmStrategy";
import { Step3Sectors }      from "./components/Step3Sectors";
import { Step4Countries }    from "./components/Step4Countries";
import { Step5Themes }       from "./components/Step5Themes";
import { Step6Invite }       from "./components/Step6Invite";

export default function OnboardingPage() {
  const router = useRouter();
  const s = useOnboardingStore();
  useWizardSave();

  // Hydrate from server on mount
  useEffect(() => {
    onboardingClient.getProfile().then(p => {
      s.hydrate({
        currentStep:  p.wizard_current_step,
        role:         { value: p.role as any, customText: p.role_other || "" },
        firmStrategy: { value: p.firm_strategy as any, customText: p.firm_strategy_other || "" },
        isGeneralist: p.is_generalist,
      });
    }).catch(console.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function continueDisabled(): boolean {
    if (s.currentStep === 1) return s.role.value === null || (s.role.value === "other" && !s.role.customText);
    if (s.currentStep === 2) return s.firmStrategy.value === null;
    if (s.currentStep === 3) return s.sectors.length === 0 && !s.isGeneralist;
    if (s.currentStep === 4) return s.countries.length === 0;
    return false;
  }

  async function handleContinue() {
    if (s.currentStep < 6) {
      s.nextStep();
      return;
    }
    // Step 6 → finish
    await onboardingClient.finishWizard({
      sectors: [
        ...s.sectors,
        ...(s.isGeneralist ? [{ sector_id: "generalist" }] : []),
      ],
      countries: s.countries,
      themes:    s.themes.filter(t => t.theme_text.trim()),
      invitees:  s.invitees.filter(i => i.email.trim()),
    });
    router.push("/dashboard");
  }

  async function handleSkip() {
    if (s.currentStep === 5 || s.currentStep === 6) {
      // Skip from step 5/6 onwards = complete the wizard with current state
      // OR skip the entire wizard
      if (confirm("Skip the rest of onboarding? You can complete it later in Settings.")) {
        await onboardingClient.skipWizard();
        router.push("/dashboard");
      }
    }
  }

  const titles = {
    1: { title: "What's your role?",                       sub: "Shapes whether your dashboard leans toward fundamentals, summary, or distillation." },
    2: { title: "What strategy does your firm run?",       sub: "Signals tier eligibility and which features to surface first." },
    3: { title: "Which sectors do you cover?",             sub: "Up to 3 — or pick Generalist for broader coverage." },
    4: { title: "Which markets do you invest in?",         sub: "Drives which exchanges, calendars, and news feeds we surface." },
    5: { title: "Which themes are on top of your mind now?", sub: "Type the themes you watch most, organized by sector." },
    6: { title: "Help shape AlphaGraph",                   sub: "Invite peers who'd find this useful." },
  } as const;
  const meta = titles[s.currentStep as keyof typeof titles];

  return (
    <WizardShell
      step={s.currentStep}
      total={6}
      title={meta.title}
      subtitle={meta.sub}
      onBack={s.currentStep > 1 ? s.prevStep : undefined}
      onContinue={handleContinue}
      onSkip={(s.currentStep === 5 || s.currentStep === 6) ? handleSkip : undefined}
      continueDisabled={continueDisabled()}
    >
      {s.currentStep === 1 && <Step1Role />}
      {s.currentStep === 2 && <Step2FirmStrategy />}
      {s.currentStep === 3 && <Step3Sectors />}
      {s.currentStep === 4 && <Step4Countries />}
      {s.currentStep === 5 && <Step5Themes />}
      {s.currentStep === 6 && <Step6Invite />}
    </WizardShell>
  );
}
```

- [ ] **Step 3: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

Expected: `/onboarding` route appears in the build output.

```bash
git add 'frontend/src/app/(onboarding)/'
git commit -m "feat(frontend): onboarding wizard orchestrator (combines Steps 1-6)"
```

---

## Phase I — Post-wizard surfaces

### Task 27: UserMenu in dashboard top-right

**Files:**
- Create: `frontend/src/app/(dashboard)/components/UserMenu.tsx`
- Modify: `frontend/src/app/(dashboard)/layout.tsx` (mount UserMenu)

- [ ] **Step 1: UserMenu component**

```tsx
// frontend/src/app/(dashboard)/components/UserMenu.tsx
"use client";
import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { API_BASE_URL } from "@/lib/api/base";

interface MeResponse {
  email: string;
  name?: string;
  picture?: string;
  admin_role?: string;
}

export function UserMenu() {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API_BASE_URL}/auth/me`, { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  // Close on outside click
  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  if (!me) {
    return (
      <Link href="/signin" className="text-[13px] px-3 py-1.5 rounded-md border"
            style={{ borderColor: "var(--ag-line)", color: "var(--ag-body)" }}>
        Sign in
      </Link>
    );
  }

  const initials = (me.name || me.email).split(/[ @.]/).filter(Boolean)
                    .slice(0, 2).map(s => s[0]).join("").toUpperCase();

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(o => !o)}
              className="flex items-center gap-2 text-[13px] px-2 py-1 rounded-md hover:bg-slate-50">
        <span className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-medium"
              style={{ background: "var(--ag-accent-fill)", color: "var(--ag-accent-text)" }}>
          {initials}
        </span>
        <span style={{ color: "var(--ag-body)" }}>{me.name || me.email}</span>
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-56 bg-white border rounded-md shadow-lg py-1"
             style={{ borderColor: "var(--ag-line)" }}>
          <div className="px-3 py-2 text-[12px] truncate" style={{ color: "var(--ag-muted)" }}>{me.email}</div>
          <div className="border-t" style={{ borderColor: "var(--ag-hairline)" }} />
          <Link href="/settings/profile"
                className="block px-3 py-2 text-[13px] hover:bg-slate-50"
                style={{ color: "var(--ag-body)" }}>Settings</Link>
          {me.admin_role === "admin" && (
            <Link href="/admin/waitlist"
                  className="block px-3 py-2 text-[13px] hover:bg-slate-50"
                  style={{ color: "var(--ag-body)" }}>Admin · Waitlist</Link>
          )}
          <div className="border-t" style={{ borderColor: "var(--ag-hairline)" }} />
          <form action={`${API_BASE_URL}/auth/logout`} method="POST">
            <button type="submit"
                    className="block w-full text-left px-3 py-2 text-[13px] hover:bg-slate-50"
                    style={{ color: "var(--ag-body)" }}>
              Sign out
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Mount in dashboard layout**

In `frontend/src/app/(dashboard)/layout.tsx`, find the existing top bar (or root div) and add the UserMenu in the top-right. Example pattern (adapt to existing layout structure):

```tsx
import { UserMenu } from "./components/UserMenu";

// In the layout JSX, somewhere in the top bar:
<div className="flex justify-between items-center h-14 px-6 border-b"
     style={{ borderColor: "var(--ag-line)" }}>
  <div>{/* existing left-side nav / logo */}</div>
  <UserMenu />
</div>
```

- [ ] **Step 3: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -5
git add 'frontend/src/app/(dashboard)/components/UserMenu.tsx' 'frontend/src/app/(dashboard)/layout.tsx'
git commit -m "feat(frontend): UserMenu in dashboard top-right with admin link"
```

---

### Task 28: Settings → Profile page (edit wizard answers later)

**Files:**
- Create: `frontend/src/app/(dashboard)/settings/profile/page.tsx`

- [ ] **Step 1: Implement settings page (reuses wizard primitives)**

```tsx
// frontend/src/app/(dashboard)/settings/profile/page.tsx
"use client";
import { useEffect, useState } from "react";
import { onboardingClient } from "@/lib/onboarding/client";
import type { UserProfile, GicsSector } from "@/lib/onboarding/types";
import { ChipMultiSelect, ChipOption } from "@/components/wizard/ChipMultiSelect";

export default function SettingsProfilePage() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [sectors, setSectors] = useState<GicsSector[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([
      onboardingClient.getProfile(),
      onboardingClient.listSectors(),
    ]).then(([p, s]) => {
      setProfile(p);
      setSectors(s);
    });
  }, []);

  async function save(patch: Partial<UserProfile>) {
    setSaving(true);
    try {
      const updated = await onboardingClient.putProfile(patch);
      setProfile(updated);
    } finally {
      setSaving(false);
    }
  }

  if (!profile) return <div className="p-8 text-[13px]" style={{ color: "var(--ag-muted)" }}>Loading…</div>;

  return (
    <main className="max-w-[640px] mx-auto px-6 py-10">
      <h1 className="text-[24px] font-semibold tracking-[-0.012em]"
          style={{ color: "var(--ag-ink)" }}>Profile settings</h1>
      <p className="mt-1 text-[13px]" style={{ color: "var(--ag-muted)" }}>
        Edit the answers you gave during onboarding. Changes take effect on your next dashboard refresh.
      </p>

      <section className="mt-8">
        <h2 className="text-[14px] font-semibold mb-2" style={{ color: "var(--ag-ink)" }}>Role</h2>
        <select value={profile.role || ""}
                onChange={(e) => save({ role: e.target.value as any })}
                disabled={saving}
                className="px-3 py-2 rounded-md text-[13px] w-full"
                style={{ border: "1px solid var(--ag-line)", outline: "none", background: "var(--ag-card)" }}>
          <option value="">—</option>
          <option value="buyside_analyst">Buyside Analyst</option>
          <option value="buyside_pm">Buyside Portfolio Manager</option>
          <option value="sell_side">Sell-side Analyst</option>
          <option value="wealth_manager">Wealth Manager / RIA</option>
          <option value="other">Other</option>
        </select>
      </section>

      <section className="mt-8">
        <h2 className="text-[14px] font-semibold mb-2" style={{ color: "var(--ag-ink)" }}>Generalist mode</h2>
        <label className="flex items-center gap-2 text-[13px]" style={{ color: "var(--ag-body)" }}>
          <input type="checkbox" checked={profile.is_generalist}
                 onChange={(e) => save({ is_generalist: e.target.checked })} />
          Subscribe to all sectors
        </label>
      </section>

      <p className="mt-8 text-[12px]" style={{ color: "var(--ag-subtle)" }}>
        For full sector / country / theme editing UI, click <a href="/onboarding" style={{ color: "var(--ag-accent)" }}>re-run the wizard</a> — it'll pre-fill your existing choices.
      </p>
    </main>
  );
}
```

This is intentionally a small surface for v1. Full sector/country/theme editing here is post-MVP work; pointing users back to the wizard is the pragmatic shortcut.

- [ ] **Step 2: Commit**

```bash
git add 'frontend/src/app/(dashboard)/settings/profile/page.tsx'
git commit -m "feat(frontend): settings → profile (edit role, generalist toggle; full editor TODO)"
```

---

### Task 29: Admin waitlist queue page

**Files:**
- Create: `frontend/src/app/(admin)/layout.tsx`
- Create: `frontend/src/app/(admin)/admin/waitlist/page.tsx`

- [ ] **Step 1: Admin layout (re-uses dashboard chrome)**

```tsx
// frontend/src/app/(admin)/layout.tsx
import { UserMenu } from "../(dashboard)/components/UserMenu";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ background: "var(--ag-page)", minHeight: "100vh" }}>
      <div className="flex justify-between items-center h-14 px-6 border-b"
           style={{ borderColor: "var(--ag-line)" }}>
        <span className="text-[14px] font-semibold" style={{ color: "var(--ag-ink)" }}>
          AlphaGraph Admin
        </span>
        <UserMenu />
      </div>
      {children}
    </div>
  );
}
```

- [ ] **Step 2: Waitlist queue page**

```tsx
// frontend/src/app/(admin)/admin/waitlist/page.tsx
"use client";
import { useEffect, useState } from "react";
import { onboardingClient } from "@/lib/onboarding/client";
import type { WaitlistEntry, WaitlistStatus } from "@/lib/onboarding/types";

const STATUSES: { value: WaitlistStatus | "all"; label: string }[] = [
  { value: "pending",            label: "Pending"            },
  { value: "approved",           label: "Approved"           },
  { value: "self_serve_attempt", label: "Self-serve attempts" },
  { value: "rejected",           label: "Rejected"           },
  { value: "all",                label: "All"                },
];

export default function AdminWaitlistPage() {
  const [filter, setFilter] = useState<WaitlistStatus | "all">("pending");
  const [entries, setEntries] = useState<WaitlistEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState<string | null>(null);

  async function reload() {
    setLoading(true);
    try {
      const r = await onboardingClient.listWaitlist(filter === "all" ? undefined : filter);
      setEntries(r.entries);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); }, [filter]);  // eslint-disable-line

  async function approve(id: string) {
    setActing(id);
    try {
      await onboardingClient.approveEntry(id);
      await reload();
    } finally { setActing(null); }
  }

  async function reject(id: string) {
    const reason = prompt("Optional reason:") || undefined;
    setActing(id);
    try {
      await onboardingClient.rejectEntry(id, reason);
      await reload();
    } finally { setActing(null); }
  }

  return (
    <main className="max-w-[1100px] mx-auto px-6 py-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-[22px] font-semibold tracking-[-0.012em]"
            style={{ color: "var(--ag-ink)" }}>Waitlist queue</h1>
        <select value={filter} onChange={(e) => setFilter(e.target.value as any)}
                className="px-3 py-1.5 rounded-md text-[13px]"
                style={{ border: "1px solid var(--ag-line)", background: "var(--ag-card)" }}>
          {STATUSES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </div>

      {loading && <p className="text-[13px]" style={{ color: "var(--ag-muted)" }}>Loading…</p>}

      <div className="bg-white border rounded-ag-card overflow-hidden"
           style={{ borderColor: "var(--ag-line)" }}>
        <table className="w-full text-[13px]">
          <thead style={{ background: "var(--ag-page)" }}>
            <tr>
              <th className="text-left px-4 py-2 font-medium" style={{ color: "var(--ag-muted)" }}>Email</th>
              <th className="text-left px-4 py-2 font-medium" style={{ color: "var(--ag-muted)" }}>Name</th>
              <th className="text-left px-4 py-2 font-medium" style={{ color: "var(--ag-muted)" }}>Role</th>
              <th className="text-left px-4 py-2 font-medium" style={{ color: "var(--ag-muted)" }}>Firm</th>
              <th className="text-left px-4 py-2 font-medium" style={{ color: "var(--ag-muted)" }}>Status</th>
              <th className="text-right px-4 py-2 font-medium" style={{ color: "var(--ag-muted)" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(e => (
              <tr key={e.id} className="border-t" style={{ borderColor: "var(--ag-hairline)" }}>
                <td className="px-4 py-2 font-mono text-[12px]">{e.email}</td>
                <td className="px-4 py-2">{e.full_name || "—"}</td>
                <td className="px-4 py-2">{e.self_reported_role || "—"}</td>
                <td className="px-4 py-2">{e.self_reported_firm || "—"}</td>
                <td className="px-4 py-2">
                  <span className="px-2 py-0.5 rounded text-[11px]"
                        style={{
                          background: e.status === "approved" ? "#dcfce7"
                                     : e.status === "pending" ? "#fef3c7"
                                     : e.status === "self_serve_attempt" ? "#dbeafe"
                                     : "#fee2e2",
                          color: e.status === "approved" ? "#166534"
                                 : e.status === "pending" ? "#854d0e"
                                 : e.status === "self_serve_attempt" ? "#1e40af"
                                 : "#991b1b",
                        }}>
                    {e.status}
                  </span>
                </td>
                <td className="px-4 py-2 text-right">
                  {e.status !== "approved" && (
                    <button onClick={() => approve(e.id)} disabled={acting === e.id}
                            className="text-[12px] mr-2 px-2 py-1 rounded"
                            style={{ background: "var(--ag-accent)", color: "#fff" }}>
                      Approve
                    </button>
                  )}
                  {e.status !== "rejected" && (
                    <button onClick={() => reject(e.id)} disabled={acting === e.id}
                            className="text-[12px] px-2 py-1 rounded border"
                            style={{ borderColor: "var(--ag-line)", color: "var(--ag-body)" }}>
                      Reject
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {entries.length === 0 && !loading && (
              <tr><td colSpan={6} className="text-center px-4 py-8"
                      style={{ color: "var(--ag-muted)" }}>No entries</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}
```

- [ ] **Step 3: Build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -5
git add 'frontend/src/app/(admin)/'
git commit -m "feat(frontend): admin waitlist queue with approve/reject actions"
```

---

## Phase J — End-to-end smoke + finish

### Task 30: Playwright e2e + production deploy verify

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/tests/e2e/onboarding.spec.ts`
- Modify: `frontend/package.json` (add playwright deps)
- Modify: `roadmap_v1.md` (mark Tranche 1 complete)

- [ ] **Step 1: Install Playwright**

```bash
cd /c/Users/Sharo/AI_projects/AlphaGraph_new/frontend && npm install -D @playwright/test playwright
npx playwright install chromium
```

- [ ] **Step 2: Playwright config**

```typescript
// frontend/playwright.config.ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:3000",
  },
});
```

- [ ] **Step 3: E2E test for the waitlist + landing journey**

```typescript
// frontend/tests/e2e/onboarding.spec.ts
import { test, expect } from "@playwright/test";

test("landing page renders + waitlist form is reachable", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "AlphaGraph" })).toBeVisible();
  await page.getByRole("link", { name: /Request access/ }).click();
  await expect(page).toHaveURL(/\/waitlist/);
  await expect(page.getByRole("heading", { name: "Request access" })).toBeVisible();
});

test("submitting waitlist routes to thanks page", async ({ page }) => {
  await page.goto("/waitlist");
  // Use a randomized email to avoid duplicate-email issues across runs
  const email = `e2e-${Date.now()}@example.com`;
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="full_name"]', "E2E Tester");
  await page.fill('input[name="role"]', "Buyside Analyst");
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(/\/waitlist\/thanks/);
});

test("signin page shows OAuth buttons", async ({ page }) => {
  await page.goto("/signin");
  await expect(page.getByText(/Sign in with Google/)).toBeVisible();
  await expect(page.getByText(/Sign in with Microsoft/)).toBeVisible();
});

test("middleware redirects unauthenticated to signin from /dashboard", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/signin/);
});
```

- [ ] **Step 4: Add npm script + run tests locally**

In `frontend/package.json` scripts section, add:

```json
"test:e2e": "playwright test"
```

Run:

```bash
# In one terminal:
cd frontend && npm run dev

# In another terminal (with backend also running):
cd frontend && npm run test:e2e
```

Expected: 4 tests pass.

- [ ] **Step 5: Deploy + verify on production URLs**

After pushing all phases:

```bash
git push origin main
```

- Vercel auto-builds; verify the new routes load: `https://alpha-graph-new.vercel.app/`, `/waitlist`, `/signin`.
- Render auto-builds; SSH into Render Shell and run `alembic upgrade head` to apply migration 0006.
- Run the seed admin user script: `python -m backend.scripts.seed_admin_user --email sharonyoutube1@gmail.com`.
- Sign in via Google → expect redirect to `/onboarding`.
- Complete the wizard → expect redirect to `/dashboard`.
- Visit `/admin/waitlist` (as admin) → expect the queue with the smoke-test applicant.

- [ ] **Step 6: Update roadmap + final commit**

In `roadmap_v1.md`, append to the Recent changes log:

```markdown
### 2026-04-30 (later)
- Tranche 1 (User onboarding) implemented per
  `docs/superpowers/specs/2026-04-30-tranche1-user-onboarding-design.md` and
  `docs/superpowers/plans/2026-04-30-tranche1-user-onboarding.md`.
- 6 new tables (waitlist_entry, user_profile, gics_sector, user_sector,
  user_country, user_theme) + ALTER app_user.admin_role.
- 12 new endpoints under /public, /admin, /me, /sectors, /countries.
- 6-step Linear-light wizard with Zustand state + per-step PUT /me/profile.
- Wizard finalize auto-subscribes to thesis groups via sector→group mapping.
- Resend transactional email with dev fallback (logs instead of sends when
  RESEND_API_KEY is missing).
- Founding-member referrals auto-approve invitees.
- All Phase 2 schema migrations 0001–0006 applied to Neon prod Postgres.
```

```bash
git add frontend/playwright.config.ts frontend/tests/e2e/ frontend/package.json frontend/package-lock.json roadmap_v1.md
git commit -m "feat(e2e): Playwright onboarding tests + roadmap update for Tranche 1 complete"
git push origin main
```

---

## Self-review checklist

- [ ] Spec § 4 data model — covered by Tasks 1–2
- [ ] Spec § 5 API surface — covered by Tasks 7–14
- [ ] Spec § 6 frontend components — covered by Tasks 17–29
- [ ] Spec § 7 email infrastructure — covered by Tasks 5–6
- [ ] Spec § 8 authorization — covered by Task 3
- [ ] Spec § 11 success criteria — verified by Task 30
- [ ] Spec § 13 migration path — covered by Task 2 + Task 30 step 5
- [ ] No placeholders in any task body
- [ ] Type names consistent (`UserProfile`, `WaitlistEntry`, `GicsSector` everywhere)
- [ ] Function names consistent (`require_admin`, `check_waitlist_status`, `finalize_wizard`, `sector_to_universe_groups`)

---

## Estimated effort

| Phase | Tasks | Est. hours |
|---|---|---|
| A — Database | 1–4 | 4 |
| B — Email | 5–6 | 2 |
| C — Public + admin waitlist API | 7–10 | 6 |
| D — Profile + wizard API | 11–14 | 8 |
| E — OAuth integration | 15–16 | 3 |
| F — Frontend design system | 17–18 | 4 |
| G — Public pages + signin | 19–21 | 5 |
| H — Wizard frontend | 22–26 | 12 |
| I — Post-wizard surfaces | 27–29 | 6 |
| J — E2E + finish | 30 | 4 |
| **Total** | **30 tasks** | **~54 hours** |

Roughly 7 working days at 8 hours/day, or 9–10 calendar days for a solo dev. Matches the 1.5-week Tranche 1 estimate from the spec.

---

## Out-of-scope reminder (will NOT be done in this plan)

Per the spec § 10:
- Tier model (free / pro / VIP) — Tranche 2
- Stripe billing — Tranche 2
- Workspace / team model — Tranche 3
- Theme universe with cross-references — Tranche 3
- Magic-link email auth — only when a pilot can't OAuth
- Companies/coverage list — future onboarding agent









