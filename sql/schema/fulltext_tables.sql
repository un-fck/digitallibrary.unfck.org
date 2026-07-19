-- UN Digital Library: document full-text pipeline schema (Track A).
-- Run: psql "$DATABASE_URL" -f sql/schema/fulltext_tables.sql
-- Prereqs:
--   - Schema "digitallibrary" must exist (see documents_tables.sql)
--   - digitallibrary.documents must exist (join key: symbol_normalized)
--
-- Re-runnable: every object uses IF NOT EXISTS so this file is safe to apply
-- repeatedly. The numbered delta lives in sql/migrations/002_add_fulltexts.sql
-- (keep the two in sync by hand — same convention as documents_tables.sql /
-- migrations/001_add_display_title.sql).
--
-- Two-layer model:
--   1. document_files        — fetch/convert/extract ledger (one row per symbol+lang)
--   2. document_paragraphs_raw — low-interpretation extraction layer
-- A THIRD, semantic layer (normalized operative/preambular paragraphs, mandate
-- objects, etc.) is deliberately NOT created here. It arrives later as migration
-- 003 once the raw layer has been iterated on. Until then, document_paragraphs_raw
-- is the re-parse substrate and the SSD archive is ground truth.

-- ---------------------------------------------------------------------------
-- Ledger: one row per (symbol, language) tracking fetch → convert → extract.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS digitallibrary.document_files (
  symbol_normalized TEXT NOT NULL,               -- joins digitallibrary.documents.symbol_normalized
  lang              TEXT NOT NULL DEFAULT 'en',
  format            TEXT,                         -- 'docx'|'doc'|'wpd'|'pdf'|'html'|'unknown' (sniffed from magic bytes, NOT content-type)
  content_type      TEXT,                         -- as reported by the server (Content-Type header)
  size_bytes        BIGINT,
  sha256            TEXT,
  ods_url           TEXT,                         -- source URL on documents.un.org
  archive_path      TEXT,                         -- relative to archive root, e.g. 'original/A_RES_60_1.doc'
  converted_path    TEXT,                         -- e.g. 'converted/A_RES_60_1.docx' (NULL if native docx or not yet converted)
  converter         TEXT,                         -- e.g. 'libreoffice 25.2'
  status            TEXT NOT NULL,                -- 'fetched'|'unavailable'|'failed'|'converted'|'convert_failed'|'extracted'
  error             TEXT,
  fetched_at        TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol_normalized, lang)
);

CREATE INDEX IF NOT EXISTS idx_document_files_status ON digitallibrary.document_files (status);
CREATE INDEX IF NOT EXISTS idx_document_files_format ON digitallibrary.document_files (format);

-- ---------------------------------------------------------------------------
-- Raw paragraph extraction: preserves document order + low-level formatting
-- with minimal interpretation. Re-derivable from the archive at any time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS digitallibrary.document_paragraphs_raw (
  symbol_normalized TEXT NOT NULL,
  lang              TEXT NOT NULL DEFAULT 'en',
  position          INTEGER NOT NULL,             -- 0-based document order
  kind              TEXT NOT NULL,                -- 'paragraph'|'table_cell'|'footnote'|'section_break'|'empty'
  text              TEXT NOT NULL,                -- may be '' for markers/empty
  style_id          TEXT,
  style_name        TEXT,
  numbering         JSONB,                        -- {"num_id":int,"ilvl":int,"num_fmt":str,"lvl_text":str} when numbered
  props             JSONB,                        -- {"italic":bool,"bold":bool,"alignment":str,"indent_left":int,"indent_hanging":int,"lead_italic_text":str, ...} only non-defaults
  table_cell        JSONB,                        -- {"table":int,"row":int,"col":int} for table cells
  hyperlinks        JSONB,                        -- [{"text":str,"url":str}] incl. field-code links
  footnote_ref      JSONB,                        -- kind='footnote': {"ref_position":int,"note_id":int}; paragraphs w/ refs: list of note ids
  extractor_version TEXT NOT NULL,
  extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol_normalized, lang, position)
);

CREATE INDEX IF NOT EXISTS idx_document_paragraphs_raw_symbol
  ON digitallibrary.document_paragraphs_raw (symbol_normalized, lang);
