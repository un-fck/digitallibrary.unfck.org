-- Document metadata tables for UN Digital Library OAI sync
-- Run: psql "$DATABASE_URL" -f sql/documents_tables.sql
-- Expects schema digitallibrary to already exist (created by admin bootstrap SQL).

CREATE TABLE IF NOT EXISTS digitallibrary.documents (
  id BIGSERIAL PRIMARY KEY,
  oai_identifier TEXT NOT NULL UNIQUE,
  recid BIGINT,
  datestamp TIMESTAMPTZ NOT NULL,
  deleted BOOLEAN NOT NULL DEFAULT FALSE,
  metadata_prefix TEXT NOT NULL DEFAULT 'oai_dc',
  source_set TEXT,
  source_url TEXT,
  -- Helpful normalized fields for search/filter UX.
  document_symbol TEXT,
  title_primary TEXT,
  publication_date_primary TEXT,
  -- Full Dublin Core fields (all 15) as arrays to preserve repeats.
  dc_title TEXT[] NOT NULL DEFAULT '{}',
  dc_creator TEXT[] NOT NULL DEFAULT '{}',
  dc_subject TEXT[] NOT NULL DEFAULT '{}',
  dc_description TEXT[] NOT NULL DEFAULT '{}',
  dc_publisher TEXT[] NOT NULL DEFAULT '{}',
  dc_contributor TEXT[] NOT NULL DEFAULT '{}',
  dc_date TEXT[] NOT NULL DEFAULT '{}',
  dc_type TEXT[] NOT NULL DEFAULT '{}',
  dc_format TEXT[] NOT NULL DEFAULT '{}',
  dc_identifier TEXT[] NOT NULL DEFAULT '{}',
  dc_source TEXT[] NOT NULL DEFAULT '{}',
  dc_language TEXT[] NOT NULL DEFAULT '{}',
  dc_relation TEXT[] NOT NULL DEFAULT '{}',
  dc_coverage TEXT[] NOT NULL DEFAULT '{}',
  dc_rights TEXT[] NOT NULL DEFAULT '{}',
  -- MARCXML payload storage (for full tag/subfield inspection).
  marcxml_xml TEXT,
  marcxml_json JSONB,
  -- Legacy generic payload columns retained for compatibility.
  metadata_xml TEXT,
  metadata_json JSONB,
  last_harvested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_recid ON digitallibrary.documents (recid);
CREATE INDEX IF NOT EXISTS idx_documents_datestamp ON digitallibrary.documents (datestamp DESC);
CREATE INDEX IF NOT EXISTS idx_documents_symbol ON digitallibrary.documents (document_symbol);
CREATE INDEX IF NOT EXISTS idx_documents_deleted ON digitallibrary.documents (deleted);
CREATE INDEX IF NOT EXISTS idx_documents_dc_identifier_gin ON digitallibrary.documents USING GIN (dc_identifier);
CREATE INDEX IF NOT EXISTS idx_documents_dc_subject_gin ON digitallibrary.documents USING GIN (dc_subject);
CREATE INDEX IF NOT EXISTS idx_documents_dc_title_gin ON digitallibrary.documents USING GIN (dc_title);
CREATE INDEX IF NOT EXISTS idx_documents_metadata_json_gin ON digitallibrary.documents USING GIN (metadata_json);

ALTER TABLE digitallibrary.documents
  ADD COLUMN IF NOT EXISTS marcxml_xml TEXT;

ALTER TABLE digitallibrary.documents
  ADD COLUMN IF NOT EXISTS marcxml_json JSONB;

CREATE INDEX IF NOT EXISTS idx_documents_marcxml_json_gin ON digitallibrary.documents USING GIN (marcxml_json);

CREATE OR REPLACE FUNCTION digitallibrary.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_set_updated_at ON digitallibrary.documents;
CREATE TRIGGER trg_documents_set_updated_at
BEFORE UPDATE ON digitallibrary.documents
FOR EACH ROW
EXECUTE FUNCTION digitallibrary.set_updated_at();
