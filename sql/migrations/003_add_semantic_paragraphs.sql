-- Live migration: add the semantic full-text layer (Track A, stage 4).
-- Run once against an existing digitallibrary schema:
--   psql "$DATABASE_URL" -f sql/migrations/003_add_semantic_paragraphs.sql
-- The from-scratch definitions live in sql/schema/fulltext_tables.sql (keep in sync).
--
-- Idempotent: every object uses IF NOT EXISTS, so re-applying is a no-op.
-- Adds two tables (additive only — no changes to 002's tables):
--   digitallibrary.document_paragraphs — normalized semantic elements (one row
--     per parsed element, document order) produced by python/fulltext_parse.py.
--   digitallibrary.document_parses     — per-document parse ledger keeping the
--     accounting invariant (dropped[]/issues[]) queryable in SQL.
--
-- The loader (fulltext_parse.py --to-db) delete-then-inserts per (symbol,lang),
-- so the tables are fully rebuildable from document_paragraphs_raw at any time.
-- It also advances document_files.status 'extracted' -> 'parsed' (failures ->
-- 'parse_failed'); no schema change is needed for those status values.

BEGIN;

-- 1. Semantic element rows (one per parsed element, document order).
CREATE TABLE IF NOT EXISTS digitallibrary.document_paragraphs (
  symbol_normalized TEXT    NOT NULL,             -- joins document_files / document_paragraphs_raw
  lang              TEXT    NOT NULL DEFAULT 'en',
  position          INTEGER NOT NULL,             -- 0-based element index in parsed order (NOT the raw position)
  id                UUID    NOT NULL,             -- uuid5(NAMESPACE_URL, '<symbol_normalized>:<lang>:<position>'), computed by the loader
  type              TEXT    NOT NULL,             -- frontmatter|title|opening|heading|paragraph|footnote|divider|vote_record|table|signature
  subtype           TEXT,                         -- masthead|subres|instrument|amendment (NULL otherwise)
  section           TEXT    NOT NULL DEFAULT 'main',  -- main|annex|appendix
  annex_index       SMALLINT,                     -- 1-based annex ordinal (section='annex' only)
  text_index        SMALLINT NOT NULL DEFAULT 1,  -- multi-text/omnibus block ordinal (1 = single text)
  paragraph_type    TEXT,                         -- preambular|operative — resolution-body only; NULL everywhere else even when numbered
  level             SMALLINT,                     -- opening=0, top-level=1, subparagraph 2/3
  heading_level     SMALLINT,                     -- heading depth
  prefix            TEXT,                         -- literal marker ('1.', '(a)', '(iv)'); NULL for inferred/unnumbered
  lead_verb         TEXT,                         -- preambular participle / operative verb
  text              TEXT    NOT NULL,             -- cleaned element text; table cells joined with ' | '
  raw_positions     INTEGER[] NOT NULL,           -- provenance into document_paragraphs_raw.position (>=1, never empty)
  inferred_operative BOOLEAN NOT NULL DEFAULT false,  -- rescued source-dropped operative number (auditable; no invented prefix)
  vote              JSONB,                        -- vote_record only: {in_favour:[country],against:[],abstaining:[],...}
  vote_summary      JSONB,                        -- vote_record only: {in_favour:int,against:int,abstaining:int}
  hyperlinks        JSONB,                        -- [{text,url}] carried from raw; [] when none
  note_ids          JSONB,                        -- [int] footnote ids; [] when none
  parser_version    TEXT    NOT NULL,             -- e.g. 'sem-v1'
  parsed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol_normalized, lang, position)
);

CREATE INDEX IF NOT EXISTS idx_document_paragraphs_symbol
  ON digitallibrary.document_paragraphs (symbol_normalized, lang);
CREATE INDEX IF NOT EXISTS idx_document_paragraphs_ptype
  ON digitallibrary.document_paragraphs (paragraph_type) WHERE paragraph_type IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_paragraphs_id
  ON digitallibrary.document_paragraphs (id);

-- 2. Per-document parse ledger (accounting invariant queryable in SQL).
CREATE TABLE IF NOT EXISTS digitallibrary.document_parses (
  symbol_normalized TEXT    NOT NULL,
  lang              TEXT    NOT NULL DEFAULT 'en',
  parser_version    TEXT    NOT NULL,
  format            TEXT,                         -- docx|doc|wpd
  element_count     INTEGER NOT NULL,             -- number of document_paragraphs rows for this doc
  dropped           JSONB   NOT NULL DEFAULT '[]'::jsonb,  -- [{position,reason}] intentionally-dropped raw positions
  issues            JSONB   NOT NULL DEFAULT '[]'::jsonb,  -- [{position,problem,text_head}] parser anomalies incl. accounting failures
  parsed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol_normalized, lang)
);

COMMIT;
