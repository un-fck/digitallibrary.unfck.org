# documents.unfck.org

Next.js app + FastAPI backend for exploring UN Digital Library metadata, backed by PostgreSQL and synced from the UN Digital Library search API.

## What this app does

- Public home page (`/`) with full-text document search.
- Detail view showing normalized MARC fields, JSON tree, and raw MARCXML.
- REST API (`/v1/`) for programmatic access to 767K+ UN documents.
- Developer dashboard (`/developer`) for API key management and usage stats.
- Auth flow (`/login`, `/verify`) — open to anyone via magic link.

## REST API

The FastAPI backend exposes a public API at `/v1/`. Interactive docs are at `/v1/docs` (Swagger) and `/v1/redoc`.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/documents` | List / search documents with filters |
| `GET` | `/v1/documents/recid/{recid}` | Get document by record ID |
| `GET` | `/v1/documents/{symbol}` | Get document by UN symbol |
| `GET` | `/v1/documents/recid/{recid}/marcxml` | Get raw MARCXML for a record |
| `GET` | `/v1/search` | Full-text search |
| `GET` | `/v1/facets` | Faceted search / filtering |
| `GET` | `/v1/stats` | Collection statistics |

### Authentication

Anonymous access is available at 10 req/min. For higher limits, sign up for a free API key — open to anyone, no UN affiliation required.

```
POST /v1/keys/signup    — request a key (sends verification email)
POST /v1/keys/verify    — verify email and receive key
GET  /v1/keys/me        — key info (requires key)
GET  /v1/keys/me/usage  — usage stats (requires key)
POST /v1/keys/rotate    — revoke + reissue (requires key)
```

Pass your key via header or query param:

```bash
curl -H "Authorization: Bearer undl_live_xxxx" \
  https://documents.unfck.org/v1/documents?q=A/RES/78

# or
curl "https://documents.unfck.org/v1/search?q=climate+change&api_key=undl_live_xxxx"
```

### Rate limits

| Tier | Requests/min | Daily |
|------|-------------|-------|
| Anonymous | 10 | 100 |
| Free | 60 | 10,000 |
| Research | 300 | 100,000 |
| Institutional | 1,000 | Unlimited |

## Data pipeline

### Full harvest

Bulk harvest of all "Documents and Publications" from the UN Digital Library search API using record ID range slicing. Fetches MARCXML, parses 30+ structured fields, and upserts into PostgreSQL.

```bash
uv run python python/harvest_full.py                   # fresh start
uv run python python/harvest_full.py --resume          # continue from checkpoint
uv run python python/harvest_full.py --max-records 500 # limited test run
uv run python python/harvest_full.py --dry-run         # parse only, no DB writes
```

### Incremental harvest

Nightly sync of new/changed records since the last run. Designed for GitHub Actions — state is stored in the database so it works across ephemeral CI runners.

```bash
uv run python python/harvest_incremental.py                    # auto (reads watermark from DB)
uv run python python/harvest_incremental.py --since 2026-02-15
uv run python python/harvest_incremental.py --dry-run
```

### MARC parser

`python/marc_parser.py` parses MARC21 XML into 30+ structured fields: subjects, authors, agendas, voting records, files, languages, and more.

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
psql "$DATABASE_URL" -f sql/schema/auth_tables.sql
psql "$DATABASE_URL" -f sql/schema/db_user.sql
psql "$DATABASE_URL" -f sql/schema/api_tables.sql
```

4. Apply document schema:

```bash
psql "$DATABASE_URL" -f sql/schema/documents_tables.sql
```

Then apply any incremental migrations in order:

```bash
for f in sql/migrations/*.sql; do psql "$DATABASE_URL" -f "$f"; done
```

5. Run full harvest (or incremental):

```bash
DATABASE_URL="$DATABASE_URL&sslrootcert=/etc/ssl/cert.pem" \
uv run python python/harvest_full.py --resume
```

6. Start the FastAPI backend:

```bash
uv run uvicorn api.main:app --reload --port 8000
```

7. Run the Next.js frontend:

```bash
pnpm dev
```

## Core files

### Frontend (Next.js)

- `src/app/page.tsx`: home page with document search
- `src/app/about/page.tsx`: about page
- `src/app/developer/page.tsx`: developer dashboard (API key management)
- `src/components/DocumentExplorer.tsx`: search + metadata render (table/json/xml)
- `src/components/DeveloperDashboard.tsx`: API key, usage stats, quick start

### API (FastAPI)

- `api/main.py`: FastAPI app with CORS, rate limiting, router mounting
- `api/routers/documents.py`: document list/detail/symbol/marcxml endpoints
- `api/routers/search.py`: full-text search endpoint
- `api/routers/facets.py`: faceted filtering
- `api/routers/stats.py`: collection statistics
- `api/routers/api_keys.py`: API key signup, verify, info, rotate
- `api/services/key_service.py`: key generation, hashing, usage tracking
- `api/services/rate_limit.py`: rate limiting via slowapi

### Data pipeline (Python)

- `python/harvest_full.py`: bulk harvest via search API with recid range slicing
- `python/harvest_incremental.py`: nightly incremental sync
- `python/marc_parser.py`: MARC21 XML to structured fields

### Database

- `sql/schema/`: re-runnable from-scratch definitions
  - `documents_tables.sql`: document metadata schema
  - `auth_tables.sql`: users, magic tokens, allowed domains
  - `api_tables.sql`: API users, keys, verification tokens, usage log
  - `db_user.sql`, `rate_limit_table.sql`
- `sql/migrations/`: numbered incremental migrations, applied in order on top of the schema

## Documentation

- `docs/data-model.md`
- `docs/oai-sync.md`
- `docs/app-api.md`
