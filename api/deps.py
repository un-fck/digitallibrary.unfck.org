"""Shared FastAPI dependencies: DB connection, API key auth."""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, APIKeyQuery
from psycopg import AsyncConnection

from api import database

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

async def get_conn():
    """Yield an async DB connection from the pool."""
    async with database.pool.connection() as conn:
        yield conn

DBConn = Annotated[AsyncConnection, Depends(get_conn)]

# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------

_header_scheme = APIKeyHeader(name="Authorization", auto_error=False)
_query_scheme = APIKeyQuery(name="api_key", auto_error=False)


def _extract_key(header_val: str | None, query_val: str | None) -> str | None:
    """Extract raw API key from header or query param."""
    if header_val:
        if header_val.startswith("Bearer "):
            return header_val[7:]
        return header_val
    return query_val


async def _lookup_key(conn: AsyncConnection, raw_key: str) -> dict | None:
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT k.id, k.api_user_id, k.tier, k.rate_limit, k.key_prefix,
                   u.email, u.blocked_at
            FROM digitallibrary.api_keys k
            JOIN digitallibrary.api_users u ON u.id = k.api_user_id
            WHERE k.key_hash = %s AND k.revoked_at IS NULL
            """,
            (key_hash,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        # Update last_used_at (fire-and-forget, don't block)
        await cur.execute(
            "UPDATE digitallibrary.api_keys SET last_used_at = NOW() WHERE id = %s",
            (row[0],),
        )
        return {
            "key_id": row[0],
            "api_user_id": row[1],
            "tier": row[2],
            "rate_limit": row[3],
            "key_prefix": row[4],
            "email": row[5],
            "blocked": row[6] is not None,
        }


async def optional_api_key(
    request: Request,
    header_key: str | None = Security(_header_scheme),
    query_key: str | None = Security(_query_scheme),
) -> dict | None:
    """Returns API key info if provided, None for anonymous."""
    raw_key = _extract_key(header_key, query_key)
    if not raw_key:
        return None
    if not raw_key.startswith("undl_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")
    async with database.pool.connection() as conn:
        info = await _lookup_key(conn, raw_key)
    if not info:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if info["blocked"]:
        raise HTTPException(status_code=403, detail="API key blocked")
    request.state.api_key = info
    return info


async def require_api_key(
    info: dict | None = Depends(optional_api_key),
) -> dict:
    """Requires a valid API key — rejects anonymous requests."""
    if info is None:
        raise HTTPException(status_code=401, detail="API key required")
    return info

OptionalKey = Annotated[dict | None, Depends(optional_api_key)]
RequiredKey = Annotated[dict, Depends(require_api_key)]
