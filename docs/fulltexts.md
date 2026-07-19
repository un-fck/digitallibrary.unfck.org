# Document Full-Text Pipeline (Track A)

Fetch, archive, convert, and extract the full text of UN resolutions, decisions,
and presidential statements — beyond the MARC metadata the harvest already
mirrors. This document covers **Track A, stage 1**: getting native files onto
disk, normalizing them to `.docx`, and defining the raw extraction layer.

## Two-layer model

The pipeline deliberately separates **archival + low-interpretation extraction**
from **semantic interpretation**:

1. **`digitallibrary.document_files`** — a fetch/convert/extract *ledger*. One row
   per `(symbol_normalized, lang)`. Tracks format, hashes, archive paths,
   conversion, and status through the pipeline.
2. **`digitallibrary.document_paragraphs_raw`** — a *low-interpretation*
   extraction layer. Document-ordered paragraphs with just enough structure
   (numbering, styles, italics/bold, indentation, table cells, hyperlinks,
   footnotes) to reconstruct meaning later, but **no** semantic labelling.
3. **Semantic layer — not built yet.** Normalized preambular/operative
   paragraphs, mandate objects, cross-references, etc. will land as
   **migration `003`** *after* we iterate on the raw layer and learn what the
   documents actually look like across the format eras. Building it now would
   bake in guesses.

The re-parse philosophy: **the SSD archive is ground truth**, and
`document_paragraphs_raw` is a disposable re-parse substrate. Any extractor bug
is fixed by re-running extraction over the archived files — we never re-fetch
from ODS to fix a parsing mistake, and never treat the raw table as canonical.

## Archive location

```
/Volumes/SSDAStorage/digitallibrary-fulltexts/
  original/    native ODS files, as fetched   (e.g. A_RES_60_1.doc)
  converted/   LibreOffice-produced .docx      (e.g. A_RES_60_1.docx)
```

This is a **local SSD on David's Mac**, not cloud storage. Consequently **this
pipeline runs locally, not in CI** (unlike the nightly MARC harvest). Ledger rows
store archive paths *relative* to this root, so the root can move (override with
`FULLTEXT_ARCHIVE_ROOT`). `original/` and `converted/` already exist.

## Corpus definition

Candidate families: **8 symbol families**, English, published **1994-01-01 or
later**:

```
A/RES/  A/DEC/  S/RES/  S/PRST/  E/RES/  E/DEC/  A/HRC/RES/  A/HRC/PRST/
```

Selection query (see `python/fulltext_fetch.py`): `deleted_at IS NULL`,
`date_publication >= '1994-01-01'`, English (lenient: `languages` empty **or**
`'eng' = ANY(languages)`), `symbol_normalized` matching the family regex,
`DISTINCT ON (symbol_normalized)` keeping the highest `recid`.

**Two families are excluded from default catalog selection** because they are
structurally absent from ODS (verified by hand, 0/85 fetched in the sample run):

- **`A/DEC/*` and `E/DEC/*` (GA and ECOSOC decisions).** Decisions are not issued
  as standalone ODS documents — they are only published inside *compilation
  volumes* (e.g. the GA "Resolutions and Decisions" fascicles). Fetching them at
  the individual-symbol level cannot work. **Future work:** a compilation-volume
  approach (fetch the volume, then split decisions out) is needed to cover
  decisions full text; it is not built yet. They remain reachable via
  `--symbols-file` if you list them explicitly.

Not excluded, but expect misses: **early Human Rights Council sessions** (roughly
`A/HRC/RES/1/*` through `A/HRC/RES/11/*`, 2006–2009) are mostly absent from ODS
too, while later sessions are fine. HRC is *not* excluded — the fetch retry logic
simply marks the absent early ones `unavailable`.

**Bracket pseudo-symbols.** Digital Library invents sub-record symbols like
`A/RES/50/204[A]` or `A/RES/63/108[B-IV]` for the parts of a combined resolution.
ODS only knows the parent (`A/RES/50/204`). The fetcher **never issues a request
containing `[`**: bracketed targets are collapsed onto their parent — dropped
when the parent is already a target or already in the ledger (the parent's file
covers all parts), otherwise the parent file is fetched and stored under the
parent `symbol_normalized`. Every run prints how many were collapsed.

Out of scope for now: **~17.7k pre-1994 documents are PDF-only** on ODS (no Word
source), so text extraction there needs a different (OCR/PDF) path. Deferred.

## Format eras (empirical)

ODS returns the *native* file; `t=docx` does **not** convert. What comes back
depends on when the document was produced:

| Era          | Format            | Magic bytes      | Handling                    |
|--------------|-------------------|------------------|-----------------------------|
| ~1993–2000   | WordPerfect 5.1   | `FF 57 50 43`    | convert → docx (LibreOffice)|
| ~2000s–2010s | binary `.doc`     | `D0 CF 11 E0`    | convert → docx (LibreOffice)|
| recent       | real `.docx`      | `PK` (zip)       | ready as-is                 |
| —            | PDF               | `%PDF`           | out of scope (pre-1994)     |
| failure      | HTML "not found"  | starts with `<`  | status `unavailable`        |

**Critical:** a *failed* lookup still returns **HTTP 200** with a ~1.3 KB
`text/html` page. Format is therefore sniffed from **magic bytes, never the
Content-Type header**. A curl-like `User-Agent` (`curl/8.7.1`) avoids the WAF
JS challenge.

