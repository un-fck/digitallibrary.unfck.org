# Adversarial content audit — `digitallibrary.document_paragraphs` vs. source documents

**FAIL — 29 documents opened against re-downloaded/archived sources: the 2015+ `.docx`, 2000s
`.doc` and WordPerfect paths are clean (fabrication 0.52% / 0.20% / 0 of paragraphs checked),
but the pre-1994 PDF path fabricates text by interleaving printed columns and merging
neighbouring resolutions (486 of 6,986 paragraphs, n=400 docs, ≈80% genuine on manual review
→ est. ~6,800 corrupted paragraphs corpus-wide), 554 of 3,902 printed GA/ECOSOC decisions
(14.2%) never reached the database at all, and 3,116 of the 25,734 "documents with text"
(12.1%) render *zero words* on mandates.un.org.**

Date: 2026-07-27. Auditor did not build this pipeline. Read-only throughout; nothing fixed.

---

## 0. Method, and what it can and cannot see

Sources were obtained **independently of the pipeline**:

- 18 documents re-downloaded from ODS (`https://documents.un.org/api/symbol/access?s=…`,
  UA `curl/8.7.1`, pseudo-redirect followed with a second GET), 21 requests total.
- Volume PDFs and bulk-scan sources read from the read-only archive
  `/Volumes/SSDAStorage/digitallibrary-fulltexts/original/`.

Text was extracted with **different tools than the pipeline uses**:

