# Data Model

## Table

Primary table: `digitallibrary.documents`

Populated from UNDL search API using MARCXML output. One row per record, keyed by `recid` (MARC 001).

## Columns

### Identity
- `recid` — INTEGER PRIMARY KEY (MARC 001)
- `document_symbol` — UN document symbol (MARC 191$a)
- `symbol_body`, `symbol_session`, `symbol_committee` — parsed symbol components

### Title & description
- `title` — MARC 245$a + $b
- `title_statement` — MARC 245$c (responsibility)
- `summary` — MARC 520$a (abstract, when present)
- `notes` — TEXT[] of MARC 500$a values

### Classification
- `doc_class_code`, `doc_class_desc` — MARC 089
- `un_body`, `un_committee` — MARC 981
- `resource_type`, `resource_subtype` — MARC 989
- `collections` — TEXT[] of MARC 980$a values
- `subjects` — TEXT[] of MARC 650$a subject headings

### Dates & publication
- `date_publication` — DATE from MARC 269$a
- `date_text` — human-readable date from MARC 260$c
- `publisher`, `pub_place` — MARC 260
- `physical_desc` — MARC 300$a

### Languages
- `languages` — TEXT[] of ISO 639 codes from MARC 041$a

### Authors
- `corporate_authors` — JSONB array: `[{name, type}]` from MARC 710

### Files
- `files` — JSONB array: `[{url, lang, size, uuid}]` from MARC 856

### Voting & agenda
- `vote_summary` — MARC 996$a
- `agenda_items` — JSONB array: `[{doc, item, desc, topic}]` from MARC 991
- `related_documents` — JSONB array: `[{symbol, relationship}]` from MARC 993

### Raw storage
- `marcxml` — full MARCXML response (enables re-parsing without re-harvesting)

### Housekeeping
- `deleted_at` — soft delete timestamp (NULL = active)
- `harvested_at`, `created_at`, `updated_at`

## State table

`digitallibrary.harvest_state` — key-value store for sync watermarks.

Used by the incremental harvester to persist state across ephemeral GH Action runners.

## Indexing

- B-tree on `document_symbol`, `date_publication`, `un_body`, `resource_type`
- Partial index on `deleted_at` (only non-null rows)
- GIN on `subjects`, `languages`, `collections` (array containment)
- GIN trigram on `title` (efficient `ILIKE '%term%'` via `pg_trgm`)

## Why this shape

- Extracted columns for every field the UI searches or displays — no runtime JSON parsing needed
- JSONB for structured repeated fields (files, authors, agenda) — queryable but avoids junction tables
- TEXT[] for flat repeated values (subjects, languages) — simpler than JSONB for plain strings
- Raw `marcxml` stored for schema evolution — re-parse all records without re-harvesting
- `recid` as natural primary key — matches the source system, no surrogate needed
