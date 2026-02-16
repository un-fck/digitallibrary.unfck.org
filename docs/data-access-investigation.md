# UN Digital Library — Complete Data Access Investigation

## TL;DR

The OAI-PMH endpoint only exposes **19,128 sanctions records** out of **1,176,440 total**.
The best route to a full harvest is the **search API with `recid` range slicing** returning MARCXML,
which can pull the entire catalog in ~8 hours (respecting crawl-delay).

---

## Catalog overview

| Metric | Value |
|--------|-------|
| Total records | **1,176,440** |
| Platform | Invenio 28.1.2 (TIND fork) |
| OAI-exposed records | 19,128 (sanctions only) |
| Admin contact | library-ny@un.org |

### Record ID space

Records live in two contiguous blocks with a large gap:

| Block | ID range | Notes |
|-------|----------|-------|
| 1 | `1` – `~864,335` | Dense, nearly every integer has a record |
| 2 | `~3,797,692` – `~4,102,674` | Dense |
| Gap | `~864,336` – `~3,797,691` | Empty |

### Collections

**By resource type** (1,176,440 total):

| Collection | Records |
|-----------|---------|
| Documents and Publications | 767,252 |
| Speeches | 382,453 |
| Voting Data | 23,576 |
| Maps | 3,003 |
| Images and Sounds | 271 |
| Datasets | 8 |

**By UN body** (1,117,885 total):

| Body | Records |
|------|---------|
| General Assembly | 519,441 |
| Economic and Social Council | 226,416 |
| Security Council | 191,500 |
| Human Rights Bodies | 105,833 |
| Secretariat | 82,529 |
| Economic Commissions | 70,088 |
| Programmes and Funds | 37,455 |
| Other UN Bodies | 23,249 |
| Trusteeship Council | 21,082 |
| Research Institutions | 5,235 |
| ICJ | 422 |

---

## Access methods investigated

### 1. OAI-PMH (`/oai2d`) — current approach

**Verdict: only 1.6% of catalog**

- `ListSets` returns a single set: `sanctions`
- `completeListSize` is 19,128 regardless of date range
- All records carry `<setSpec>sanctions</setSpec>`
- Resumption tokens work (100 records/page)
- Metadata formats: `marcxml`, `oai_dc`, `oai_openaire`

Not suitable for complete harvest. Fine for keeping sanctions data current.

### 2. Search API (`/search`) — best bulk route

**Verdict: viable for full harvest via recid range slicing**

Base URL: `https://digitallibrary.un.org/search`

**Parameters:**

| Param | Description | Notes |
|-------|-------------|-------|
| `p` | Search pattern | Supports `recid:N->M` range syntax |
| `of` | Output format | `id` (ID array), `xm` (MARCXML), `recjson` (JSON), `hb` (HTML) |
| `rg` | Records per page | Hard cap at **200** regardless of value |
| `jrec` | Jump to record | **Broken** for search; only works in RSS |
| `sf` | Sort field | `recid`, etc. |
| `so` | Sort order | `a` (ascending), `d` (descending) |
| `cc` | Collection | e.g. `Documents and Publications`, `Voting Data` |

**Format performance (200 records):**

| Format | Time | Size | Notes |
|--------|------|------|-------|
| `of=id` | ~0.5s | ~2 KB | Just integer IDs |
| `of=xm` | ~0.9s | ~1.3 MB | Full MARC21 XML |
| `of=recjson` | 40–90s | ~560 KB | **Extremely slow**, unusable at scale |

**Critical limitations:**
- Max 200 records per response (hard cap)
- `jrec` pagination is broken for search (returns 0 results at offset > 1)
- Standard search result count appears bugged (always says `1`)

### 3. Invenio REST API (`/api/`)

**Verdict: not available**

- `/api/` → 501 Method Not Allowed
- `/api/records/` → 404

### 4. Individual record export

**Verdict: too slow for bulk, useful for targeted fetches**

```
/record/<ID>?of=recjson    — JSON
/record/<ID>/export/xm     — MARCXML
```

