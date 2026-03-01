"""MARCXML endpoints — raw MARC21 XML by symbol or recid."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from api.deps import DBConn, OptionalKey
from api.services import document_service as svc

router = APIRouter(tags=["marcxml"])


@router.get("/marcxml/recid/{recid}")
async def get_marcxml_by_recid(
    recid: int,
    conn: DBConn,
    _key: OptionalKey,
):
    """Get raw MARCXML for a document by numeric record ID."""
    xml = await svc.get_marcxml_by_recid(conn, recid)
    if not xml:
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(content=xml, media_type="application/xml")


@router.get("/marcxml/{symbol:path}")
async def get_marcxml_by_symbol(
    symbol: str,
    conn: DBConn,
    _key: OptionalKey,
):
    """Get raw MARCXML for a document by symbol (e.g. A/RES/78/1).

    Symbol lookup is case-insensitive.
    """
    xml = await svc.get_marcxml_by_symbol(conn, symbol)
    if not xml:
        raise HTTPException(status_code=404, detail=f"No document found for symbol '{symbol}'")
    return Response(content=xml, media_type="application/xml")
