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

settings = Settings()
