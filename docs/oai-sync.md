# OAI Sync Runbook

## Endpoint

- Base URL: `https://digitallibrary.un.org/oai2d`

## Supported prefixes used in this repo

- `oai_dc`
- `marcxml`

The sync script supports running both in one command:

```bash
uv run python python/sync_oai_to_db.py --prefixes oai_dc,marcxml
```

This is two sequential OAI passes, one per prefix.

## Script

- `python/sync_oai_to_db.py`

Key behavior:

- uses OAI `ListRecords`
- handles `resumptionToken` paging
- upserts rows into `digitallibrary.documents`
- keeps separate resume state per prefix in:
  - `python/.oai_sync_state.json`

## Common commands

### First sync from 2025 onward

```bash
DATABASE_URL="$DATABASE_URL&sslrootcert=/etc/ssl/cert.pem" \
uv run python python/sync_oai_to_db.py \
  --from 2025-01-01T00:00:00Z \
  --prefixes oai_dc,marcxml
```

### Resume interrupted sync

```bash
DATABASE_URL="$DATABASE_URL&sslrootcert=/etc/ssl/cert.pem" \
uv run python python/sync_oai_to_db.py \
  --from 2025-01-01T00:00:00Z \
  --prefixes oai_dc,marcxml \
  --resume
```

### Limited test run

```bash
DATABASE_URL="$DATABASE_URL&sslrootcert=/etc/ssl/cert.pem" \
uv run python python/sync_oai_to_db.py \
  --from 2025-01-01T00:00:00Z \
  --prefixes oai_dc,marcxml \
  --max-records 100
```

## Operational notes

- OAI can throttle; keep `--sleep` non-zero for safer long runs.
- For production, run sync as scheduled job and always pass `--resume`.
- `oai_dc` and `marcxml` do not always complete in lockstep if runs are interrupted; rerun with `--resume` until both prefixes finish.

## Validation queries

```sql
select
  count(*) as total,
  count(*) filter (where deleted=false) as active,
  count(*) filter (where marcxml_json is not null) as with_marc
from digitallibrary.documents;
```

```sql
select recid, document_symbol, datestamp
from digitallibrary.documents
where deleted=false
order by datestamp desc
limit 20;
```

