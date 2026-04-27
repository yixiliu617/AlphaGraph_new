from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from backend.app.api.routers.v1 import ingest, chat, ledger, topology, insights, notes, data, earnings, research, pricing, prices, social, taiwan, tsmc, umc, mediatek, admin, calendar
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.on_event("startup")
async def startup_event():
    init_db()


@app.get("/")
async def root():
    return {"message": "Welcome to the AlphaGraph Institutional API", "version": API_VERSION}