| DB path | pipeline extractor | this audit's extractor |
|---|---|---|
| `.docx` | (pipeline's own) | `python-docx` + raw `word/footnotes\|endnotes\|header*.xml` |
| `.doc` | LibreOffice 25.2 | macOS `textutil` |
| `.wpd` | LibreOffice 25.2 | printable-ASCII runs from raw WP5.1 bytes |
| `.pdf` | (pipeline's own) | PyMuPDF 1.28 |

The core instrument is a presence test: every DB paragraph of ≥10 words is cut into 8-grams
and each is looked up in the normalised source. A paragraph whose 8-grams are <25% findable is
flagged **absent from source**. Normalisation folds quotes/dashes/soft hyphens and rejoins
hyphen-linebreaks on the source side.

**Negative control (undamaged input must stay quiet).** Run against the `.docx` path the same
detector reports 28 flags in 5,431 paragraphs (**0.52%**) and against `.doc` 7 in 3,523
(**0.20%**). The four hand-read `.docx` documents (A/RES/70/1, 71/1, 73/195, S/RES/2231(2015))
produced **zero** flags and **zero** missing source lines. So a 6.96% flag rate on the PDF path
is ~13× the instrument's own noise floor, not the instrument.

**Positive control.** A/RES/1514(XV) was flagged by the detector and then proved corrupt by
reading the printed page (§2.1). A/RES/2469(XXIII) likewise. The detector has been shown to
fail on damaged input and to stay silent on clean input.

**What this audit cannot see.** (a) `.wpd` at scale — my ASCII-run extractor recovers nothing
from ~21 of 120 compressed WP files, which produces a spurious 14.79% rate for that path; the
four hand-checked WPD documents were clean and I make no scale claim for that path. (b) The
5,009 `no_text_layer` PDFs, which hold no paragraphs and are honestly recorded as such —
correct behaviour, see §6. (c) `public.bracket_document_aliases` is not readable by the
`digitallibrary_rw` role, so bracket resolution was tested through its underlying mechanism
(subres headings + `text_index`) rather than through the view.

---

## 1. Per-document verdicts

`src` = words my extractor recovered from the source file. For pre-1994 PDFs the source file
is an *Official Records page* carrying several resolutions, so `db/src` there is **not** a
truncation measure and is omitted. `db` = all stored words; `rendered` = words that survive
`ParagraphsSection`'s `HIDDEN_TYPES` gate and therefore reach a reader.

### (a) Native `.docx`, 2015+ — PASS

| symbol | src w | db w | ratio | rendered w | defects | verdict |
|---|---|---|---|---|---|---|
| A/RES/70/1 | 15,945 | 15,413 | 0.967 | 14,604 | none | PASS |
| A/RES/73/195 | 17,307 | 16,933 | 0.978 | 16,088 | none | PASS |
| A/RES/71/1 | 11,484 | 11,236 | 0.978 | 10,988 | none | PASS |
| S/RES/2231(2015) | 38,305 | 37,966 | 0.991 | 32,262 | 5,704 w of annex tables/footnotes stored but never rendered | PASS (storage) |

No fabricated paragraph, no source line missing from the DB. The residual 1–3% is masthead
chrome my extractor emits and the parser drops.

### (b) Binary `.doc`, 2000s — PASS

| symbol | src w | db w | ratio | rendered w | defects | verdict |
|---|---|---|---|---|---|---|
| A/RES/60/1 | 17,491 | 17,125 | 0.979 | 16,251 | none | PASS |
| A/RES/58/4 (UNCAC, 518-para annex) | 19,628 | 19,158 | 0.976 | 18,908 | none | PASS |
| S/RES/1929(2010) | 7,537 | 7,336 | 0.973 | 7,311 | none | PASS |
| A/RES/56/81 | 9,423 | 9,022 | 0.957 | 8,869 | none | PASS |
| A/RES/56/25 (omnibus A–F) | — | 4,445 | — | 4,171 | none; block boundaries exact (§5) | PASS |

### (c) WordPerfect, 1994–1999 — PASS (4 documents; path not verifiable at scale here)

| symbol | src w | db w | rendered w | defects | verdict |
|---|---|---|---|---|---|
| A/RES/51/210 | 2,920 | 2,547 | 2,370 | none | PASS |
| A/RES/49/60 | 2,438 | 2,070 | 1,895 | none | PASS |
| A/RES/53/243 | 3,297 | 3,046 | 2,849 | none | PASS |
| A/RES/48/104 | 2,475 | 2,065 | 1,844 | none | PASS |

Every source string absent from the DB was WordPerfect internal boilerplate
(`'Format standard with headers and footers definition'`, `'I. A. 1. a.(1)(a) i) a)'`,
`'@15-12-93 09:10p'`). No document text lost.

### (d) Pre-1994 PDF — FAIL on all six

| symbol | db w | rendered w | defects | verdict |
|---|---|---|---|---|
| A/RES/1514(XV) | 612 | 171 | column interleaving → fabricated sentences (§2.1) | **FAIL** |
| S/RES/242(1967) | 527 | 520 | French text stored in a `lang='en'` record; column interleaving (§2.4) | **FAIL** |
| A/RES/2469(XXIII) | 90 | 45 | text of a *different resolution* (2470 (XXIII)) merged in (§2.2) | **FAIL** |
| A/RES/2898(XXVI) | 276 | 206 | interleaving + budget-appropriations table from another resolution (§2.3) | **FAIL** |
| A/RES/32/39 | 1,033 | **0** | whole body typed `frontmatter` → renders nothing (§3) | **FAIL** |
| A/RES/3314(XXIX) | 1,298 | 234 | Definition of Aggression annex reaches 18% of stored / 8% of source words | **FAIL** |
| A/RES/44/25 (CRC) | 0 | 0 | image-only scan, `status='no_text_layer'`, no rows — honest gap | PASS |

### (e) Volume-split children (A/DEC/*, E/DEC/*) — mixed

Text attribution is **correct**; coverage and rendering are not.

| check | result |
|---|---|
| 24 children across 8 volumes: first sentence inside the printed decision's own block | 24/24 |
| … last sentence inside the same block | 24/24 |
| … paragraphs belonging to a neighbouring decision | 0/24 documents |
| printed decisions absent from the DB | **554 of 3,902 (14.2%)** — §4 |
| children that render zero words on the site | **2,865 of 3,354 (85.4%)** — §3 |

Sampled children included one-line decisions (A/DEC/74/505, 33 source words), lettered
suffixes (E/DEC/2003/201B, E/DEC/2003/215B) and 128-paragraph ones (E/DEC/2023/325). Two
boundary bleeds found: A/DEC/80/520 ends with the *next* section's heading
`'Decisions adopted on the reports of the Third Committee'`, and its footnote block carries
footnotes 57–59 (`A/80/537`, `A/C.4/80/L.15`, `A/80/PV.64`) that belong to Fourth-Committee
decisions, not to this one.

### (f) Early-HRC split children — PASS on content, FAIL on boundaries

| symbol | rows | rendered w | defects | verdict |
|---|---|---|---|---|
| A/HRC/RES/6/1 | 18 | 504 | none; 0/17 paragraphs absent from source | PASS |
| A/HRC/RES/9/8 | 54 | 1,635 | none; 0/45 absent | PASS |
| A/HRC/DEC/6/104 | 7 | — | none | PASS |
| A/HRC/DEC/9/101 | 7 | — | none | PASS |
| A/HRC/PRST/8/2 | 1,631 | **108,175** | absorbs the whole remainder of A/HRC/8/52 | **FAIL** (§2.5) |
| A/HRC/PRST/6/2 | 685 | 20,974 | same over-capture | **FAIL** |

Child *counts* per session match the source: A/HRC/5/21 prints 4 texts → 4 children;
A/HRC/2/9 prints 21 → 21; A/HRC/6/22 prints 43 numbered + PRSTs → 45. No decisions dropped on
this path.

### (g) Bracket-resolved parts — PASS on the case tested

A/RES/56/25 segments into `text_index` 1–6 matching the printed blocks A–F exactly (block 2
starts at the source's `B` / *Convention on the Prohibition of the Use of Nuclear Weapons*,
etc.). But **85 of 741 omnibus parents (11.5%)** have a subres-heading count ≠ `max(text_index)`,
i.e. only some lettered parts are segmented. For those, `getResolvedParagraphs` step 4 finds no
matching block and silently falls back to the whole parent — the reader asking for part `[B]`
is shown parts A+B+C with `resolved_block=false` and no visible distinction in the paragraph
list itself.

---

## 2. Defect class 1 — FABRICATION (the worst class, and it is real)

The pre-1994 PDF extractor reads two-column Official Records pages **line by line across the
page**, weaving the left and right columns into single paragraphs. The result is grammatical
enough to read and is not in any document.

### 2.1 A/RES/1514(XV) — Declaration on the Granting of Independence to Colonial Countries

Stored in the DB, position 13:

> "Convinced that all peoples have an inalienable right **any distinction as to race, creed or
> colour, in order to** to complete freedom, the exercise of their sovereignty **enable them to
> enjoy complete independence and** and the …"

The printed page (ODS `NR015288.PDF`, p. 2) has, in the **left** column:

> "Convinced that all peoples have an inalienable right / to complete freedom, the exercise of
> their sovereignty / and the integrity of their national territory,"

and, in the **right** column, forty lines lower:

> "any distinction as to race, creed or colour, in order to / enable them to enjoy complete
> independence and / freedom."

The string `an inalienable right any distinction as to race` occurs nowhere in the source.
Position 15 is worse — it invents words at the column seam:

> "Any attempt aimed at the partial or total **dis**speedy and unconditional end colonialism in
> all its forms ruption of the national unity and the territorial **in**and manifestations ;"

`disspeedy` and `inand` are not words; the hyphenated stems `dis-|ruption` and `in-|tegrity`
were glued to the other column's line.

### 2.2 A/RES/2469(XXIII) — two different resolutions merged into one record

DB position 0 (the title):

> "2469 (XXIII). Appolntments to fill vaeancies in **2470 {XXIII). Appointment to fill a vacancy
> in** the membership of the Advi1ory Committee **the membership of the Board of Auditora** on
> Administrat…"

DB position 2:

> "The General Assembly **Appoints the Auditor-General of Colombia as a** l. Appoints the
> following persons as members of **member of the Board of Auditors for a three-year** the
> Advisory Committee on …"

The Board-of-Auditors appointment is **resolution 2470 (XXIII)**. It is served to the reader as
part of 2469 (XXIII). This is defect class 6 (wrong document) inside class 1.

### 2.3 A/RES/2898(XXVI) — a budget table from an unrelated resolution

DB position 8, typed `footnote`:

> "BUDGET APPROPRIATIONS FOR THE FINANCIAL YEAR 1972 TOTAL, PART l TOTAL, PART JI TOTAL, PART III"

A/RES/2898(XXVI) is *Restructuring of the Department of Economic and Social Affairs*. It
contains no budget appropriations table. Positions 3, 4, 6 and 7 are column-interleaved with
the adjacent information-policy resolution ("Takes note also of the report of the Joint Inspec**in
the implementation of information policies and** tion Unit on…").

### 2.4 S/RES/242(1967) — French text served as English

DB positions 21–23, in a row whose `lang` is `'en'`:

> "A ffirme que l'accomplissement des principes de rable au Moyen-Orient qui devrait comprendre
> !'application des deux principes suivants :"
> "i) Retrait des forces armees israeliennes des territoires occupes !ors du recent conflit;"

The source page is the trilingual Official Records sheet; the parser took the French column
into the English record, interleaved. Same pattern in A/RES/120(II) and S/RES/227(1966).

### 2.5 A/HRC/PRST/8/2 — a one-paragraph President's statement stored as 108,207 words

Positions 0–1 are the real statement ("At the 27th meeting, on 18 June 2008, the President of
the Council made a statement reading as follows:"). Position 900 is:

> "Amnesty International welcomed the recommendations on the rights of asylum-seekers and
> migrants. It noted that…"

— an NGO intervention from the Universal Periodic Review section of A/HRC/8/52. Position 1630 is
a heading, *"Administrative and programme budget implications of Council resolutions adopted at
the eighth session"*. The final child of the volume absorbs everything after it. Nine other
children exceed 10,000 words, all on the A/HRC path (A/HRC/DEC/11/116 = 68,546 w;
A/HRC/DEC/2/116 = 18,876 w; A/HRC/DEC/4/105 = 13,836 w; A/HRC/PRST/9/2 = 11,598 w …).

### Prevalence

| population | n sampled | metric | value |
|---|---|---|---|
| standalone PDF path (7,003 docs, 121,264 paragraphs ≥10 w) | 400 docs / 6,986 paras | paragraphs absent from source | **486 = 6.96%** |
| " | 400 docs | documents with ≥1 absent paragraph | **187 = 46.8%** |
| " | 400 docs | documents with ≥2 | 95 = 23.8% |
| `.docx` path (control) | 120 docs / 5,431 paras | absent | 28 = 0.52% |
| `.doc` path (control) | 120 docs / 3,523 paras | absent | 7 = 0.20% |

A blind sample of 30 flagged PDF paragraphs was read by hand: **24 were genuine column
interleaving / cross-resolution contamination / wrong-language**, 6 were detector artefacts
(merged footnote blocks, one soft-hyphen false positive). Applying 0.80:

> **Estimated ~5.6% of pre-1994 PDF-path paragraphs (≈6,800 of 121,264) contain text that is not
> in the document they are attributed to, spread over an estimated ~2,600 of 7,003 documents
> (37%).** n = 400 documents, one seed, binomial 95% CI on the document rate ≈ ±4.9 points before
> the 0.80 adjustment. Confidence: high on the class, medium on the point estimate.

---

## 3. Defect class 2 — EMPTY SHELLS: 3,116 documents render nothing

`fetchDlArm` returns rows; `ParagraphsSection.isContentElement` then discards
`frontmatter, backmatter, footnote, footer, signature, vote_record, divider, table` and any
`title` outside an annex. Applying that gate to the stored corpus:

| measure | value |
|---|---|
| symbols with paragraphs (the headline "documents with text") | 25,734 |
| stored words | 31,855,406 |
| words that reach a reader | 28,932,220 (**90.8%**) |
| **symbols rendering 0 words** | **3,116 (12.1%)** |
| symbols rendering <30 words | 3,530 (13.7%) |
| words stored inside zero-render documents | 521,169 |

By provenance:

| path | docs | render nothing | % |
|---|---|---|---|
| pdf, volume-split child | 3,354 | **2,865** | **85.4** |
| docx, split child | 236 | 18 | 7.6 |
| pdf, standalone | 7,003 | 202 | 2.9 |
| wpd | 2,557 | 14 | 0.5 |
| docx | 5,613 | 11 | 0.2 |
| doc | 6,971 | 6 | 0.1 |

Cause: the split and pre-1994 parsers type the decision body as `type='frontmatter'` with
`subtype IS NULL`. Examples, each of which is the *entire* document:

- `A/DEC/63/518` — 2 rows: a title, and one `frontmatter` paragraph beginning *"At its 61st
  plenary meeting, on 2 December 2008, the General Assembly, on the recommendation of the First
  Committee,41 decided to include in the provisional agenda…"*. Rendered: nothing.
- `E/DEC/2017/224`, `A/DEC/78/404`, `E/DEC/2003/201B` (48 paragraphs, all `frontmatter`),
  `A/RES/32/39` (67 of 68 rows) — same shape. Rendered: nothing.

**1,594 of the 3,116 are >80% `frontmatter` by word count.** These are not empty records; they
are records the product cannot show. The claim "25,734 documents with text" is true of the
database and false of the website for 12.1% of them.

### 3a. A latent SQL three-valued-logic bug in the same query

`website/src/lib/data/paragraphs.ts:168`:

```sql
AND NOT (dp.type = 'frontmatter' AND dp.subtype = 'masthead')
```

With `subtype IS NULL` this evaluates `TRUE AND NULL → NULL`, `NOT NULL → NULL`, and the row is
**dropped**. It therefore removes all **78,156** NULL-subtype `frontmatter` rows (1,026,994
words) rather than the 20,283 mastheads it names. Replaying the exact predicate against
A/RES/32/39 returns 4 rows of 68. Today this is masked because the render layer hides
`frontmatter` wholesale, but the moment `frontmatter` is un-hidden (the obvious fix for §3) the
SQL will still silently drop 21,879 documents' worth of it. Correct form:
`AND NOT (dp.type = 'frontmatter' AND dp.subtype IS NOT DISTINCT FROM 'masthead')`.

---

## 4. Defect class 3 — SILENT LOSS: 554 printed decisions never harvested

Denominator taken **from the source**, not from what processing produced: every decision number
printed at line-start in the 89 archived volume PDFs, deduplicated across Vol. II / Vol. III,
restricted to the decision ranges (GA ≥400, ECOSOC ≥200).

| | |
|---|---|
| decisions printed in the volumes | **3,902** |
| A/DEC + E/DEC symbols in `document_paragraphs` | 3,354 |
| **printed but absent** | **554 (14.2%)** |

Worst sessions: A/DEC/74 (47 of 107 missing), A/DEC/68 (29 of 91), E/DEC/2020 (**29 of 34** —
only 5 of the COVID-era Council's decisions exist), A/DEC/64 (22 of 99), E/DEC/2012 (22 of 63),
E/DEC/2013 (22 of 65).

Negative control on the denominator — every claimed-missing decision was re-read in the PDF and
has printed body text, not merely a table-of-contents line:

- `A/DEC/78/409` — *"Election of members of the Committee for Programme and Coordination. At its
  37th plenary meeting, on 20 November 2023, the General Assembly, on the basis of nominations by
  the Economic and Social Council…"*
- `A/DEC/74/402` — *"Appointment of members of the Advisory Committee on Administrative and
  Budgetary Questions. At its 14th plenary meeting, on 10 October 2019… appointed Ms. Donna-Marie
  C…"*
- `E/DEC/2020/205` — *"Procedure for taking decisions of the Economic and Social Council during
  the coronavirus disease (COVID-19) pandemic. On 3 April 2020, the Economic and Social Council,
  noting with concern…"*

(An earlier pass counted 10 missing in A/78/49(Vol. II) using a regex that also matched
table-of-contents lines; re-checking each hit against the body confirmed all 10, and the same
body-vs-TOC discrimination is applied to the 554 figure.)

---

## 5. What is *not* broken

Recorded because a finding of "clean" is only useful if it names what was attacked.

- **Misattribution in volume splits: not found.** 24 children, 8 volumes, first *and* last
  sentence inside the printed decision's own block, 0 foreign paragraphs. Includes one-line
  decisions, lettered suffixes and 128-paragraph decisions.
- **Order corruption on the `.doc`/`.docx` paths: not found.** (The order metric is noisy —
  repeated boilerplate makes shingle offsets ambiguous — so this is a weak negative, not a
  proof.)
- **Truncation on `.docx`/`.doc`: not found.** Ratios 0.957–0.991, and the residue is masthead
  chrome, verified line by line for four documents.
- **Annexes on the modern paths: complete.** S/RES/2231(2015) stores all 1,759 annex paragraphs;
  A/RES/58/4 all 518.
- **Gaps are recorded as gaps.** A/RES/44/25 (image-only scan) is `status='no_text_layer'` with
  zero paragraph rows — not stored as an empty document. That is the right behaviour and it
  distinguishes 5,009 honest gaps from a fabrication.

---

## 6. Ranked defect classes with prevalence

| # | class | evidence | est. corpus prevalence | confidence |
|---|---|---|---|---|
| 1 | **Fabrication** — two-column interleaving on pre-1994 PDFs producing sentences and words that exist in no document, including cross-resolution merges | §2.1–2.4 | ~6,800 paragraphs / ~2,600 documents (37% of the 7,003 standalone PDF docs) | high on class, medium on magnitude (n=400, hand-adjusted 0.80) |
| 2 | **Empty shells** — body typed `frontmatter`, hidden by the render gate | §3 | **3,116 of 25,734 documents (12.1%) render zero words**; 3,530 render <30 | exact (full-corpus query) |
| 3 | **Silent loss** — decisions printed in the volumes and never harvested | §4 | **554 of 3,902 (14.2%)** | exact against a source-derived denominator |
| 4 | **Over-capture / misattribution at split boundaries** — trailing content absorbed by the last child | §2.5 | 10 children >10,000 words, ~7 confirmed over-capture, all on the A/HRC path; plus heading/footnote bleed in A/DEC | high, small n |
| 5 | **Wrong-language content in `lang='en'` rows** — French/Spanish columns of trilingual Official Records sheets | §2.4 | seen in 3 of 30 hand-read flagged paragraphs → ~0.7% of PDF-path paragraphs | medium |
| 6 | **Structural content never rendered** — footnotes (946,883 w), tables (457,073 w), vote records (16,715 w), signatures (103,941 w) stored and hidden | §3 | 2.92m words = 9.2% of the corpus | exact |
| 7 | **Latent SQL 3-valued-logic bug** in `paragraphs.ts` masthead filter | §3a | 78,156 rows / 21,879 documents would be affected the moment `frontmatter` is un-hidden | exact |
| 8 | **Partial omnibus segmentation** — bracket part `[B]` silently falls back to the whole parent | §1(g) | 85 of 741 omnibus parents (11.5%) | exact |

## 7. The two numbers that should replace the headline

- "25,734 documents with fulltext" → **22,618 documents whose text is visible on the site**
  (25,734 − 3,116), and of those, ~2,600 in the pre-1994 PDF stratum contain at least one
  passage that is not in the document it is attributed to.
- Decision coverage should be quoted against the printed volumes: **3,348 of 3,902 (85.8%)**,
  not against the count of children the splitter produced.

## 8. Cheapest checks that would have caught each of these

1. **Fabrication (class 1).** A total conservation check: every stored paragraph's 8-grams must
   be findable in its own source file. It runs at ~1 doc/s over the archive, needs no downloads,
   and it is what found all of §2. Ship it with the A/RES/1514(XV) case as a permanent negative
   control.
2. **Empty shells (class 2).** Assert `rendered_words > 0` for every symbol counted as "has
   fulltext", using the *website's* `HIDDEN_TYPES` set as the single source of truth (import it,
   do not re-list it). Negative control: force one document's body to `frontmatter` and require
   the check to fail.
3. **Silent loss (class 3).** Denominator from the volume's own printed decision numbers, run at
   split time, refusing to mark a volume `split` unless `children == printed`. Negative control:
   delete one child and require a non-zero exit.
4. **Over-capture (class 4).** A per-child word-count bound derived from the source block
   (`child_words ≤ 1.15 × block_words`), which flags A/HRC/PRST/8/2 at 108,207 vs ~120.

None of these is a sampled check; all four are conservation checks over the whole corpus.

---

### Appendix — reproduction

Scratch dir `/Users/david/.claude/jobs/3f4ded06/tmp/adv3/`:
`fetch.sh` (ODS), `batch.sh` (the 20-symbol download list), `compare.py` (per-document
source↔DB diff), `scan.py` / `scan2.py` (bulk detector, PDF and docx/doc/wpd),
`coverage.py` (volume decision denominators), `cov.json`, `scan_{pdf,docx,doc,wpd}.tsv`.
No writes to the database or the archive.
