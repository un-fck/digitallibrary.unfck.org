"""API key generation, hashing, and management."""

from __future__ import annotations

import hashlib
import secrets

from psycopg import AsyncConnection

PREFIX = "undl_live_"
DAILY_LIMITS = {"free": 10_000, "research": 100_000, "institutional": -1}


def generate_key() -> str:
    """Generate a new API key with the undl_live_ prefix."""
    return PREFIX + secrets.token_hex(32)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def key_prefix(raw_key: str) -> str:
    return raw_key[:12]


async def create_api_user(
    conn: AsyncConnection,
    *,
    email: str,
    name: str | None = None,
    use_case: str | None = None,
    user_id: str | None = None,
) -> str:
    """Create or get an api_user. Returns the api_user id."""
    async with conn.cursor() as cur:
        # Check if exists
        await cur.execute(
            "SELECT id FROM digitallibrary.api_users WHERE email = %s",
            (email,),
        )
        row = await cur.fetchone()
        if row:
            return str(row[0])

        await cur.execute(
            """
            INSERT INTO digitallibrary.api_users (email, name, use_case, user_id, verified_at)
            VALUES (%s, %s, %s, %s, NOW())
            RETURNING id
            """,
            (email, name, use_case, user_id),
        )
        row = await cur.fetchone()
        await conn.commit()
        return str(row[0])


async def create_key_for_user(conn: AsyncConnection, api_user_id: str) -> str:
    """Generate a new API key for a user. Returns the raw key."""
    raw_key = generate_key()
    async with conn.cursor() as cur:
        # Revoke any existing active keys
        await cur.execute(
            "UPDATE digitallibrary.api_keys SET revoked_at = NOW() WHERE api_user_id = %s AND revoked_at IS NULL",
            (api_user_id,),
        )
        await cur.execute(
            """
            INSERT INTO digitallibrary.api_keys (api_user_id, key_hash, key_prefix, tier, rate_limit)
            SELECT %s, %s, %s, COALESCE(u.tier, 'free'), CASE u.tier WHEN 'research' THEN 300 WHEN 'institutional' THEN 1000 ELSE 60 END
            FROM digitallibrary.api_users u WHERE u.id = %s
            """,
            (api_user_id, hash_key(raw_key), key_prefix(raw_key), api_user_id),
        )
        await conn.commit()
    return raw_key


async def get_key_info(conn: AsyncConnection, api_user_id: str) -> dict | None:
    """Get active key info for a user."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT k.key_prefix, k.tier, k.rate_limit, k.created_at, k.last_used_at
            FROM digitallibrary.api_keys k
            WHERE k.api_user_id = %s AND k.revoked_at IS NULL
            ORDER BY k.created_at DESC LIMIT 1
            """,
            (api_user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "key_prefix": row[0],
            "tier": row[1],
            "rate_limit": row[2],
            "created_at": row[3],
            "last_used_at": row[4],
        }


async def get_usage(conn: AsyncConnection, api_user_id: str) -> dict:
    """Get usage stats for the user's active key."""
    async with conn.cursor() as cur:
        # Get key_id
        await cur.execute(
            "SELECT id, tier, rate_limit FROM digitallibrary.api_keys WHERE api_user_id = %s AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (api_user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return {"requests_today": 0, "requests_this_month": 0, "daily_limit": 10_000, "rate_limit_per_min": 60}

        key_id, tier, rate_limit = row

        await cur.execute(
            "SELECT count(*) FROM digitallibrary.api_usage_log WHERE key_id = %s AND requested_at >= CURRENT_DATE",
            (key_id,),
        )
        today = (await cur.fetchone())[0]

        await cur.execute(
            "SELECT count(*) FROM digitallibrary.api_usage_log WHERE key_id = %s AND requested_at >= date_trunc('month', CURRENT_DATE)",
            (key_id,),
        )
        month = (await cur.fetchone())[0]

        daily_limit = DAILY_LIMITS.get(tier, 10_000)
        return {
            "requests_today": today,
            "requests_this_month": month,
            "daily_limit": daily_limit,
            "rate_limit_per_min": rate_limit,
        }


async def rotate_key(conn: AsyncConnection, api_user_id: str) -> str:
    """Revoke current key and issue a new one. Returns raw key."""
    return await create_key_for_user(conn, api_user_id)


async def create_verify_token(
    conn: AsyncConnection,
    *,
    email: str,
    name: str | None = None,
    use_case: str | None = None,
) -> str:
    """Create a verification token for API key signup. Returns the token."""
    token = secrets.token_urlsafe(32)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO digitallibrary.api_verify_tokens (token, email, name, use_case, expires_at)
            VALUES (%s, %s, %s, %s, NOW() + INTERVAL '1 hour')
            """,
            (token, email, name, use_case),
        )
        await conn.commit()
    return token


async def verify_token(conn: AsyncConnection, token: str) -> dict | None:
    """Verify a signup token. Returns {email, name, use_case} or None."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT email, name, use_case FROM digitallibrary.api_verify_tokens
            WHERE token = %s AND used_at IS NULL AND expires_at > NOW()
            """,
            (token,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        # Mark as used
        await cur.execute(
            "UPDATE digitallibrary.api_verify_tokens SET used_at = NOW() WHERE token = %s",
            (token,),
        )
        await conn.commit()
        return {"email": row[0], "name": row[1], "use_case": row[2]}
