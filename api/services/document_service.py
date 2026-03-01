"""Query builders for document endpoints."""

from __future__ import annotations

from datetime import date

from psycopg import AsyncConnection
from psycopg.rows import dict_row

# Columns returned for list/search (compact).
# document_symbol aliased to symbol for API consistency.
SUMMARY_COLS = """
    recid, document_symbol AS symbol, title, date_publication,
    un_body, resource_type, languages, summary
"""

# All columns for detail view — marcxml excluded (fetched separately).
DETAIL_COLS = """
    recid, document_symbol AS symbol, symbol_body, symbol_session, symbol_committee,
    title, title_statement, date_publication, date_text,
    publisher, pub_place, physical_desc,
    doc_class_code, doc_class_desc, languages, subjects,
    corporate_authors, un_body, un_committee, notes, summary,
    files, collections, resource_type, resource_subtype,
    vote_summary, agenda_items, related_documents,
    harvested_at
"""


async def get_by_recid(conn: AsyncConnection, recid: int) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {DETAIL_COLS} FROM digitallibrary.documents WHERE recid = %s AND deleted_at IS NULL",
            (recid,),
        )
        return await cur.fetchone()


async def get_by_symbol(conn: AsyncConnection, symbol: str) -> dict | None:
    """Return the document matching the symbol (case-insensitive).

    If somehow multiple records share a symbol, returns the most recently
    published one.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {DETAIL_COLS} FROM digitallibrary.documents "
            f"WHERE UPPER(document_symbol) = UPPER(%s) AND deleted_at IS NULL "
            f"ORDER BY date_publication DESC NULLS LAST LIMIT 1",
            (symbol,),
        )
        return await cur.fetchone()


async def get_marcxml_by_recid(conn: AsyncConnection, recid: int) -> str | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT marcxml FROM digitallibrary.documents WHERE recid = %s AND deleted_at IS NULL",
            (recid,),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def get_marcxml_by_symbol(conn: AsyncConnection, symbol: str) -> str | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT marcxml FROM digitallibrary.documents "
            "WHERE UPPER(document_symbol) = UPPER(%s) AND deleted_at IS NULL "
            "ORDER BY date_publication DESC NULLS LAST LIMIT 1",
            (symbol,),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def list_documents(
    conn: AsyncConnection,
    *,
    q: str | None = None,
    symbol: str | None = None,
    body: str | None = None,
    resource_type: str | None = None,
    subject: str | None = None,
    language: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "date_desc",
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[dict], int]:
    """Return (results, total_count) for filtered document listing."""
    conditions: list[str] = ["deleted_at IS NULL"]
    params: list = []
    idx = 1
    q_idx: int | None = None  # tracks position of q param for relevance sort

    if q:
        conditions.append(
            f"(document_symbol ILIKE ${idx} || '%%' OR title ILIKE '%%' || ${idx} || '%%')"
        )
        params.append(q)
        q_idx = idx
        idx += 1

    if symbol:
        conditions.append(f"document_symbol ILIKE ${idx} || '%%'")
        params.append(symbol)
        idx += 1

    if body:
        conditions.append(f"un_body = ${idx}")
        params.append(body)
        idx += 1

    if resource_type:
        conditions.append(f"resource_type = ${idx}")
        params.append(resource_type)
        idx += 1

    if subject:
        conditions.append(f"${idx} = ANY(subjects)")
        params.append(subject)
        idx += 1

    if language:
        conditions.append(f"${idx} = ANY(languages)")
        params.append(language)
        idx += 1

    if date_from:
        conditions.append(f"date_publication >= ${idx}")
        params.append(date_from)
        idx += 1

    if date_to:
        conditions.append(f"date_publication <= ${idx}")
        params.append(date_to)
        idx += 1

    where = " AND ".join(conditions)

    sort_map = {
        "date_desc": "date_publication DESC NULLS LAST",
        "date_asc": "date_publication ASC NULLS LAST",
        "symbol_asc": "document_symbol ASC NULLS LAST",
    }
    order_by = sort_map.get(sort, "date_publication DESC NULLS LAST")

    if sort == "relevance" and q_idx is not None:
        # Symbol prefix match ranks above title match, then by date
        order_by = f"(document_symbol ILIKE ${q_idx} || '%%')::int DESC, date_publication DESC NULLS LAST"

    offset = (page - 1) * per_page

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT count(*) FROM digitallibrary.documents WHERE {where}",
            params,
        )
        total = (await cur.fetchone())["count"]

        await cur.execute(
            f"SELECT {SUMMARY_COLS} FROM digitallibrary.documents WHERE {where} ORDER BY {order_by} LIMIT {per_page} OFFSET {offset}",
            params,
        )
        rows = await cur.fetchall()

    return rows, total


async def get_facets(
    conn: AsyncConnection,
    *,
    q: str | None = None,
    body: str | None = None,
    resource_type: str | None = None,
) -> dict:
    """Return aggregated counts for filtering UI."""
    conditions: list[str] = ["deleted_at IS NULL"]
    params: list = []
    idx = 1

    if q:
        conditions.append(
            f"(document_symbol ILIKE ${idx} || '%%' OR title ILIKE '%%' || ${idx} || '%%')"
        )
        params.append(q)
        idx += 1
    if body:
        conditions.append(f"un_body = ${idx}")
        params.append(body)
        idx += 1
    if resource_type:
        conditions.append(f"resource_type = ${idx}")
        params.append(resource_type)
        idx += 1

    where = " AND ".join(conditions)
    facets: dict = {}

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT un_body AS value, count(*) AS count FROM digitallibrary.documents WHERE {where} AND un_body IS NOT NULL GROUP BY un_body ORDER BY count DESC LIMIT 50",
            params,
        )
        facets["un_body"] = await cur.fetchall()

        await cur.execute(
            f"SELECT resource_type AS value, count(*) AS count FROM digitallibrary.documents WHERE {where} AND resource_type IS NOT NULL GROUP BY resource_type ORDER BY count DESC LIMIT 50",
            params,
        )
        facets["resource_type"] = await cur.fetchall()

        await cur.execute(
            f"SELECT lang AS value, count(*) AS count FROM (SELECT unnest(languages) AS lang FROM digitallibrary.documents WHERE {where}) sub GROUP BY lang ORDER BY count DESC LIMIT 30",
            params,
        )
        facets["language"] = await cur.fetchall()

        await cur.execute(
            f"SELECT EXTRACT(YEAR FROM date_publication)::int AS value, count(*) AS count FROM digitallibrary.documents WHERE {where} AND date_publication IS NOT NULL GROUP BY value ORDER BY value DESC LIMIT 100",
            params,
        )
        facets["year"] = await cur.fetchall()

    return facets


async def get_stats(conn: AsyncConnection) -> dict:
    """Dataset-level metadata."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("""
            SELECT
                count(*) AS total_documents,
                min(date_publication) AS earliest_date,
                max(date_publication) AS latest_date,
                max(harvested_at) AS last_harvested,
                count(DISTINCT un_body) AS bodies_count,
                count(DISTINCT resource_type) AS resource_types_count
            FROM digitallibrary.documents
            WHERE deleted_at IS NULL
        """)
        return await cur.fetchone()
