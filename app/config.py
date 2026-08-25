"""
Application configuration.

All configuration is loaded from environment variables (optionally via a
local .env file during development). Nothing is hard-coded and nothing
is persisted to disk at runtime (except the transient local file used
by the legacy /tableau download endpoints, which is deleted immediately
after upload to Azure Blob -- same behavior as the original backend).
"""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Centralized runtime configuration, read once from the environment."""

    # --- Tableau Server / Cloud ---
    TABLEAU_SERVER: str = os.getenv("TABLEAU_SERVER", "").rstrip("/")
    API_VERSION: str = os.getenv("TABLEAU_API_VERSION", "3.21")
    METADATA_API_PATH: str = os.getenv(
        "TABLEAU_METADATA_API_PATH", "/api/metadata/graphql"
    )

    # --- OpenAI ---
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.1"))
    OPENAI_MAX_TOKENS: int = int(os.getenv("OPENAI_MAX_TOKENS", "4000"))

    # --- App ---
    APP_NAME: str = os.getenv("APP_NAME", "Tableau Pre-Migration Analysis Platform")
    APP_ENV: str = os.getenv("APP_ENV", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    REQUEST_TIMEOUT_SECONDS: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))

    # --- CORS ---
    CORS_ALLOW_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOW_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080"
        ).split(",")
        if origin.strip()
    ]

    # --- Legacy /tableau routes: local scratch dir + Azure Blob (unchanged
    # from the original tableau_backend; used only by download endpoints) ---
    DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "./_tableau_downloads")
    AZURE_CONNECTION_STRING: str = os.getenv("AZURE_CONNECTION_STRING", "")
    AZURE_CONTAINER_NAME: str = os.getenv("AZURE_CONTAINER_NAME", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Convenience module-level accessors (kept for compatibility with the
# authentication snippet, which imports these names directly).
_settings = get_settings()
TABLEAU_SERVER = _settings.TABLEAU_SERVER
API_VERSION = _settings.API_VERSION
