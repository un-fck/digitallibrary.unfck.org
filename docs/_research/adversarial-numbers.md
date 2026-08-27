# Adversarial review of the fulltext headline numbers

**FAIL — decisions recall 3,348/3,847 printed (87.0%), 499 decisions printed in volumes we already hold but never stored (249 of them silently, invisible to every check); volume gate is 3/89, not 89/89; HRC session 10 is 51/51 missing; the coverage table omits 1,891 never-probed symbols. 2 numbers must be retracted, 5 must be restated, 0 stand as written.**

Reviewer: adversarial statistician, fresh context, did not build any of this. Read-only on
DB and archive. Method: every denominator rebuilt from the source (archived PDFs/docx, the
DL catalog) with my own code, importing nothing from the pipeline except where I explicitly
say I ran the pipeline's own tool to reproduce its output.

Date: 2026-07-27. Target: `feature/fulltexts` @ `79bdd29`, DB `un80-dev-pg`.

---

## Corrected claims table

| # | Claim as made | Independently derived | Gap | Verdict |
|---|---|---|---|---|
| 1 | 25,734 documents have structured fulltext | 25,734 rows — real; but ≥127 are empty shells or misattributed, 3,590 have no archive file and no fidelity gate | count ✓, meaning ✗ | **RESTATE** |
| 2 | 100.0000 % text preservation | 100.0000 % over **15,141 of 25,734** docs (58.8 %); 10,593 silently skipped | scope 41.2 % | **RESTATE** |
| 3 | 89/89 volumes pass the volume gate | **3/89** under a gate that can fail | 86 volumes | **RETRACT** |
| 4 | 3,354 GA/ECOSOC decisions recovered | 3,348 of **3,847** printed = **87.0 %** (catalog denominator: 3,354/4,303 = **78.0 %**) | 499–949 missing | **RESTATE** |
| 5 | 262 early-HRC documents | 262 of **320** catalog = **81.9 %**; session 10 is **0 of 51** | 58 missing | **RESTATE** |
| 6 | Coverage buckets (parsed/bracket/scans/decisions/absent) | Buckets do not partition; **1,891 never-probed symbols** are in no bucket; ~2,247 unaccounted | ~5.4 % of catalog | **RESTATE** |
| 7a | ~41 % of pre-1994 PDFs are pure scans | 41.35 % **of the 11,577 triaged**; 32.9 % of the 14,567 fetchable; 2,990 never fetched | denominator | **RESTATE** |
| 7b | OCR yield 80–95 % by era | 0 documents OCR'd and parsed; n=5 ground-truth docs, all 1994–96 born-digital | unmeasured | **RETRACT** |
| 7c | Scan/text threshold sound at the boundary | Empty valley 1.0 → 354.0 chars/page; 1/12,012 misclassified | — | **stands** |

---

## Claim 1 — "25,734 documents have structured fulltext"

**Reproduces exactly.** `document_paragraphs` holds 25,734 distinct `symbol_normalized`,
all `parser_version = sem-v4`, matching `document_parses` 1:1.

**Composition** (source-derived; catalog = the 8 families in `digitallibrary.documents`):

| Slice | n |
|---|---:|
| volume-split children (`document_paragraphs_raw.source_symbol IS NOT NULL`) | 3,590 |
| non-children | 22,144 |
| — of those, in the 8-family catalog | 21,914 |
| — of those, **not** in the catalog | 230 |
| children in catalog | 3,506 |
| children not in catalog (`A/HRC/DEC/…`, not a catalog family) | 84 |

The doc footnote reads *"21,906 of the catalog audit plus 3,590 split children plus
**non-catalog volume parents** and rescues"*. Two corrections: the catalog figure is
**21,914**, and **zero** volume parents are in `document_paragraphs` — all 89 are retired to
`status='split'` and hold no paragraphs. The 230 are bracket-parent symbols (`A/RES/31/106`,
`A/RES/2363(XXII)`, …) that the DL mirror has no record for; "volume parents" mislabels them.

**Where "has structured fulltext" is not true:**

