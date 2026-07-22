-- Live migration: add source_symbol provenance for the VOLUME-SPLIT pipeline.
-- Run once against an existing digitallibrary schema:
--   psql "$DATABASE_URL" -f sql/migrations/005_source_symbol.sql
-- The from-scratch definitions live in sql/schema/fulltext_tables.sql (keep in sync).
--
-- Idempotent: every object uses IF NOT EXISTS, so re-applying is a no-op.
--
-- Context. GA/ECOSOC *decisions* (A/DEC/*, E/DEC/*) and early Human Rights
-- Council texts (A/HRC/RES|PRST|DEC/* for sessions 2-11) are NOT issued as
-- standalone ODS documents — they only exist inside compilation *volumes* / the
-- session *reports* that ODS does host:
--   * GA decisions   -> GAOR Supplement 49, Volume II  ('A/<n>/49 (Vol. II)')
--   * ECOSOC res+dec -> ECOSOC Supplement 1            ('E/<year>/99')
--   * early HRC       -> the per-session HRC report      ('A/HRC/<n>/<doc>')
-- The volume-split pipeline (python/fulltext_split_volumes.py) fetches the parent
-- volume/report as an ordinary ledger doc, extracts its raw paragraphs, then
-- SPLITS the per-decision / per-resolution CHILDREN back out into
-- document_paragraphs_raw under their own child symbol_normalized.
--
-- `source_symbol` records, on a split CHILD row, the parent volume/report symbol
-- it was carved from. Semantics:
--   * On document_paragraphs_raw: set on every child paragraph row so the child's
--     provenance (which volume it came from) is queryable and a re-split can
--     DELETE ... WHERE source_symbol = <volume> cleanly.
--   * On document_files: set on the child LEDGER row. A child ledger row never
--     gets an archive_path of its own (the parent volume owns the file); it uses
--     the normal status lifecycle from 'extracted' onward. The parent volume row
--     itself is an ordinary ledger doc (source_symbol NULL, archive_path set).
--   * NULL for every ordinary (non-split) row — the 8-family catalog is unchanged.

BEGIN;

ALTER TABLE digitallibrary.document_files
  ADD COLUMN IF NOT EXISTS source_symbol TEXT;   -- split children: parent volume/report symbol; NULL otherwise

ALTER TABLE digitallibrary.document_paragraphs_raw
  ADD COLUMN IF NOT EXISTS source_symbol TEXT;   -- split children: parent volume/report symbol; NULL otherwise

-- Look up all children carved from one volume (re-split delete key; provenance).
CREATE INDEX IF NOT EXISTS idx_document_files_source_symbol
  ON digitallibrary.document_files (source_symbol)
  WHERE source_symbol IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_document_paragraphs_raw_source_symbol
  ON digitallibrary.document_paragraphs_raw (source_symbol)
  WHERE source_symbol IS NOT NULL;

COMMIT;
