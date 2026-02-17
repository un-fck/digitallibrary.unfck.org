from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str
    name: str | None = None
    use_case: str | None = None


class VerifyRequest(BaseModel):
    token: str


class KeyResponse(BaseModel):
    api_key: str
    key_prefix: str
    tier: str
    rate_limit: int
    message: str = "Store this key securely — it will not be shown again."


class KeyInfo(BaseModel):
    key_prefix: str
    tier: str
    rate_limit: int
    created_at: datetime
    last_used_at: datetime | None = None


class UsageStats(BaseModel):
    requests_today: int
    requests_this_month: int
    daily_limit: int
    rate_limit_per_min: int