- **712 documents have under 300 characters of total parsed text**; 14 have under 100.
- **96 pre-1976 `A/RES/…(Roman)` documents are OCR-garbled index fragments, not
  resolutions.** Their entire stored fulltext is a table-of-contents line, complete with
  leader dots and a page number:
  - `A/RES/1353(XIV)` → `1353 (XIV). Question of Tibet (21 October 1959) (item 73)................ 61`
  - `A/RES/357(IV)` → `357 (IV). Unforeseen and extraordi•` (35 chars, one paragraph)
  - `A/RES/1081(XI)` → `1081 (XI). United Nations Relief and Work& || TOTAL, PART I ToTAL, PART II`
  - `A/RES/2384(XXIII)` → `2384 (XXIII). Admission of the Republic of || Resoltttion Date of No. || Title Item adoption 11`
- **31 symbols carry byte-identical text to another symbol** (28 duplicate groups) — a crop
  failure that attributes one page-region to several adjacent resolutions. `A/RES/1123(XI)`,
  `1124(XI)`, `1125(XI)`, `1126(XI)` all hold the same 4,224-character blob; at most one
  attribution can be correct. Same for `A/RES/1127–1129(XI)`, `S/RES/335(1973)`/`336(1973)`,
  `A/RES/58/101`/`A/RES/58/101A-B`.
- A further 301 roman-numeral `A/RES` sit at 300–599 chars with visible two-column
  interleaving and boundary bleed — e.g. `A/RES/703(VII)` reads *"Methods which might be
  used to mainagencies and n | tain and strengthen international peace and within the
  limit"*, and `A/RES/1634(XVI)` carries a sentence belonging to its neighbour.

**Verdict: RESTATE.** The count is honest; the predicate is not. Say *"25,734 documents have
a parsed structure record"* and publish the shell count alongside it. Note separately that
3,590 of them (14 %) have no archived file at all and are therefore outside every
text-preservation gate (see Claim 2).

---

## Claim 2 — "100.0000 % text preservation"

**I ran `fulltext_verify_text.py` over the whole corpus.** Verbatim result:

```
Acceptance gate: 25734 extracted docs; parsed_dir=…/parsed_dev
checked=15141  pass=15141  FAIL=0  skip(no docx)=10593
ground-truth words=26995619  genuine tokens lost=0  aggregate preserved=100.0000%
EXIT=0
```

**The residual is a genuine zero — 0 of 26,995,619 words.** That part is not rounded and not
hedged, and it deserves credit.

**The denominator is not the corpus.** 10,593 of 25,734 documents (41.2 %) hit
`skip(no docx)` and are removed from numerator *and* denominator. They are every PDF-path
document (10,357) plus the 3,590 split children, which have `archive_path IS NULL` and so can
never be checked by a gate that opens an archived file.

Combined reach of both fidelity gates:

| Gate | Reaches |
|---|---:|
| `fulltext_verify_text.py` (docx path) | 15,141 |
| `fulltext_verify_pdf.py` (`format='pdf'`, needs `archive_path`) | 7,003 |
| **Total under some text-preservation gate** | **22,144 (86.0 %)** |
| **Covered by no fidelity gate at all** | **3,590 split children (14.0 %)** |

The 3,590 children are covered only by `fulltext_verify_volumes.py` — which Claim 3 shows
cannot fail.

**Direction.** The gate is one-directional: docx → parsed. It proves no source word was
lost. It does **not** test the reverse (invented or duplicated text), nor order, nor
structure. The 31 duplicate-content documents in Claim 1 would pass it.

**Negative control — I built the one the doctrine requires, and the gate passes it.** I copied
four parsed JSONs to scratch and damaged three (deleted 90 % of elements; blanked every
element text; dropped the last third), leaving one intact, then re-ran against the damaged
directory:

```
pass  A/RES/79/1        preserved=100.000%          <- undamaged stays quiet
FAIL  A/RES/80/1        preserved=25.956%  lost=368
FAIL  E/RES/2020/19     preserved=24.579%  lost=761
FAIL  S/RES/2806(2025)  preserved=90.033%  lost=573
checked=4 pass=1 FAIL=3   aggregate preserved=95.1034%   EXIT=1
```

The same four against the real directory: 4/4 pass, 100.0000 %. **A real content loss moves
the number and flips the exit code in both directions.** This is the only instrument in the
project I was unable to break.

**Staleness.** `parsed_dev/*.json` are dated 22 Jul 08:54 and the gate reads them, so the
figure is as fresh as the last parse run, not automatically recomputed on parser change.

**Also wrong in the docs:** `docs/fulltexts.md:254` — *"Aggregate preservation across the
763-doc corpus is 99.998 % (760/763 clean)."* 760/763 is **99.607 %**. A token-level
percentage and a document-level fraction are printed as one figure, making a 0.39 %
document failure rate read as a 0.002 % residual.

