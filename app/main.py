"""
FastAPI application entrypoint.

Tableau Pre-Migration Analysis Platform (integrated build)
------------------------------------------------------------
Single FastAPI application that combines:

  * The original `tableau_backend` functionality (Tableau sign-in,
    projects/workbooks/views/datasources listing, workbook + datasource
    downloads to Azure Blob) -- exposed unchanged under the `/tableau`
    prefix so the existing frontend keeps working without modification.

  * The new Pre-Migration AI Analysis platform (Tableau discovery,
    AI-powered analysis, and Fabric/Power BI migration) -- exposed
    under `/auth`, `/discovery`, `/analysis`, `/analyze`, and
    `/migration`.

This app does NOT migrate anything by default. Discovery/analysis only
discover and analyze; `/migration/*` is opt-in and separate.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import analysis, auth, discovery, migration, orchestration, tableau_legacy
from app.config import get_settings

logging.basicConfig(level=getattr(logging, get_settings().LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Integrated backend: existing Tableau REST/Azure-Blob functionality "
        "(under /tableau) plus the Pre-Migration AI Analysis platform "
        "(discovery, AI analysis, and optional Fabric migration). No "
        "discovery/analysis data is persisted; the legacy /tableau routes "
        "keep an in-memory session token exactly as before."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception while processing %s %s", request.method, request.url)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


# Existing (legacy) Tableau functionality -- unchanged routes/behavior.
app.include_router(tableau_legacy.router)

# New Pre-Migration AI Analysis platform.
app.include_router(auth.router)
app.include_router(discovery.router)
app.include_router(analysis.router)
app.include_router(orchestration.router)
app.include_router(migration.router)


@app.get("/", tags=["Health"])
async def root() -> dict[str, str]:
    return {"status": "ok", "service": settings.APP_NAME}


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "healthy"}
