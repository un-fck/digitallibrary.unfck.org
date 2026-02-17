"""Facets endpoint — aggregated counts for filtering."""

from __future__ import annotations

from fastapi import APIRouter, Query

from api.deps import DBConn, OptionalKey
from api.services import document_service as svc

router = APIRouter(tags=["metadata"])


@router.get("/facets")
async def document_facets(
    conn: DBConn,
    _key: OptionalKey,
    q: str | None = Query(None, min_length=2, description="Narrow facets to search results"),
    body: str | None = Query(None),
    type: str | None = Query(None),
):
    """Aggregated counts by UN body, resource type, language, and year."""
    return await svc.get_facets(conn, q=q, body=body, resource_type=type)
