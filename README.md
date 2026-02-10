# digitallibrary.unfck.org

Next.js app for exploring UN Digital Library metadata, backed by PostgreSQL and synced from UNDL OAI-PMH.

## What this app does

- Public home page (`/`) with document search.
- Search over synced metadata from `digitallibrary.documents`.
- Detail view showing:
  - normalized Dublin Core fields
  - MARC payload as JSON tree and XML code view
- Auth flow still exists (`/login`, `/verify`) for protected areas.

## Data source

UNDL OAI-PMH endpoint:

- `https://digitallibrary.un.org/oai2d`

Formats used:

- `oai_dc` for normalized `dc_*` columns
- `marcxml` for full MARC payload (`marcxml_json`, `marcxml_xml`)

Important: OAI returns one metadata format per request. “Both in one go” means one sync command that runs both format passes sequentially.

## Quick start

1. Install JS deps:

```bash
pnpm install
```

2. Install Python deps via `uv`:

```bash
uv sync
```

3. Apply DB setup SQL (admin):

```bash
psql "$DATABASE_URL" -f sql/auth_tables.sql
psql "$DATABASE_URL" -f sql/create_digitallibrary_user.sql
```

4. Apply document schema:

```bash
psql "$DATABASE_URL" -f sql/documents_tables.sql
```

5. Run OAI sync (DC + MARC) from 2025:

```bash
DATABASE_URL="$DATABASE_URL&sslrootcert=/etc/ssl/cert.pem" \
uv run python python/sync_oai_to_db.py \
  --from 2025-01-01T00:00:00Z \
  --prefixes oai_dc,marcxml \
  --resume
```

6. Run app:

```bash
pnpm dev
```

## Core files

- `sql/documents_tables.sql`:
  document metadata schema
- `python/sync_oai_to_db.py`:
  dual-prefix OAI sync into Postgres
- `src/app/api/documents/search/route.ts`:
  search API from `digitallibrary.documents`
- `src/app/api/documents/[recid]/route.ts`:
  document detail API (includes MARC payload)
- `src/components/DocumentExplorer.tsx`:
  search + metadata render (table/json/xml)

## Documentation

- `docs/data-model.md`
- `docs/oai-sync.md`
- `docs/app-api.md`