~0.7s per record → ~228 hours for full catalog. Not viable for bulk.

### 5. RSS feed (`/rss`)

**Verdict: useful for monitoring, not for bulk**

- Returns `opensearch:totalResults` (confirms 1,176,440)
- `jrec` pagination works here (unlike search)
- Hard cap at ~1,000 records
- Good for checking recent additions

### 6. SRU

**Verdict: not available**

`/sru` returns 404.

### 7. Sitemap

**Verdict: viable for ID extraction**

- `robots.txt` references `/sitemap_index.xml.gz`
- 139 gzipped sitemaps (`sitemap_01.xml.gz` – `sitemap_139.xml.gz`)
- ~23,500 URLs per file (~9,900 unique records + PDF file URLs)
- ~1.38M URLs total (includes both `/record/X` and `/record/X/files/...`)

Can extract all record IDs, then fetch metadata in batches.

---

## Recommended harvest strategy

### Primary: search API with `recid` range slicing

```
GET /search?p=recid:<start>-><end>&of=xm&rg=200
```

**Algorithm:**

1. Walk the two ID blocks in windows of ~199 IDs:
   - Block 1: `recid:1->199`, `recid:200->398`, … up to `~864,400`
   - Block 2: `recid:3797600->3797798`, … up to `~4,102,800`
2. Each request returns up to 200 MARCXML records in ~1s
3. If a batch returns exactly 200, the range may be truncated — narrow and re-query
4. Parse MARCXML, upsert into database

**Estimates:**

| Metric | Value |
|--------|-------|
| Total queries | ~5,900 |
| Time per query | ~1s |
| Raw time | ~1.6 hours |
| With `Crawl-Delay: 5` (robots.txt) | **~8.2 hours** |
| Data volume | ~38 GB MARCXML (uncompressed) |

### Alternative: sitemap → batch fetch

1. Download 139 gzipped sitemaps
2. Extract unique record IDs from `/record/\d+` URLs
3. Batch-fetch metadata using `recid:X->Y` search queries with `of=xm`

Advantage: guaranteed complete ID coverage. Disadvantage: extra step.

### For incremental updates

- **Sanctions subset**: continue using OAI-PMH with `--resume`
- **Everything else**: re-scan sitemaps periodically for new IDs, or use RSS to detect recent additions, then fetch new records via search API
- **Date-scoped updates**: `p=recid:1->864400 AND datestamp:2025-06-01->2025-06-30` (if Invenio supports combined queries — needs testing)

---

## What about the 500-record search limit?

The app's search endpoint (`/api/documents/search`) hits the database, not the UNDL API, so the 500-record observation is likely either:
- A `LIMIT 500` in the app's SQL query, or
- The UNDL search's 200-record cap (which would appear to "repeat" due to broken pagination)

The `recid` range slicing strategy **completely bypasses this problem** because each request asks for a specific ID window, not paginated results. As long as each window returns ≤ 200 records (which a range of ~199 IDs guarantees), we get everything.

---

## `robots.txt` compliance notes

```
User-agent: *
Crawl-Delay: 5
Disallow: /rss
Disallow: /search
Disallow: /record/*/export/*
```

The `Disallow: /search` means crawlers shouldn't hit the search endpoint. For a one-time institutional harvest:
- Contact `library-ny@un.org` to request permission / a data dump
- Alternatively, use the sitemap (explicitly published for discovery) to get IDs, then fetch individual records via `/record/<ID>?of=xm` which is not disallowed

---

## Next steps

1. **Contact library-ny@un.org** — ask if they can provide a bulk export or expand OAI sets beyond sanctions. This is the cleanest path.
2. **Prototype the recid-range harvester** — build a script that walks ID ranges, fetches MARCXML, and upserts. Start with a small range to validate.
3. **Test sitemap extraction** — download a few sitemaps, extract IDs, compare against known ranges to validate coverage.
4. **Decide on scope** — do we need all 1.17M records, or can we scope to specific collections (e.g., Security Council + General Assembly resolutions)?