**Transient soft-block.** In the 317-doc sample run (1 req/s), after ~250–300
consecutive requests ODS began returning that same 1.3 KB HTML page for
documents that *are* available (the same symbols succeeded again minutes later).
The block is invisible in the response — it looks exactly like a genuine
not-found. The fetcher defends against it on two levels: per-symbol soft retries
(30 s then 120 s before recording `unavailable`) and the politeness/circuit-
breaker machinery in the next section that keeps request volume far below the
threshold that triggered the block.

## Status lifecycle

`document_files.status`:

- `fetched` — original archived, format sniffed.
- `unavailable` — ODS returned HTML/unknown *even after the soft-block retries*
  (30 s + 120 s); no file saved. Because ODS transiently soft-blocks (its "not
  available" HTML page is byte-identical whether the doc is genuinely missing or
  we are just being throttled), some `unavailable` rows are false negatives —
  recover them with a later `--recheck-unavailable` pass.
- `failed` — network/HTTP error after retries; **retried** on the next fetch run.
- `converted` — a valid `.docx` exists (native, `converted_path` NULL; or produced
  from doc/wpd, `converted_path` set).
- `convert_failed` — LibreOffice produced nothing usable; `error` records why.
- `extracted` — raw paragraphs written (later stage).

## Runbook

All commands run **locally** (archive is on the local SSD), from the repo root,
with `DATABASE_URL` in `.env`.

### 1. Apply the migration

```bash
psql "$DATABASE_URL" -f sql/migrations/002_add_fulltexts.sql
# (from-scratch equivalent: sql/schema/fulltext_tables.sql)
```

### 2. Fetch native files from ODS

The fetcher runs **deliberately slowly to avoid ODS soft-blocking** (see Format
eras above). It targets **≲1200 requests/hour**: ~3 s between requests with ±30%
jitter, plus a **3–5 min rest break after every ~150 requests**, plus long
back-offs on any sign of throttling. **Budget about a day of wall-clock time for
the full backfill.** This is by design — start it and leave it running.

Anti-block behaviour, in order of severity:

- **Soft retries.** An html/unknown response is retried up to 2 more times
  (30 s, then 120 s) before the symbol is recorded `unavailable`.
- **Rest breaks.** After every ~150 requests, a random 3–5 min pause regardless
  of whether anything failed.
- **Back-off.** HTTP 429/403 or a connection reset ⇒ a 10-min pause before
  retrying; it also counts toward the circuit breaker.
- **Circuit breaker (tiered).** 8 consecutive block/miss outcomes ⇒ pause
  15 min (1st trip), 60 min (2nd trip), then **exit cleanly on the 3rd** — a
  crash is worse than stopping, and the run is resumable.

Each progress line logs cumulative requests and current requests/hour so the
friendliness is auditable.

```bash
# Full corpus — expect ~a day of wall clock. Run detached (tmux/nohup).
uv run python python/fulltext_fetch.py

# Try a small batch first
uv run python python/fulltext_fetch.py --limit 100 --rate 4

# Fetch a specific priority list (one symbol per line). This is also the ONLY
# way to reach A/DEC and E/DEC decisions, which are excluded by default.
uv run python python/fulltext_fetch.py --symbols-file priority.txt

# Preview targets without fetching (also prints excluded/collapsed counts)
uv run python python/fulltext_fetch.py --dry-run
```

**Recommended two-pass workflow.** Because some `unavailable` rows are transient
soft-block false negatives, do a bulk pass first, then a slow second pass that
re-probes only the misses:

```bash
# Pass 1 — bulk backfill (a day-ish)
uv run python python/fulltext_fetch.py

# Pass 2 — re-probe everything marked 'unavailable'; rows that now fetch are
# overwritten to 'fetched'. Structurally-absent decisions are skipped here too.
uv run python python/fulltext_fetch.py --recheck-unavailable
```

Repeat pass 2 as needed; genuinely-absent documents (early HRC, etc.) stay
`unavailable` no matter how often you re-probe.

Safe to Ctrl-C and re-run: already-fetched/unavailable symbols are skipped (except
under `--recheck-unavailable`, which deliberately revisits them); only `failed`
rows are retried on a normal run. A resume watermark is persisted to
`digitallibrary.harvest_state` under key `fulltext_fetch`.

### 3. Convert legacy files to docx

```bash
# Native docx flagged ready in bulk; doc + wpd converted with 4 workers
uv run python python/fulltext_convert.py

# More parallelism / a capped run
uv run python python/fulltext_convert.py --workers 8 --limit 500
```

Requires LibreOffice (`soffice` on PATH, or set `SOFFICE_BIN`). Each worker uses
an isolated `-env:UserInstallation` profile under `/tmp/lo_profile_<n>` so
parallel instances don't collide over the shared profile.

### 4. Extract raw paragraphs

Later stage (extractor script + `extractor_version`). The table
`digitallibrary.document_paragraphs_raw` and its status value `extracted` are
already defined so the schema is stable before that code lands.

## Files

| File | Role |
|------|------|
| `sql/schema/fulltext_tables.sql` | Re-runnable table definitions |
| `sql/migrations/002_add_fulltexts.sql` | Idempotent delta migration |
| `python/fulltext_common.py` | Shared helpers (archive, sniff, DB, ledger, state) |
| `python/fulltext_fetch.py` | Stage 1 — fetch + archive from ODS |
| `python/fulltext_convert.py` | Stage 2 — doc/wpd → docx via LibreOffice |
