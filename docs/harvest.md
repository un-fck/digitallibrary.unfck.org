# Harvest Runbook

## Overview

The UN Digital Library OAI-PMH endpoint only exposes ~19K sanctions records.
To get the full "Documents and Publications" catalog (~767K records), we use
the search API with `recid` range slicing and MARCXML output.

## Scripts

| Script | Purpose |
|--------|---------|
| `python/harvest_full.py` | One-time bulk harvest (~8h) |
| `python/harvest_incremental.py` | Nightly delta sync |
| `python/marc_parser.py` | Shared MARCXML parser |

## Bulk harvest (one-time)

```bash
# Apply schema first
psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
psql "$DATABASE_URL" -f sql/documents_tables.sql

# Start the harvest (~8 hours with crawl-delay)
DATABASE_URL="$DATABASE_URL" \
uv run python python/harvest_full.py
```

### Resume after interruption

```bash
DATABASE_URL="$DATABASE_URL" \
uv run python python/harvest_full.py --resume
```

### Test run (limited)

```bash
DATABASE_URL="$DATABASE_URL" \
uv run python python/harvest_full.py --max-records 500
```

### Dry run (no DB writes)

```bash
uv run python python/harvest_full.py --max-records 100 --dry-run
```

## Incremental sync (nightly)

Runs automatically via GitHub Action (`.github/workflows/nightly-sync.yml`)
at 03:00 UTC. Can also be run manually:

```bash
DATABASE_URL="$DATABASE_URL" \
uv run python python/harvest_incremental.py
```

Override the start date:

```bash
DATABASE_URL="$DATABASE_URL" \
uv run python python/harvest_incremental.py --since 2026-02-01
```

## How it works

### Search API

```
GET https://digitallibrary.un.org/search?p=recid:{start}->{end}&of=xm&rg=200&cc=Documents+and+Publications
```

- Returns up to 200 MARCXML records per request (~1s)
- `robots.txt` specifies `Crawl-Delay: 5`
- Record IDs in two dense blocks: 1–864K and 3.8M–4.1M

### MARC fields extracted

| DB column | MARC field | Description |
|-----------|-----------|-------------|
| `recid` | 001 | Record identifier |
| `document_symbol` | 191$a | UN document symbol |
| `title` | 245$a+$b | Title |
| `date_publication` | 269$a | Publication date |
| `languages` | 041$a | Language codes |
| `subjects` | 650$a | Subject headings |
| `corporate_authors` | 710 | Corporate authors |
| `un_body` | 981$a | UN body |
| `files` | 856 | File attachments |
| `vote_summary` | 996$a | Voting record |
| `marcxml` | (full) | Raw XML for re-parsing |

### State management

- **Bulk harvest**: state in `python/.harvest_state.json` (local file)
- **Incremental**: state in `digitallibrary.harvest_state` table (DB, for GH Action)

## Validation queries

```sql
SELECT count(*) AS total,
       count(*) FILTER (WHERE deleted_at IS NULL) AS active
FROM digitallibrary.documents;
```

```sql
SELECT recid, document_symbol, title, date_publication, un_body
FROM digitallibrary.documents
WHERE deleted_at IS NULL
ORDER BY date_publication DESC NULLS LAST
LIMIT 20;
```

```sql
SELECT un_body, count(*) AS n
FROM digitallibrary.documents
WHERE deleted_at IS NULL
GROUP BY un_body
ORDER BY n DESC;
```

## GitHub Action

- Workflow: `.github/workflows/nightly-sync.yml`
- Schedule: daily at 03:00 UTC
- Secret required: `DATABASE_URL`
- Can be triggered manually via `workflow_dispatch`
