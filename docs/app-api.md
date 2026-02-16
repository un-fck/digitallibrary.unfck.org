# App API Notes

## Search endpoint

- `GET /api/documents/search?q=<term>`

Backed by `digitallibrary.documents`.

Search strategy:

- prefix match on `document_symbol`
- trigram `ILIKE` on `title` (uses `pg_trgm` GIN index)
- exact match on `recid` (as string)

Returns: `recid`, `symbol`, `title`, `date`, `body`, `type`

## Detail endpoint

- `GET /api/documents/:recid`

Returns all extracted MARC fields plus raw `marcxml` for the XML viewer.

Structured fields returned as JSON:
- `files` — `[{url, lang, size, uuid}]`
- `corporate_authors` — `[{name, type}]`
- `agenda_items` — `[{doc, item, desc, topic}]`
- `related_documents` — `[{symbol, relationship}]`

Array fields: `languages`, `subjects`, `notes`, `collections`

## UI components

- `src/components/DocumentSearch.tsx`
  - autocomplete input with dropdown showing symbol, title, body, type
- `src/components/DocumentExplorer.tsx`
  - selection state
  - tabs: Metadata (semantic sections) / JSON / MARCXML

## Public routing behavior

Main page is public:

- `/` is not auth-gated in `src/proxy.ts`

Protected routes still require valid session token.
