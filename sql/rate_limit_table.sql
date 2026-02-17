-- PostgreSQL-backed rate limiting for serverless (replaces in-memory slowapi)
-- Run: psql "$DATABASE_URL" -f sql/rate_limit_table.sql

CREATE TABLE IF NOT EXISTS digitallibrary.rate_limit_windows (
  key          TEXT NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  count        INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (key, window_start)
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_cleanup
  ON digitallibrary.rate_limit_windows (window_start);
