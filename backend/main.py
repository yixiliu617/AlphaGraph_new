import os
from pathlib import Path
# Load .env into os.environ BEFORE importing anything else that reads
# env vars. pydantic-settings loads .env into Settings attrs, but doesn't
# propagate to os.environ — modules like backend.app.services.auth.encryption
# read os.environ directly (intentionally, so tests can monkeypatch), so
# they need this. Idempotent: dotenv won't overwrite already-set env vars.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
except ImportError:
    # python-dotenv missing in this env — fall back to manual parse.
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.middleware.sessions import SessionMiddleware
from backend.app.api.routers.v1 import ingest, chat, ledger, topology, insights, notes, data, earnings, research, pricing, prices, social, taiwan, tsmc, umc, mediatek, admin, calendar, auth, connections, me_calendar, me_notes
from backend.app.core.config import settings
from backend.app.db.session import init_db

# Increment this when a breaking change is made to any API contract.
# The frontend reads the X-API-Version header to detect mismatches.
API_VERSION = "1.0.0"

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Starlette session middleware — required by Authlib's OAuth flow to
# persist the OIDC nonce + state across the redirect to the IdP and back.
# This middleware uses its own short-lived signed cookie (`session`) that's
# separate from our long-lived auth cookie (`ag_session`); only the OAuth
# handshake reads it. Secret is the same SECRET_KEY used for the JWT.
# ---------------------------------------------------------------------------
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site=settings.SESSION_COOKIE_SAMESITE,
    https_only=settings.SESSION_COOKIE_SECURE,
    max_age=600,  # 10 minutes is plenty for an OAuth round-trip
)

# ---------------------------------------------------------------------------
# API Version Header
# Injected on every response so the frontend can assert it hasn't drifted.
# When you bump API_VERSION, update the assertion in frontend/src/lib/api/base.ts.
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_api_version_header(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-API-Version"] = API_VERSION
    return response

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(ingest.router,   prefix=f"{settings.API_V1_STR}/ingest",   tags=["ingestion"])
app.include_router(chat.router,     prefix=f"{settings.API_V1_STR}/chat",     tags=["chat"])
app.include_router(ledger.router,   prefix=f"{settings.API_V1_STR}/ledger",   tags=["ledger"])
app.include_router(topology.router, prefix=f"{settings.API_V1_STR}/topology", tags=["topology"])
app.include_router(insights.router, prefix=f"{settings.API_V1_STR}/insights", tags=["insights"])
app.include_router(notes.router,   prefix=f"{settings.API_V1_STR}/notes",   tags=["notes"])
app.include_router(earnings.router, prefix=f"{settings.API_V1_STR}/earnings", tags=["earnings"])
app.include_router(calendar.router, prefix=f"{settings.API_V1_STR}/calendar", tags=["calendar"])
app.include_router(research.router, prefix=f"{settings.API_V1_STR}/research", tags=["research"])
app.include_router(data.router,    prefix=f"{settings.API_V1_STR}",         tags=["data"])
app.include_router(pricing.router, prefix=f"{settings.API_V1_STR}/pricing", tags=["pricing"])
app.include_router(prices.router,  prefix=f"{settings.API_V1_STR}/prices",  tags=["prices"])
app.include_router(social.router,  prefix=f"{settings.API_V1_STR}/social",  tags=["social"])
app.include_router(taiwan.router,  prefix=f"{settings.API_V1_STR}/taiwan",  tags=["taiwan"])
app.include_router(tsmc.router,    prefix=f"{settings.API_V1_STR}/tsmc",    tags=["tsmc"])
app.include_router(umc.router,     prefix=f"{settings.API_V1_STR}/umc",     tags=["umc"])
app.include_router(mediatek.router,prefix=f"{settings.API_V1_STR}/mediatek",tags=["mediatek"])
app.include_router(admin.router,   prefix=f"{settings.API_V1_STR}/admin",   tags=["admin"])
app.include_router(auth.router,        prefix=f"{settings.API_V1_STR}/auth",        tags=["auth"])
app.include_router(connections.router, prefix=f"{settings.API_V1_STR}/connections", tags=["connections"])
app.include_router(me_calendar.router, prefix=f"{settings.API_V1_STR}/me/calendar", tags=["me-calendar"])
app.include_router(me_notes.router,    prefix=f"{settings.API_V1_STR}/me/notes",    tags=["me-notes"])


@app.on_event("startup")
async def startup_event():
    init_db()


@app.get("/")
async def root():
    return {"message": "Welcome to the AlphaGraph Institutional API", "version": API_VERSION}


@app.get("/healthz")
async def healthz():
    """Liveness probe for Render / load balancers / uptime monitors.

    Returns 200 with the API version. Intentionally NOT a deep health
    check — no DB ping — because we want load-balancer health checks to
    decouple from transient DB issues. A separate `/readyz` would be
    where to add a DB ping if/when ready-check semantics are needed.
    """
    return {"status": "ok", "version": API_VERSION}
