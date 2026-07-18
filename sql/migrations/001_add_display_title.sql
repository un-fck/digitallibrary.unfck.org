-- Live migration: add title_other (MARC 239$a), symbol_normalized, display_title.
-- Run once against an existing digitallibrary.documents:
--   psql "$DATABASE_URL" -f sql/migrations/001_add_display_title.sql
-- The from-scratch definitions live in sql/schema/documents_tables.sql (keep in sync).
--
-- Ordering matters: add + backfill title_other FIRST, then add the two generated
-- columns in ONE statement so the table is rewritten only once and display_title
-- computes with title_other already populated.

BEGIN;

-- 1. Plain column (metadata-only change, no rewrite) for MARC 239$a.
ALTER TABLE digitallibrary.documents
  ADD COLUMN IF NOT EXISTS title_other TEXT;

-- 2. Backfill 239$a from the stored marcxml (only ~37k rows carry a 239 — all of
--    S/RES + S/PRST + a few GA). All such marcxml is well-formed XML (verified),
--    so xpath is safe here. Going forward marc_parser.py populates this on harvest.
UPDATE digitallibrary.documents
SET title_other = NULLIF(btrim(
      (xpath('//datafield[@tag="239"]/subfield[@code="a"]/text()', marcxml::xml))[1]::text
    ), '')
WHERE marcxml LIKE '%tag="239"%'
  AND title_other IS NULL;

-- 3. Both generated columns in one ALTER = single table rewrite.
ALTER TABLE digitallibrary.documents
  ADD COLUMN symbol_normalized TEXT GENERATED ALWAYS AS (
    upper(regexp_replace(document_symbol, '[[:space:]]', '', 'g'))
  ) STORED,
  ADD COLUMN display_title TEXT GENERATED ALWAYS AS (
    CASE
      WHEN title IS NOT NULL AND title <> ''
           AND title !~* '^(resolution|decision|statement)([[:space:]]|$)'
        THEN regexp_replace(
               regexp_replace(title, '[[:space:]]+(resolution|decision)$', '', 'i'),
               '\.$', '')
      WHEN title_other IS NOT NULL AND title_other <> ''
        THEN title_other
      ELSE NULLIF(title, '')
    END
  ) STORED;

-- 4. Index the canonical join key.
CREATE INDEX IF NOT EXISTS idx_doc_symbol_norm
  ON digitallibrary.documents (symbol_normalized);

COMMIT;
