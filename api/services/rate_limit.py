"""PostgreSQL-backed rate limiting for serverless environments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from psycopg import AsyncConnection


# Tier limits: requests per minute
TIER_LIMITS: dict[str, int] = {
    "anonymous": 10,
    "free": 60,
    "research": 300,
    "institutional": 1000,
}


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset: datetime


async def check_rate_limit(
    conn: AsyncConnection,
    key: str,
    tier: str,
) -> RateLimitResult:
    """Check and increment rate limit for a key using a per-minute window.

    Uses INSERT ... ON CONFLICT for atomic upsert. Each (key, minute) pair
    gets a counter that increments on every request.
    """
    limit = TIER_LIMITS.get(tier, TIER_LIMITS["anonymous"])

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO digitallibrary.rate_limit_windows (key, window_start, count)
            VALUES (
                %(key)s,
                date_trunc('minute', now()),
                1
            )
            ON CONFLICT (key, window_start)
            DO UPDATE SET count = digitallibrary.rate_limit_windows.count + 1
            RETURNING count, window_start
            """,
            {"key": key},
        )
        row = await cur.fetchone()
        await conn.commit()

    count = row[0]
    window_start: datetime = row[1]
    reset = window_start.replace(second=0, microsecond=0, tzinfo=timezone.utc)
    reset = reset + timedelta(minutes=1)

    return RateLimitResult(
        allowed=count <= limit,
        limit=limit,
        remaining=max(0, limit - count),
        reset=reset,
    )


async def cleanup_old_windows(conn: AsyncConnection) -> int:
    """Delete rate limit windows older than 1 hour. Returns rows deleted."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            DELETE FROM digitallibrary.rate_limit_windows
            WHERE window_start < now() - interval '1 hour'
            """
        )
        await conn.commit()
        return cur.rowcount
