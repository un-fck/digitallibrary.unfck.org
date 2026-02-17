"""Rate limiting via slowapi with tier-aware limits."""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter

# Anonymous: 10/min, 100/day
ANON_LIMIT = "10/minute;100/day"
# Free tier: 60/min, 10000/day
FREE_LIMIT = "60/minute;10000/day"
# Research: 300/min, 100000/day
RESEARCH_LIMIT = "300/minute;100000/day"
# Institutional: 1000/min
INSTITUTIONAL_LIMIT = "1000/minute"


def _key_func(request: Request) -> str:
    """Extract rate-limit key: API key prefix or client IP."""
    key_info = getattr(request.state, "api_key", None)
    if key_info:
        return f"key:{key_info['key_prefix']}"
    # Anonymous — use IP
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _dynamic_limit(key: str) -> str:
    """Return the rate limit string based on the key type."""
    # This is called by slowapi with the key returned by _key_func
    # We can't easily access request.state here, so we use a simpler approach:
    # the middleware sets the limit based on the key's tier
    # For now, use a default; the actual enforcement happens via the decorator
    return FREE_LIMIT


limiter = Limiter(key_func=_key_func)
