"""Stats endpoint — public dataset metadata."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import DBConn
from api.services import document_service as svc

router = APIRouter(tags=["metadata"])


@router.get("/stats")
async def dataset_stats(conn: DBConn):
    """Public dataset-level statistics. No API key required."""
    return await svc.get_stats(conn)
