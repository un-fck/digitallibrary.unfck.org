-- API key management tables for the public API
-- Run: psql "$DATABASE_URL" -f sql/api_tables.sql
-- Prereqs: Schema "digitallibrary" must exist, auth tables (users) must exist

-- API users (anyone who signs up for an API key)
CREATE TABLE IF NOT EXISTS digitallibrary.api_users (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email       TEXT UNIQUE NOT NULL,
  name        TEXT,
  use_case    TEXT,
  tier        TEXT NOT NULL DEFAULT 'free',
  user_id     UUID REFERENCES digitallibrary.users(id),  -- link to magic-link user if exists
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  verified_at TIMESTAMPTZ,
  blocked_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_users_email ON digitallibrary.api_users (email);

-- API keys (one user can have multiple, typically one active)
CREATE TABLE IF NOT EXISTS digitallibrary.api_keys (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  api_user_id  UUID NOT NULL REFERENCES digitallibrary.api_users(id),
  key_hash     TEXT UNIQUE NOT NULL,           -- SHA-256 of raw key
  key_prefix   TEXT NOT NULL,                  -- first 12 chars for display/logs
  tier         TEXT NOT NULL DEFAULT 'free',
  rate_limit   INTEGER NOT NULL DEFAULT 60,    -- requests per minute
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ,
  revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON digitallibrary.api_keys (key_hash) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON digitallibrary.api_keys (api_user_id);

-- Email verification tokens for API key signup
CREATE TABLE IF NOT EXISTS digitallibrary.api_verify_tokens (
  token      TEXT PRIMARY KEY,
  email      TEXT NOT NULL,
  name       TEXT,
  use_case   TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  used_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_verify_expires ON digitallibrary.api_verify_tokens (expires_at);

-- API usage log (rate limiting analytics)
CREATE TABLE IF NOT EXISTS digitallibrary.api_usage_log (
  id           BIGINT GENERATED ALWAYS AS IDENTITY,
  key_id       UUID,                           -- NULL for anonymous requests
  ip_address   TEXT,
  endpoint     TEXT NOT NULL,
  status_code  SMALLINT,
  response_ms  INTEGER,
  requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_usage_key_time ON digitallibrary.api_usage_log (key_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_usage_requested ON digitallibrary.api_usage_log (requested_at DESC);
