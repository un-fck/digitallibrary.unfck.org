"""Document endpoints: list, search by symbol, search by recid, marcxml."""

from __future__ import annotations

import math
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response
from psycopg import AsyncConnection

from api.deps import DBConn, OptionalKey
from api.models.common import PaginationMeta
from api.models.document import DocumentDetail, DocumentListResponse, DocumentSummary
from api.services import document_service as svc

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    conn: DBConn,
    _key: OptionalKey,
    q: str | None = Query(None, min_length=2, description="Search title and symbol"),
    symbol: str | None = Query(None, description="Symbol prefix filter"),
    body: str | None = Query(None, description="UN body filter"),
    type: str | None = Query(None, description="Resource type filter"),
    subject: str | None = Query(None, description="Subject filter"),
    language: str | None = Query(None, description="Language code filter (e.g. eng)"),
    date_from: date | None = Query(None, description="Publication date from"),
    date_to: date | None = Query(None, description="Publication date to"),
    sort: str = Query("date_desc", description="Sort order", enum=["date_desc", "date_asc", "symbol_asc", "relevance"]),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(25, ge=1, le=100, description="Results per page"),
):
    """List and filter documents with pagination."""
    rows, total = await svc.list_documents(
        conn,
        q=q,
        symbol=symbol,
        body=body,
        resource_type=type,
        subject=subject,
        language=language,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
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


@router.get("/documents/recid/{recid}", response_model=DocumentDetail)
async def get_document_by_recid(
    recid: int,
    conn: DBConn,
    _key: OptionalKey,
):
    """Get a single document by record ID."""
    doc = await svc.get_by_recid(conn, recid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.pop("marcxml", None)
    return DocumentDetail(**doc)


@router.get("/documents/recid/{recid}/marcxml")
async def get_marcxml_by_recid(
    recid: int,
    conn: DBConn,
    _key: OptionalKey,
):
    """Get raw MARCXML for a document by record ID."""
    xml = await svc.get_marcxml(conn, recid)
    if not xml:
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(content=xml, media_type="application/xml")


@router.get("/documents/{symbol:path}", response_model=DocumentDetail | list[DocumentDetail])
async def get_document_by_symbol(
    symbol: str,
    conn: DBConn,
    _key: OptionalKey,
):
    """Get document(s) by UN document symbol (e.g. A/RES/78/1).

    Returns a single document if the symbol is unique, or an array if multiple
    records share the same symbol. Falls back to recid lookup if the symbol
    looks like an integer.
    """
    # Recid fallback: if the "symbol" is just a number, treat as recid
    if symbol.isdigit():
        doc = await svc.get_by_recid(conn, int(symbol))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        doc.pop("marcxml", None)
        return DocumentDetail(**doc)

    docs = await svc.get_by_symbol(conn, symbol)
    if not docs:
        raise HTTPException(status_code=404, detail=f"No documents found for symbol '{symbol}'")

    results = []
    for d in docs:
        d.pop("marcxml", None)
        results.append(DocumentDetail(**d))

    if len(results) == 1:
        return results[0]
    return results
