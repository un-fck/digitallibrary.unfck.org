"""Search endpoint — semantic alias for /documents with relevance sort."""

from __future__ import annotations

import math
from datetime import date

from fastapi import APIRouter, Query

from api.deps import DBConn, OptionalKey
from api.models.common import PaginationMeta
from api.models.document import DocumentListResponse, DocumentSummary
from api.services import document_service as svc

router = APIRouter(tags=["search"])


@router.get("/search", response_model=DocumentListResponse)
async def search_documents(
    conn: DBConn,
    _key: OptionalKey,
    q: str = Query(..., min_length=2, description="Search query (title, symbol, or recid)"),
    body: str | None = Query(None, description="UN body filter"),
    type: str | None = Query(None, description="Resource type filter"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
):
    """Search documents with relevance ranking."""
    rows, total = await svc.list_documents(
        conn,
        q=q,
        body=body,
        resource_type=type,
        date_from=date_from,
        date_to=date_to,
        sort="relevance",
        page=page,
        per_page=per_page,
    )
    return DocumentListResponse(
        meta=PaginationMeta(
            total=total,
            page=page,
            per_page=per_page,
            total_pages=max(1, math.ceil(total / per_page)),
        ),
        results=[DocumentSummary(**r) for r in rows],
    )
