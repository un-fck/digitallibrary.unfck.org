from __future__ import annotations

from pydantic import BaseModel


class PaginationMeta(BaseModel):
    total: int
    page: int
    per_page: int
    total_pages: int


class ErrorResponse(BaseModel):
    detail: str
