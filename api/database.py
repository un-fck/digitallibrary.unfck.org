from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from api.config import get_settings

pool: AsyncConnectionPool | None = None


async def init_pool() -> None:
    global pool
    settings = get_settings()
    pool = AsyncConnectionPool(
        conninfo=settings.db_conninfo,
        min_size=2,
        max_size=10,
        open=False,
    )
    await pool.open()


async def close_pool() -> None:
    global pool
    if pool:
        await pool.close()
        pool = None
