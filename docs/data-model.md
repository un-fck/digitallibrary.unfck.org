# Data Model

## Table

Primary table:

- `digitallibrary.documents`

This table is populated from OAI-PMH and stores:

1. OAI header fields
- `oai_identifier` (unique)
- `recid`
- `datestamp`
- `deleted`
- `source_set`
- `source_url`

2. Normalized DC fields
- `document_symbol`
- `title_primary`
- `publication_date_primary`
- `dc_title`
- `dc_creator`
- `dc_subject`
- `dc_description`
- `dc_publisher`
- `dc_contributor`
- `dc_date`
- `dc_type`
- `dc_format`
- `dc_identifier`
- `dc_source`
- `dc_language`
- `dc_relation`
- `dc_coverage`
- `dc_rights`

3. MARC payload storage
- `marcxml_xml` (raw XML payload from `metadataPrefix=marcxml`)
- `marcxml_json` (parsed MARC record with leader/controlfields/datafields/subfields)

4. Legacy payload columns
- `metadata_xml`
- `metadata_json`

These are retained for compatibility. Current sync flow treats DC as normalized columns and MARC as the payload format.

## Indexing

Current indexes support:

- recency/date filtering (`datestamp`)
- symbol filtering (`document_symbol`)
- array lookups (`dc_identifier`, `dc_subject`, `dc_title`)
- MARC JSON exploration (`marcxml_json` GIN index)

## Why this shape

- DC is simple and query-friendly, so it is stored as explicit columns.
- MARC is high-fidelity bibliographic structure, so it is preserved as JSON/XML payload.
- One row per `oai_identifier` keeps merge semantics straightforward when syncing multiple prefixes.

