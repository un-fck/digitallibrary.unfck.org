-- Live migration: add the full-text pipeline tables (Track A).
-- Run once against an existing digitallibrary schema:
--   psql "$DATABASE_URL" -f sql/migrations/002_add_fulltexts.sql
-- The from-scratch definitions live in sql/schema/fulltext_tables.sql (keep in sync).
--
-- Idempotent: every object uses IF NOT EXISTS, so re-applying is a no-op.
-- Adds two tables:
--   digitallibrary.document_files          — fetch/convert/extract ledger
--   digitallibrary.document_paragraphs_raw — low-interpretation extraction layer
-- The semantic layer (normalized paragraphs / mandate objects) is intentionally
-- NOT part of this migration; it lands later as 003.

BEGIN;

-- 1. Fetch/convert/extract ledger, one row per (symbol, language).
CREATE TABLE IF NOT EXISTS digitallibrary.document_files (
  symbol_normalized TEXT NOT NULL,
  lang              TEXT NOT NULL DEFAULT 'en',
  format            TEXT,                         -- sniffed from magic bytes, NOT content-type
  content_type      TEXT,                         -- as reported by the server
  size_bytes        BIGINT,
  sha256            TEXT,
  ods_url           TEXT,
  archive_path      TEXT,                         -- relative to archive root, e.g. 'original/A_RES_60_1.doc'
  converted_path    TEXT,                         -- e.g. 'converted/A_RES_60_1.docx' (NULL if native docx / not yet converted)
  converter         TEXT,                         -- e.g. 'libreoffice 25.2'
  status            TEXT NOT NULL,                -- 'fetched'|'unavailable'|'failed'|'converted'|'convert_failed'|'extracted'
  error             TEXT,
  fetched_at        TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol_normalized, lang)
);

CREATE INDEX IF NOT EXISTS idx_document_files_status ON digitallibrary.document_files (status);
CREATE INDEX IF NOT EXISTS idx_document_files_format ON digitallibrary.document_files (format);

-- 2. Raw paragraph extraction layer (re-derivable from the archive).
CREATE TABLE IF NOT EXISTS digitallibrary.document_paragraphs_raw (
  symbol_normalized TEXT NOT NULL,
  lang              TEXT NOT NULL DEFAULT 'en',
  position          INTEGER NOT NULL,             -- 0-based document order
  kind              TEXT NOT NULL,                -- 'paragraph'|'table_cell'|'footnote'|'section_break'|'empty'
  text              TEXT NOT NULL,                -- may be '' for markers/empty
  style_id          TEXT,
  style_name        TEXT,
  numbering         JSONB,
  props             JSONB,
  table_cell        JSONB,
  hyperlinks        JSONB,
  footnote_ref      JSONB,
  extractor_version TEXT NOT NULL,
  extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol_normalized, lang, position)
);

CREATE INDEX IF NOT EXISTS idx_document_paragraphs_raw_symbol
  ON digitallibrary.document_paragraphs_raw (symbol_normalized, lang);

COMMIT;
