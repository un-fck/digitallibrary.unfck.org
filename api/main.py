"""UN Digital Library Public API — FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import close_pool, init_pool
from api.deps import check_rate
from api.routers import api_keys, documents, facets, marcxml, search, stats

DESCRIPTION = """\
Public API for the UN Digital Library — providing access to 767K+ United Nations
documents including resolutions, reports, meeting records, and more.

**Anonymous access** is available at 10 requests/minute for exploration.
[Sign up for a free API key](/developer) for higher limits (60 req/min).

## Authentication

Include your API key in requests using one of:
- Header: `Authorization: Bearer undl_live_xxxx`
- Query: `?api_key=undl_live_xxxx`

## Rate Limits

| Tier | Requests/min | Daily |
|------|-------------|-------|
| Anonymous | 10 | 100 |
| Free | 60 | 10,000 |
| Research | 300 | 100,000 |
| Institutional | 1,000 | Unlimited |
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_pool()
    except Exception as e:
        import warnings
        warnings.warn(f"Database pool failed to initialize: {e}. API will start but DB-dependent endpoints will fail.")
    yield
    await close_pool()


app = FastAPI(
    title="UN Digital Library API",
    description=DESCRIPTION,
    version="1.0.0",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
    openapi_url="/v1/openapi.json",
    lifespan=lifespan,
)

# CORS — public API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization"],
)

# Mount routers — all /v1 routes are rate-limited via PostgreSQL
_rate_deps = [Depends(check_rate)]
app.include_router(marcxml.router, prefix="/v1", dependencies=_rate_deps)
app.include_router(documents.router, prefix="/v1", dependencies=_rate_deps)
app.include_router(search.router, prefix="/v1", dependencies=_rate_deps)
app.include_router(stats.router, prefix="/v1", dependencies=_rate_deps)
app.include_router(facets.router, prefix="/v1", dependencies=_rate_deps)
app.include_router(api_keys.router, prefix="/v1/keys", dependencies=_rate_deps)


@app.get("/v1/", tags=["meta"])
async def api_root():
    """API root — links to available endpoints."""
    return {
        "name": "UN Digital Library API",
        "version": "1.0.0",
        "docs": "/v1/docs",
        "endpoints": {
            "documents": "/v1/documents",
            "marcxml": "/v1/marcxml",
            "search": "/v1/search",
            "facets": "/v1/facets",
            "stats": "/v1/stats",
            "keys": "/v1/keys",
        },
    }
