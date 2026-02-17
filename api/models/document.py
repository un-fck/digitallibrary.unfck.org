from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from api.models.common import PaginationMeta


class FileEntry(BaseModel):
    url: str
    lang: str | None = None
    size: str | None = None
    uuid: str | None = None


class CorporateAuthor(BaseModel):
    name: str
    type: str | None = None


class AgendaItem(BaseModel):
    doc: str | None = None
    item: str | None = None
    desc: str | None = None
    topic: str | None = None


class RelatedDocument(BaseModel):
    symbol: str | None = None
    relationship: str | None = None


class DocumentSummary(BaseModel):
    """Compact representation for list/search results."""

    recid: int
    document_symbol: str | None = None
    title: str | None = None
    date_publication: date | None = None
    un_body: str | None = None
    resource_type: str | None = None
    languages: list[str] = []
    summary: str | None = None


class DocumentDetail(DocumentSummary):
    """Full document with all fields."""

    symbol_body: str | None = None
    symbol_session: str | None = None
    symbol_committee: str | None = None
    title_statement: str | None = None
    date_text: str | None = None
    publisher: str | None = None
    pub_place: str | None = None
    physical_desc: str | None = None
    doc_class_code: str | None = None
    doc_class_desc: str | None = None
    subjects: list[str] = []
    corporate_authors: list[CorporateAuthor] = []
    un_committee: str | None = None
    notes: list[str] = []
    files: list[FileEntry] = []
    collections: list[str] = []
    resource_subtype: str | None = None
    vote_summary: str | None = None
    agenda_items: list[AgendaItem] = []
    related_documents: list[RelatedDocument] = []
    harvested_at: datetime | None = None


class DocumentListResponse(BaseModel):
    meta: PaginationMeta
    results: list[DocumentSummary]
