-- UN Digital Library: MARC-native document schema
-- Run: psql "$DATABASE_URL" -f sql/documents_tables.sql
-- Prereqs:
--   - Schema "digitallibrary" must exist

DROP TABLE IF EXISTS digitallibrary.documents CASCADE;

CREATE TABLE digitallibrary.documents (
  -- Identity
  recid              INTEGER PRIMARY KEY,           -- MARC 001
  document_symbol    TEXT,                           -- MARC 191$a  e.g. "A/RES/78/1"
  symbol_body        TEXT,                           -- MARC 191$b  e.g. "A/"
  symbol_session     TEXT,                           -- MARC 191$c  e.g. "78"
  symbol_committee   TEXT,                           -- MARC 191$d  e.g. "A/C.3/"

  -- Title
  title              TEXT,                           -- MARC 245$a + $b
  title_statement    TEXT,                           -- MARC 245$c  (responsibility)

  -- Dates
  date_publication   DATE,                           -- MARC 269$a  (ISO date)
  date_text          TEXT,                           -- MARC 260$c  (human-readable)

  -- Publication
  publisher          TEXT,                           -- MARC 260$b
  pub_place          TEXT,                           -- MARC 260$a
  physical_desc      TEXT,                           -- MARC 300$a

  -- Classification
  doc_class_code     TEXT,                           -- MARC 089$b
  doc_class_desc     TEXT,                           -- MARC 089$a

  -- Languages
  languages          TEXT[] NOT NULL DEFAULT '{}',   -- MARC 041$a  (ISO 639 codes)

  -- Subjects & authors
  subjects           TEXT[] NOT NULL DEFAULT '{}',   -- MARC 650$a
  corporate_authors  JSONB NOT NULL DEFAULT '[]',    -- MARC 710  [{name, type}]
  un_body            TEXT,                           -- MARC 981$a
  un_committee       TEXT,                           -- MARC 981$b

  -- Notes & abstract
  notes              TEXT[] NOT NULL DEFAULT '{}',   -- MARC 500$a
  summary            TEXT,                           -- MARC 520$a

  -- Files / attachments
  files              JSONB NOT NULL DEFAULT '[]',    -- MARC 856  [{url, lang, size, uuid}]

  -- Collections & type
  collections        TEXT[] NOT NULL DEFAULT '{}',   -- MARC 980$a
  resource_type      TEXT,                           -- MARC 989$a
  resource_subtype   TEXT,                           -- MARC 989$b

  -- Voting
  vote_summary       TEXT,                           -- MARC 996$a

  -- Agenda & related
  agenda_items       JSONB NOT NULL DEFAULT '[]',    -- MARC 991  [{doc, item, desc, topic}]
  related_documents  JSONB NOT NULL DEFAULT '[]',    -- MARC 993  [{symbol, relationship}]

  -- Raw storage (enables re-parsing without re-harvesting)
  marcxml            TEXT NOT NULL,

  -- Housekeeping
  deleted_at         TIMESTAMPTZ,                    -- NULL = active; set = soft-deleted
  harvested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- B-tree indexes for common lookups
CREATE INDEX idx_doc_symbol        ON digitallibrary.documents (document_symbol);
CREATE INDEX idx_doc_date          ON digitallibrary.documents (date_publication DESC NULLS LAST);
CREATE INDEX idx_doc_body          ON digitallibrary.documents (un_body);
CREATE INDEX idx_doc_resource_type ON digitallibrary.documents (resource_type);
CREATE INDEX idx_doc_deleted       ON digitallibrary.documents (deleted_at) WHERE deleted_at IS NOT NULL;

-- GIN indexes for array searches
CREATE INDEX idx_doc_subjects_gin    ON digitallibrary.documents USING GIN (subjects);
CREATE INDEX idx_doc_languages_gin   ON digitallibrary.documents USING GIN (languages);
CREATE INDEX idx_doc_collections_gin ON digitallibrary.documents USING GIN (collections);

-- Harvest state table (used by incremental sync to persist watermark across GH Action runs)
DROP TABLE IF EXISTS digitallibrary.harvest_state CASCADE;

CREATE TABLE digitallibrary.harvest_state (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-update updated_at on documents
CREATE OR REPLACE FUNCTION digitallibrary.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_documents_set_updated_at
BEFORE UPDATE ON digitallibrary.documents
FOR EACH ROW
EXECUTE FUNCTION digitallibrary.set_updated_at();