**Verdict: RESTATE.** *"100.0000 % of 26,995,619 content words across the 15,141 docx-path
documents; 10,593 documents (41.2 %), including all 3,590 volume-split children, are not
covered by this gate."*

---

## Claim 3 — "89/89 volumes pass the volume gate"

**Reproduces: 89/89 PASS, exit 0, coverage 0.977–1.000, 0 boundary leaks, 364 unmatched
headings reported.** And it means almost nothing. Three structural defects.

### A. The coverage metric does not measure what the docstring says

The docstring promises the gate *"checks that the union of the volume's children accounts
for"* the volume's decisions section. It does not. The ground truth comes from the file, but
the bag it is compared against is built from the **volume's own raw rows**:

```python
rows = read_volume_rows(conn, volume, lang)      # the VOLUME's rows, not the children's
accounted: Counter = Counter()
for r in rows:
    accounted.update(tokens(r["text"]))
...
missing = gt_types - set(accounted)
rep.coverage = 1.0 - (len(missing) / len(gt_types)) if gt_types else 1.0
```

A decision printed in the volume and never routed to any child still has all its tokens in
`accounted`. The code comment concedes it: the allowed-drop set is *"exactly the volume rows
that are NOT routed into a child."* So the metric compares pymupdf `get_text("text")` against
the `pdf-v1` extractor **on the same file** — PDF-extraction self-consistency, not split
recall. This is the denominator failure in mirror image: everything is accounted by
construction.

### B. `unmatched` is reported and never fails anything

```python
rep.ok = rep.coverage >= min_coverage and not rep.leaks
```

`rep.unmatched` — headings detected in the volume that could not be routed to a catalog
symbol, i.e. the decisions we knowingly dropped — is printed and discarded. **364 across the
89 volumes.**

### C. An empty ground truth reads as perfect coverage

`... if gt_types else 1.0`. If the ground-truth extraction yields nothing, coverage is 1.000.

### Proof that the gate cannot fail

```
Volume gate: 3 split volume(s)
  [PASS] A/HRC/10/29:   coverage=1.000 children=0 gt_tokens=6061 missing=0 unmatched=0 leaks=0
  [PASS] A/HRC/S-11/2:  coverage=1.000 children=0 gt_tokens=0    missing=0 unmatched=0 leaks=0
  [PASS] A/HRC/S-4/2:   coverage=1.000 children=0 gt_tokens=0    missing=0 unmatched=0 leaks=0
Done. 3/3 volumes passed.
```

Three volumes wrote **zero children**, were retired to `status='split'`, and pass with
coverage 1.000. `A/HRC/10/29` is the report of the tenth HRC session: it prints **51 adopted
texts** (resolutions 10/1–10/33, decisions 10/101–10/117, PRST 10/1 — I counted them in the
converted docx), the DL catalog has all 51, and the database holds **none** of them.

### The pre-edit number

`fulltext_verify_volumes.py` was created already carrying the accommodations named in
`72d5101`'s own message, so there is no earlier version in git to diff. I reconstructed it:
a scratch copy with the three named accommodations disabled (no `converted/` fallback for
`.doc`; no footnote-apparatus round-trip allowance; token identity required for cross-volume
children) plus three defects closed (empty ground truth ≠ pass; zero children ≠ pass;
`unmatched > 0` fails).

```
Done. 3/89 volumes passed.   EXIT=1
```

Attribution of the 86 failures:

| Cause | Volumes |
|---|---:|
| `unmatched` headings never routed (reported today, never failed) | 60 |
| empty ground truth — gate reports coverage 1.000 (20 are the `.doc` ground-truth read) | 21 |
| DB round-trip token loss (accommodations #2/#3) | 5 |

The only three that survive a gate capable of failing: `A/57/49(VOL.II)`, `E/2003/99`,
`E/2012/99`.

**Verdict: RETRACT.** "89/89" describes a gate that passes a volume which stored nothing.
The honest statement is *"3/89 volumes pass a gate that can fail; the published gate does not
measure split recall."*

---

## Claim 4 — "3,354 GA/ECOSOC decisions recovered" — the key denominator test

The stored count is real: 1,854 `A/DEC` + 1,500 `E/DEC` split children = 3,354. The question
is the denominator.

### My denominator, built from the source PDFs

I opened all 69 archived GA/ECOSOC volume PDFs with pymupdf and found decision headings with
my own regex, importing nothing from the pipeline. A printed **decision body** requires a
heading line `<sess>/<num>[L].` reached by an adoption record (`At its …`) **before** any
dot-leader line — which is what separates a real body from the checklist entry whose title
wraps onto a leader-dotted continuation line. I ran three variants to bound regex risk:

| Variant | Printed | Stored | Recall | Missing |
|---|---:|---:|---:|---:|
| loose (heading pattern, any occurrence) | 3,903 | 3,353 | 85.9 % | 550 |
| strict (adoption record within 12 lines) | 3,786 | 3,295 | 87.0 % | 491 |
| **v3 (ordered: adoption before dot-leader)** | **3,847** | **3,348** | **87.0 %** | **499** |
| v3, letter-agnostic (most generous possible) | 3,841 | 3,345 | 87.1 % | 496 |

All four converge on **87 %**. I hand-checked a random sample of 14 misses against the PDFs:
**14/14 are genuine printed decision bodies with adoption records.**

### A second, fully independent denominator

The DL catalog itself, for the in-scope range (`A/DEC` sessions 57–80, `E/DEC` 2003–2025):

**4,303 catalog records → 3,354 stored → 78.0 %, 949 missing.**

The two denominators differ because the catalog splits multi-part decisions into lettered
records (`A/DEC/61/405A`, `405B`) that the volume prints under a single `61/405.` heading.
**The true recall is between 78 % and 87 %; between 499 and 949 decisions are printed in
volumes we already hold and have no stored text.**

### Why — two causes, and the second is invisible

Of the 499 v3 misses:

- **250 were flagged `unmatched` by the splitter** — detected, unroutable against the
  catalog, dropped. Reported in logs; fails nothing (Claim 3B).
- **249 were never detected as headings at all.** Silent. No log line, no counter, no gate.

**Root cause of the silent 249, demonstrated.** `split_volume` body-confirms a heading by
searching the *following* rows only:

```python
window = " ".join(rows[j]["text"] for j in range(i + 1, min(i + 1 + _BODY_CONFIRM_WINDOW, len(rows))))
if not _ADOPTION_RE.search(window):
    continue
```

It never inspects row `i` itself. The PDF extractor routinely merges a heading and its
adoption line into one paragraph — so the confirmation looks past the very text that would
confirm it. Ground truth, straight from `document_paragraphs_raw` for `A/60/49(VOL.II)`:

```
position 146 | 60/404. Election of eighteen members of the Economic and Social Council
               At its 34th plenary meeting, on 17 October 2005, the General Assembly, …
position 273 | 60/519. International instrument to enable States to identify and trace …
               At its 61st plenary meeting, on 8 December 2005, the General Assembly, by a
               recorded vote of 151 to none, with 25 abstentions, …
```

The text is in the database, under the volume. `A/DEC/60/404` and `A/DEC/60/519` have **zero
paragraphs** and both are in the DL catalog.

**The "catalog-absent, not our fault" excuse covers 19 cases, not 496.** Of the 496
letter-agnostic uncovered decisions, **477 have a DL catalog record**.

### Named decisions printed in a volume and absent from the DB

`A/DEC/58/419`, `A/DEC/59/422`, `A/DEC/60/404`, `A/DEC/60/406`, `A/DEC/60/415`,
`A/DEC/60/419`, `A/DEC/60/513`, `A/DEC/60/519`, `A/DEC/60/537`, `A/DEC/60/549`,
`A/DEC/61/405`, `A/DEC/61/406`, `A/DEC/61/409`, `A/DEC/61/411`, `A/DEC/61/419`,
`A/DEC/61/502`, `A/DEC/61/503`, `A/DEC/61/511`, `A/DEC/61/532`, `A/DEC/61/544`,
`A/DEC/62/404`, `A/DEC/68/504`, `A/DEC/68/663`, `A/DEC/79/407`, `E/DEC/2009/201`,
`E/DEC/2020/201`, `E/DEC/2024/205`. (Full list: 499 records, reproducible with the script
described below.)

### Worst volumes

| Volume | Printed | Stored | Recall |
|---|---:|---:|---:|
| `A/68/49(VOL.III)` | 33 | 15 | 45.5 % |
| `A/74/49(VOL.III)` | 37 | 18 | 48.6 % |
| `A/66/49(VOL.III)` | 34 | 21 | 61.8 % |
| `A/69/49(VOL.III)` | 27 | 17 | 63.0 % |
| `A/70/49(VOL.III)` | 27 | 17 | 63.0 % |
| `E/2012/99` | 63 | 41 | 65.1 % |
| `E/2013/99` | 65 | 43 | 66.2 % |
| `A/67/49(VOL.III)` | 30 | 20 | 66.7 % |
| `A/71/49(VOL.III)` | 34 | 23 | 67.6 % |
| `A/64/49(VOL.II)` | 65 | 46 | 70.8 % |

Against the catalog denominator, the worst strata are `E/DEC` **2020 (12.5 %)**, `A/DEC`
session **74 (51.3 %)**, session **80 (53.9 %)**, session **68 (59.6 %)**, `E/DEC` **2025
(61.8 %)**, **2013 (61.4 %)**, **2012 (62.1 %)**.

**Verdict: RESTATE.** *"3,354 GA/ECOSOC decisions stored, out of 3,847 printed in the source
volumes (87 %) / 4,303 in the DL catalog for the same range (78 %). 499–949 decisions remain
unrecovered from volumes already on disk."*

---

## Claim 5 — "262 early-HRC documents"

**Reproduces:** 262 distinct `A/HRC/(RES|PRST|DEC)/` symbols for sessions 1–11 plus all
special sessions. 236 came from the volume split, 26 from the ordinary fetch path.

**Denominator (DL catalog, identical scope): 320. Recall 81.9 %, 58 missing.**
For the declared backfill scope (sessions 2–11 + S-2…S-11): catalog 291, stored 236 =
**81.1 %**, 55 missing.

**51 of the 58 are one session.** Session 10: catalog 51, stored **0**. `A/HRC/10/29` prints
all 51 (I counted them in the converted docx: resolutions 10/1–10/33, decisions
10/101–10/117, PRST 10/1) and was retired to `status='split'` having written no children.

Two further holes:

- `A/HRC/S-11/2` prints `S-11/1` ("Assistance to Sri Lanka in the promotion and protection of
  human rights") — 0 stored.
- `A/HRC/S-4/2` — 0 stored, and **the archived file is not a session report**. It is a
  40-paragraph letter from Antonio Cassese, former Chairperson of the International
  Commission of Inquiry on Darfur, to the President of the Council. The volume map points at
  the wrong document for the fourth special session.

Session 1 and S-1 are outside `HRC_REPORTS` (which covers sessions 2–11) and
`HRC_SPECIAL_SESSIONS` (S-2…S-11) entirely; S-1 is in the catalog and unstored.

**Verdict: RESTATE.** *"262 of 320 early-HRC documents (81.9 %); the tenth session is
entirely absent (0 of 51)."*

---

## Claim 6 — the coverage bucket table

### The denominator

41,802 reproduces exactly — but **only** with no date filter and no language filter. The
pipeline's own catalog (`targets_from_catalog`: `date_publication >= 1994-01-01`, English)
is **24,080**. The published table therefore mixes a whole-history denominator with a
1994-onward pipeline; that is defensible (it counts pre-1994 as uncovered) but should be
stated, because the two numbers differ by 17,722.

### The buckets do not partition

My strict priority partition over the same 41,802, source-derived:

| Bucket | n |
|---|---:|
| 1 parsed (own fulltext) | 25,420 |
| 2 bracket part resolved via parent | 2,723 |
| 3 deferred: GA/ECOSOC decision in a pre-2003 scanned volume | 5,089 |
| 4 scan, no text layer | 4,889 |
| 5 ODS returned nothing ("confirmed absent") | 1,790 |
| 6 **never attempted — no ledger row, never probed** | **1,891** |
| **Total** | **41,802 ✓** |

Against the published table (25,734 / 2,721 / ~5,200 / ~4,900 / ~1,500 ≈ 39,555):
**~2,247 catalog symbols sit in no bucket.**

Two specific errors:

1. **A whole bucket is missing.** 1,891 catalog symbols have no ledger row and were never
   probed: 796 `A/RES`, 707 `A/DEC`, 350 `E/DEC`, 34 `A/HRC/RES`, 3 `E/RES`, 1 `A/HRC/PRST`.
   They are not "absent", not "scans", not "deferred" — nothing was ever tried. They are
   absent from the published table too, which is how they stayed invisible.
2. **"parsed 25,734" is a category error in this table.** It is a whole-corpus count
   (including 314 documents that are not in the catalog at all) placed in a table whose
   denominator is the catalog. The catalog-consistent figure is **25,420**.

### Overlap

The four ledger-status buckets are genuinely disjoint — I tested all four pairs and got 0
overlaps (no bracket symbol has own fulltext; no `no_text_layer` or `unavailable` symbol has
paragraphs; no pre-2003 decision has own fulltext). **Credit where due.**

But the two *deferred/absent* rows overlap: **2,989 of the 5,097 pre-2003 decisions also
carry `status='unavailable'`**. Total catalog `unavailable` is 4,773; net of pre-2003
decisions it is **1,790**, against the published ~1,500 — an understatement of ~290 (19 %).

### Is "confirmed absent" really confirmed? — yes, for the sample

I probed ODS politely (curl/8.7.1 UA, `%20`/`%28` encoding, 2 s pacing, following the
pseudo-redirect body with a second GET). 10 symbols in their recorded format, plus 5 in the
alternate format. **All 15 returned `Found. Redirecting to https://documents.un.org/error`.**

**Positive control** (essential — my first probe script reported the controls as
"NO-REDIRECT" because I matched only absolute URLs, and the control caught my own bug):

```
A/RES/80/1     -> Found. Redirecting to /doc/UNDOC/GEN/N25/246/52/PDF/N2524652.PDF
A/DEC/47/320   -> Found. Redirecting to https://documents.un.org/error
```

Probed and confirmed absent: `A/DEC/47/320`, `A/DEC/50/463`, `E/DEC/1999/273`,
`E/DEC/2002/273`, `E/DEC/165(LXI)`, `A/RES/888(IX)A-C`, `A/DEC/51/438`, `E/DEC/1983/108`,
`A/DEC/41/464`, `A/DEC/49/445` — plus alternate-format retries for five of them.

Caveat: English only, and at most two of the three ODS format parameters. "Confirmed absent"
should read *"absent from ODS in English in the formats probed."*

**Verdict: RESTATE** the table so it partitions, and add the never-attempted row.

---

## Claim 7 — scans and OCR (delegated to a second reviewer; findings verified against the same DB)

**7a — "~41 % of pre-1994 PDFs are pure scans": RESTATE.** 41.35 % of the **11,577 PDFs
that were fetched and triaged**. The in-scope pre-1994 universe is 14,567, of which **2,990
(20.5 %) were never fetched** and are absent from numerator and denominator alike — so the
source-derived figure is 32.9 % confirmed, bounded [32.9 %, 53.4 %]. Against the full
pre-1994 catalog (17,715) it is 27.0 % confirmed, bounded [27.0 %, 61.7 %]. Separately,
`docs/fulltexts.md:436` still publishes a **stale 46 %** from the 64-document sample, never
replaced by the 12,012-document measurement that now exists (41.7 % none / 55.5 % text /
2.8 % poor).

**7b — "OCR yield 80–95 % by era": RETRACT.** `ocr-experiments.md` §8 heads the table
*"Measured CER by era"*. There are **5 ground-truth documents**, all `S/RES` from 1994–96,
all **born-digital** (the document itself notes "0 embedded images"), from none of the eras
in the yield table; Surya was benchmarked on **2**; the "scan" tier is those same 5 documents
synthetically degraded; the real-scan tier is **3 pages with no ground truth**. **Zero
documents have been OCR'd and run through `fulltext_parse.py`**, and yield is a parser
outcome. §11 says so honestly — the defect is §8's word "Measured" and the headline
repeating it. *To make it knowable:* yield is one bit per document, so no character ground
truth is needed — ~200 documents per era stratum × 4 strata ≈ **800 documents**, OCR'd,
parsed unchanged, human-adjudicated on "did the opening formula, preamble and operative
sequence come out correct?" That converts four asserted ranges into four measured numbers
with ±5 pp at 95 % CI.

**7c — threshold sound at the boundary: stands, and is stronger than claimed.** The rule is

```python
if chars_per_page < 80 or n_tok < 20:
    klass = "none"
```

The requested boundary sample cannot be drawn because **there is no boundary population**:
across all 12,012 archived PDFs the maximum chars/page among `none` is **1.0** and the
minimum among `text`/`poor` is **354.0**. Any threshold in [2, 354) gives the identical
partition. A *total* check (not a sample) of all 5,009 `no_text_layer` PDFs found
**5,008 carry a full-page image on every page**. Observed misclassification: **1 in 12,012
(0.008 %)**. The one failure, `S/RES/2577(2021)`, is not a threshold error — the archived
file is the wrong document entirely (Russian-language `E/2021/L.22`), binned `none` because
`_tokens` is `[A-Za-z]{2,}` and Cyrillic scores 8 tokens. **`n_tok < 20` is an undocumented
Latin-only language filter** that stores a wrong-document fetch under a status meaning "pure
image scan", making two different failures indistinguishable in the ledger.

**Bonus correction to the OCR plan:** the page backlog is asserted as "25–40k pages,
projections use 30k". Measured: the `no_text_layer` backlog is **9,437 pages across 5,009
documents** — 3.2× smaller. The $1–4 cost conclusion survives; the number should not have
been a ballpark when it was a one-query answer.

---

## What must be retracted or restated

**Retract outright**

1. **"89/89 volumes pass the volume gate."** The gate passes volumes that stored nothing.
   3/89 under a gate that can fail.
2. **"OCR yield 80–95 % by era."** No document has been OCR'd and parsed. This is a
   projection presented as a measurement.

**Restate with the honest denominator**

3. "100.0000 % text preservation" → *of 15,141 of 25,734 documents (58.8 %); 3,590
   split children are covered by no fidelity gate at all.*
4. "3,354 GA/ECOSOC decisions recovered" → *3,354 stored; 87 % of what the volumes print,
   78 % of the catalog; 499–949 unrecovered.*
5. "262 early-HRC documents" → *262 of 320 (81.9 %); session 10 entirely absent (0 of 51).*
6. The coverage bucket table → make it partition; add the **1,891 never-probed** row; use
   25,420 (catalog-consistent) not 25,734; stop double-counting 2,989 pre-2003 decisions
   between the deferred and absent rows.
7. "~41 % of pre-1994 PDFs are pure scans" → *41 % of the 11,577 triaged; 2,990 never
   fetched.* Replace the stale 46 % in `docs/fulltexts.md:436`.
8. `docs/fulltexts.md:254` "99.998 % (760/763 clean)" → 760/763 is **99.607 %**.

**Not in dispute** — these survived adversarial testing: the `fulltext_verify_text.py` gate
itself (a real check with a working negative control in both directions); the disjointness of
the four ledger-status buckets; the "confirmed absent" classification for the 15 probed
symbols; the scan/text chars-per-page threshold.

---

## What would make the unknowable knowable

| Question | What it needs |
|---|---|
| True decision recall (78 % vs 87 %) | Resolve multi-part decisions: decide whether `A/DEC/61/405 A` and `B` are one document or two, and count the source the same way the catalog does. One rule, applied to both sides. |
| Whether the 249 silent misses are the whole silent set | The current evidence is my regex vs theirs. A conservation check would settle it: every heading-shaped line in a volume must end up in exactly one of {child written, cross-check, unmatched-and-logged, explicitly-classified-noise}, with the residue printed. Absence is only visible to a check that enumerates the source. |
| Whether the volume gate works | A negative control: delete one child from the DB and assert the gate fails. Today it passes a volume with zero children, so the control is guaranteed to fail — which is the point of running it. |
| OCR yield | ~800 documents (200 × 4 era strata), OCR'd, parsed unchanged, one human bit each. |
| Whether "parsed" means "usable" | A minimum-plausibility assertion per family (a resolution has a title, an opening formula and ≥1 operative). It would currently fail on ≥96 documents — that is the number worth publishing. |

---

## Reproducing this

Scratch scripts (read-only; import nothing from `python/` except where noted) are at
`/Users/david/.claude/jobs/3f4ded06/tmp/adv4/`:

| Script | Purpose |
|---|---|
| `indep_decisions.py`, `indep2.py`, `indep3.py` | three independent decision censuses from the archived volume PDFs |
| `recall.py` … `recall4.py` | recall against stored symbols, four denominator definitions |
| `indep_hrc.py` | HRC session-report census from the converted docx |
| `whydrop.py` | runs the pipeline's own `split_volume` read-only to extract `unmatched` |
| `volgate_orig.py` | the de-accommodated volume gate (3/89) |
| `probe.sh`, `probe2.sh` | polite ODS probes with positive controls |
| `verify_text_full.log`, `negctl.log`, `volgate_current.log`, `volgate_strict.log` | run outputs |

Nothing in the pipeline, the database, or the SSD archive was modified.
