# App API Notes

## Search endpoint

- `GET /api/documents/search?q=<term>`

Backed by `digitallibrary.documents`.

Search strategy:

- prefix match on `document_symbol`
- full-text-like `ILIKE` on `title_primary`
- lookup in `dc_identifier` array

Result label strategy:

1. `document_symbol` when present
2. first non-URL `dc_identifier`
3. fallback (rare)

This avoids displaying raw record URLs as the primary dropdown label.

## Detail endpoint

- `GET /api/documents/:recid`

Returns:

- header/core fields
- all normalized `dc_*` arrays
- payload fields mapped for UI viewer:
  - `metadata_json` => `marcxml_json`
  - `metadata_xml` => `marcxml_xml`

The UI uses this to render:

- metadata table
- JSON tree
- XML code block

## UI components

- `src/components/DocumentSearch.tsx`
  - autocomplete input and dropdown
- `src/components/DocumentExplorer.tsx`
  - selection state
  - tabs for table/json/xml render

## Public routing behavior

Main page is public:

- `/` is not auth-gated in `src/proxy.ts`

Protected routes still require valid session token.

