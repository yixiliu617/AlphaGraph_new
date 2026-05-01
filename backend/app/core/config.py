from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    """
    Configuration management for the AlphaGraph Backend.
    Values can be overridden by environment variables.
    """
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    PROJECT_NAME: str = "AlphaGraph Institutional Platform"
    API_V1_STR: str = "/api/v1"

    # AI & LLM
    # ACTIVE_LLM   — general-purpose LLM for extraction pipeline and embeddings.
    # ENGINE_LLM   — LLM used by the Engine agent for tool-use reasoning.
    #                Options: "anthropic" (default) | "gemini" | "openai"
    # ENGINE_MODEL — optional model override (e.g. "gpt-4o-mini", "gemini-2.0-flash").
    #                When blank, each adapter uses its own sensible default.
    ACTIVE_LLM:    str           = "gemini"
    ENGINE_LLM:    str           = "anthropic"
    ENGINE_MODEL:  Optional[str] = None
    GEMINI_API_KEY:    Optional[str] = None
    OPENAI_API_KEY:    Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None

    # ---- Email (Resend) ----
    # Set RESEND_API_KEY for prod. Dev: leave blank → emails are logged not sent.
    RESEND_API_KEY: Optional[str] = None
    EMAIL_FROM: str = "AlphaGraph <noreply@alphagraph.com>"
    ADMIN_EMAIL_BCC: Optional[str] = None  # Sharon's email; BCC'd on every waitlist email

    # Vector Database
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_INDEX_NAME: str = "alphagraph-v1"

    # Relational & State Database (Default to local SQLite for easy testing)
    POSTGRES_URI: str = "sqlite:///./alphagraph.db"

    # Graph Database
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"

    # Quant Data Lake
    DUCKDB_PATH: str = "backend/data/parquet/"

    # Security
    SECRET_KEY: str = "SUPER_SECRET_KEY_REPLACE_IN_PROD"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days

    # ---- Phase 2 — Auth (OAuth + sessions) ---------------------------
    # Phase 2 tables (app_user, oauth_session, user_alert) live in a
    # separate database from the legacy Fragment/Insight/etc. tables so
    # the existing app can stay on SQLite while auth uses Postgres.
    # Defaults to POSTGRES_URI; override only if the auth DB is separate
    # (e.g. legacy on SQLite, auth on Postgres during Phase 2 migration).
    AUTH_DATABASE_URI: Optional[str] = None  # falls back to POSTGRES_URI

    # OAuth — Google
    GOOGLE_OAUTH_CLIENT_ID:     Optional[str] = None
    GOOGLE_OAUTH_CLIENT_SECRET: Optional[str] = None

    # OAuth — Microsoft (Entra / Azure AD; "common" tenant supports both
    # personal MSA and work/school accounts).
    MICROSOFT_OAUTH_CLIENT_ID:     Optional[str] = None
    MICROSOFT_OAUTH_CLIENT_SECRET: Optional[str] = None
    MICROSOFT_OAUTH_TENANT:        str           = "common"

    # OAuth redirect base — what the IdP redirects back to after consent.
    # Dev: localhost:8000. Prod: your AWS / fly host.
    OAUTH_REDIRECT_BASE: str = "http://localhost:8000"

    # Frontend URL the auth callback redirects to after a successful sign-in.
    FRONTEND_URL: str = "http://localhost:3000"

    # CORS origins — comma-separated. The middleware reads this and
    # falls back to localhost:3000/3001 if unset, so dev keeps working
    # without explicit config. In prod set to e.g.
    #   CORS_ORIGINS=https://alphagraph.vercel.app,https://alphagraph.com
    # Vercel preview deploys get unique URLs per PR, so use
    # `CORS_ORIGIN_REGEX` for wildcard matching against preview domains.
    CORS_ORIGINS:      str           = "http://localhost:3000,http://localhost:3001"
    CORS_ORIGIN_REGEX: Optional[str] = None  # e.g. ^https://alphagraph-.*\.vercel\.app$

    # Session cookie — HttpOnly + SameSite=Lax. Dev defaults to non-Secure
    # so http://localhost works; prod sets SESSION_COOKIE_SECURE=true.
    SESSION_COOKIE_NAME:     str  = "ag_session"
    SESSION_COOKIE_SECURE:   bool = False
    SESSION_COOKIE_SAMESITE: str  = "lax"
    SESSION_COOKIE_DOMAIN:   Optional[str] = None  # None => host-only cookie

    JWT_ALGORITHM: str = "HS256"

    # Fernet key for service-credential token encryption. Falls through
    # to TOKEN_ENCRYPTION_KEY env var (also TOKEN_ENCRYPTION_KEYS for
    # rotation). pydantic-settings reads it via env, but we don't expose
    # it as a typed Settings attribute because we want the encryption
    # module to read it directly from env (so tests can monkeypatch).
    # See `backend/app/services/auth/encryption.py`.

    @property
    def auth_db_uri(self) -> str:
        """Resolves AUTH_DATABASE_URI with POSTGRES_URI as the fallback."""
        return self.AUTH_DATABASE_URI or self.POSTGRES_URI

settings = Settings()
