# OCR engine evaluation for the deferred (scanned) fulltext backlog

Status: **experiment + recommendation only.** Nothing here runs in production. All
scratch artifacts live on the external SSD (`/Volumes/SSDAStorage/ocr-experiments/`)
and the large model/venv downloads are torn down at the end (see *Cleanup*).

## 1. Why this exists

The deterministic Word path (`fulltext_extract_raw.py`, raw-v2) and the born-digital
PDF path (`fulltext_extract_pdf.py`, pdf-v1) together cover everything with a usable
text layer. What is left are **image-only scans with no recoverable text layer** —
`document_files.status = 'no_text_layer'`. As of this run:

| status         | files  |
|----------------|-------:|
| parsed         | 22,136 |
| **no_text_layer** | **4,907** |
| unavailable    |  4,898 |
| fetched        |    176 |

The 4,907 deferred scans break down as **3,380 `E/RES/…` (ECOSOC)**, **1,523
`A/RES/…` (GA)**, 4 stray `S/RES`. On top of the loose resolution scans there are the
~80 scanned compilation volumes (`A/NN/49 Vol.II/III` and older "Resolutions and
Decisions" supplements) holding the 5,247 GA decisions. Ballpark **25–40k pages**;
projections below use **30k** as the working figure.

The question: **which OCR engine, at what quality and what compute cost, and run
where?** The output has to feed the existing frozen semantic parser (`fulltext_parse.py`,
sem-v4) through its style-less lexical path, exactly like the pdf-v1 rows do.

## 2. Machine

| | |
|---|---|
| Model | MacBook Air (MacBookAir10,1), Apple **M1** |
| Cores | 8 = 4 performance + 4 efficiency |
| RAM | **8 GB** (unified) |
| OS | macOS (Darwin 25.5) |
| Root disk free | ~2–3 GB (very tight) |
| Scratch | external **exFAT SSD**, 843 GB free — all venvs, model caches, rendered pages here |

Two hard constraints shaped the experiment: **8 GB RAM** (rules out large-batch VLM
inference; llama.cpp/Surya paged heavily) and **~3 GB root headroom** (every venv,
the 1.4 GB Surya GGUF and all rendered PNGs were redirected to the SSD; `HF_HOME`,
`UV_CACHE_DIR`, `TMPDIR` all repointed). The exFAT SSD spawns AppleDouble `._*`
sidecar files that broke `uv` hardlinking and, worse, made `transformers`' module
scanner choke on `._*.py` stubs — worked around with `UV_LINK_MODE=copy` and a
recursive `find … -name '._*' -delete` before each Surya run. Noting this because it
will bite anyone who tries to run the Python OCR stack off that SSD.

## 3. Benchmark set

The clean design goal — *a document with BOTH a perfect text and a scanned image of
the same page* — runs into a hard reality: **the era that has clean Word-derived
ground truth (1994+) is exactly the era ODS serves as born-digital PDFs, and the era
that is genuinely scanned (pre-1994) has no independent ground truth** (its DB text
was itself extracted from the scan's own text layer, pdf-v1). I verified this: the
five 1994–96 `S/RES` PDFs pulled from ODS (`t=pdf`) all came back **born-digital** —
0 embedded images, a clean pymupdf text layer — *not* scans.

So the benchmark is deliberately **three tiers**:

### Tier 1 — ground-truth CER, clean render (optimistic bound + reading-order + speed)
Five SC resolutions whose DB paragraphs are **Word-derived (raw-v2 = independent,
high-fidelity ground truth)**, rendered from their born-digital ODS PDF at **300 DPI**
(pymupdf) to a crisp image, text layer discarded:

| symbol | pages | GT chars | source |
|---|--:|--:|---|
| S/RES/895(1994)  | 2 | 2,018  | raw-v2 (Word) |
| S/RES/917(1994)  | 6 | 15,420 | raw-v2 |
| S/RES/942(1994)  | 6 | 12,796 | raw-v2 |
| S/RES/1031(1995) | 6 | 13,988 | raw-v2 |
| S/RES/1088(1996) | 6 | 17,472 | raw-v2 |

ODS fetches were polite: `curl/8.7.1` UA, `%28/%29`-encoded symbols, 2 s spacing, five
requests total.

### Tier 2 — degraded render (realistic scan estimate)
The same five pages put through a **synthetic-scan degradation**: grayscale → ±1.1°
rotation → 60 % downscale-and-back (resolution loss) → Gaussian blur 0.8 → contrast
×0.85 → Gaussian noise σ=10 → salt-and-pepper speckle → JPEG q55. This brackets a
mediocre 1980s photocopy/microfiche scan while keeping the **same known ground truth**.

### Tier 3 — real scans (qualitative + engine agreement, no ground truth)
Actual deferred-class scans pulled from the SSD archive:

| id | doc | era | layout | reference |
|---|---|---|---|---|
| res1960 | A/RES/1514(XV) page | 1960 | single-col compilation page | legacy embedded OCR (silver) |
| res1975 | E/RES/1970(LIX) | 1975 | **two-column** | none (true `no_text_layer`) |
| vol2003 | A/57/49 Vol.II pp.10–11 | 2002/03 | single-col GA decisions volume | legacy text layer (silver) |

(No pre-2003 volume exists as an image-only scan in the archive — the volumes present
carry a legacy text layer — so vol2003 stands in for the volume *reading-order* test.)

## 4. Metric methodology

- **CER** = `Levenshtein(ref, aligned_window(hyp)) / len(ref)`; **WER** the same on
  whitespace tokens. Distances via `rapidfuzz`.
- **Normalization** (both sides): lowercase; NFKD accent-strip; **de-hyphenate**
  line-break hyphens (`inter-\nnational`→`international`); collapse all whitespace to
  single spaces; keep basic punctuation `'"().,;:/%-`.
- **Local alignment**: the scan page carries masthead/footer/page-number material that
  the ground-truth body does not. To avoid penalising an engine for correctly reading
  boilerplate, the hypothesis is trimmed to the **best-matching window** against the
  reference (`rapidfuzz.fuzz.partial_ratio_alignment`) before scoring. This means CER
  measures *"how well did it read the content that is in the reference"*, not padding.
- **Reading order** is reconstructed by us for the box-based engines (Vision, RapidOCR):
  line-group by y-proximity, sort x within a line, and a **histogram-valley two-column
  detector** (find a sparse gutter band in the normalised left-edge distribution with
  both sides ≥25 % populated). Tesseract and Surya emit reading order themselves.
- **Timing**: median wall-clock **sec/page**, measured **sequentially and uncontended**
  (the canonical figures); one-time model-load reported separately. Concurrent runs
  inflate CPU-engine timing badly (a contended tesseract page measured 14 s vs 2.1 s
  uncontended), so contended numbers are not used. Peak RSS via `getrusage`.

## 5. Engines

| engine | install | notes |
|---|---|---|
| **Tesseract 5.5.1** | `brew` (already present) | classic CPU baseline; native page-segmentation (`--psm 3`) does column detection |
| **Apple Vision** via `ocrmac` | `pip install ocrmac` (pyobjc, tiny, no model download) | uses the Mac's on-device accelerated OCR (Neural Engine); returns text+bbox, **reading order is on us** |
| **RapidOCR** | `pip install rapidocr-onnxruntime onnxruntime` | runs the **PP-OCRv4** models (the same models PaddleOCR ships) via ONNX Runtime — a lightweight modern option |
| **Surya** (`surya-ocr` 0.22.1) | `pip install surya-ocr` + `brew install llama.cpp` | modern document-OCR; **0.22 is now a VLM served through llama.cpp** (GGUF, 1.4 GB), runs on M1 Metal; emits its own reading order incl. italics/footnotes |
| **PaddleOCR** | **skipped** | `paddlepaddle` has no reliable Apple-Silicon wheel and fights the 3 GB root disk; **RapidOCR already runs Paddle's PP-OCRv4 models via ONNX**, so its quality stands in for PaddleOCR here without the install pain |

`fra` traineddata is present for Tesseract (`eng+fra` available) for the
French-interleaved older records; the English-only pass was used for the CER numbers.

## 6. Results

### 6.1 Speed & footprint (canonical, sequential, uncontended)

| engine | median s/page | one-time load | peak RSS | device |
|---|--:|--:|--:|---|
| **Apple Vision (ocrmac)** | **0.82** | 6.7 s | 273 MB | Neural Engine |
| Tesseract | 2.14 | ~0 | 24 MB | CPU |
| RapidOCR | 6.77 | ~0 (lazy) | 1,061 MB | CPU (ONNX) |
| Surya (VLM) | **67.2** | ~23 s | 303 MB py + llama-server (GGUF 1.4 GB) | M1 Metal |

### 6.2 Character/word error rate

**Tier 1 — clean 300 DPI render** (mean over the 5 docs; per-doc CER in parentheses):

| engine | CER % | WER % | notes |
|---|--:|--:|---|
| **Tesseract** | **1.64** | 2.33 | 895 3.3 · 917 1.1 · 942 1.2 · 1031 1.1 · 1088 1.6 — uniformly excellent |
| Surya | 1.92 | 2.33 | (n=2: 895 2.6 · 942 1.3) |
| Apple Vision | 9.35 | 10.59 | 942 3.6 · 1031 3.3 · 1088 2.2 · 917 9.6 · **895 28** (see failure mode) |
| RapidOCR | 15.71 | 21.82 | spacing loss, full-width-punctuation substitution, dropped lines (best of its runs, column-aware sort) |

**Tier 2 — degraded (simulated scan)** (mean over 5 docs):

| engine | CER % | WER % | notes |
|---|--:|--:|---|
| **Surya** | **1.92** | 2.35 | (n=2: 895 2.6 · 942 1.3) — **identical to its clean score; degradation barely touches it** |
| **Tesseract** | 4.46 | 14.85 | 3.2–6.1 % CER, very consistent; WER jumps (word splits under noise) |
| Apple Vision | 9.91 | 11.52 | 942 2.3 · 1031 2.8 · 1088 7.6 · 917 10.3 · **895 26.5** |
| RapidOCR | ~13 (n=2) | | worst of the four; erratic |

**Apples-to-apples on the two docs all four engines fully covered (895, 942):**

| engine | clean CER % | degraded CER % | Δ |
|---|--:|--:|--:|
| **Surya** | **1.92** | **1.92** | **0.0** — flat under degradation |
| Tesseract | 2.24 | 4.90 | +2.7 |
| Apple Vision | 15.89 | 14.43 | (895 outlier dominates) |
| RapidOCR | 24.71 | 12.68 | erratic |

The headline: **Surya is the only engine whose accuracy does not move when the page is
degraded** — a VLM reads through blur/noise/rotation that costs Tesseract 2–3 CER points.
That is exactly the property the *pre-1994 scan* backlog needs.

The Vision **895 outlier** is a real, reportable failure mode, *not* a scoring
artifact: on a short doc whose "Recalling its resolutions 425 (1978) … 501 (1982) …
520 (1982) …" block is a dense, number-heavy justified paragraph, Vision returns the
line as several geometric fragments and our reconstruction mis-orders them:

```
Recalling its resolutions 425 (1978) and 426 (1978) of 19 March 1978,
(1982) of 5 June 1982, 509 (1982) of        <- fragment, out of order
501 (1982) of 25 February 1982, 508          <- should precede the line above
6 June 1982 and 520 (1982) of 17 September 1982, as well
```

Same root cause as two-column interleave: **Vision gives geometry, not reading order,
and dense/short blocks are ambiguous to reconstruct.** On long, plainly-set resolution
bodies Vision is 2–4 % CER and competitive; it is the reconstruction of awkward
geometry that costs it.

### 6.3 Qualitative — real scans

**res1975 — E/RES/1970(LIX), 1975, TWO-COLUMN, true image-only.** This is the make-or-
break case for the box engines. With a **naive** top-to-bottom sort, Vision interleaves
the two columns line-by-line into nonsense:

```
Recalling that the International Labour Conference   1. Takes note of the report of the World Food Council
on its first session 99 and transmits it to the General
adopted at its sixtieth session a declaration on equality
```

With our **histogram-valley column detector** the same Vision output reads correctly,
left column fully then right:

```
Recalling that the International Labour Conference
adopted at its sixtieth session a declaration on equality
of opportunity and treatment for women workers, in
which it considered that the establishment of a new
...
```

**Tesseract** reads res1975's two columns correctly **out of the box** (`--psm 3`).
**Surya** is the standout: it reads both columns in perfect order *and reconstructs
wrapped lines into whole paragraphs*, preserving the italic lead-verbs and enumerators
the parser wants:

```
Recalling  that the International Labour Conference adopted at its sixtieth session a declaration on equality of opportunity ... particularly in the developing countries,
Emphasizing  that the items on the agenda of the Tripartite World Conference ...
1.  Requests  the regional commissions to give the fullest possible co-operation ...
2.  Further requests  the competent bodies of the United Nations Conference on Trade and Development ...
1978th plenary meeting  30 July 1975
1969 (LIX). Food problems
```

**res1960 — A/RES/1514(XV) page, 1960 scan.** Tesseract:

```
66  General Assembly — Fifteenth Session
... should be admitted to membership in the United Nations,
Having considered the application for membership of the Republic of Mali,
Decides#8 admit the Republic of Mali to membership in the United Nations.
876th plenary meeting, 28 September 1960.
1492 (XV). Admission of the Federation of Nigeria to membership ...
```

Body prose and the enumerator `1492 (XV).` are clean; the Tesseract failure mode is
**superscript footnote markers** — `,®1`, `Decides#8` (the word "to" collided with a
footnote superscript). Legible and parseable, footnote refs unreliable. **Surya** reads
the same page cleanly — `Decides to admit …`, footnote refs as tidy superscripts
`… Mali, 32` — no marker collisions.

**vol2003 — A/57/49 Vol.II, GA decisions volume (single column).** Tesseract:

```
Decisions
A. Elections and appointments
57/401. Appointment of the members of the Credentials Committee
At its 1st plenary meeting, on 10 September 2002, the General Assembly, in accordance
with rule 28 of its rules of procedure, appointed a Credentials Committee ...
57/402. Election of five non-permanent members of the Security Council
```

Clean — the modern GA decisions volumes are single-column and the decision enumerators
(`57/401.`) read perfectly. Older "Resolutions and Decisions" supplements are the
two-column case and need the column handling shown above.

**Engine agreement (qual pages, pairwise normalised similarity).** With reading order
corrected on all engines, agreement is high across the board — a good sign that the OCR
is converging on the same text and there is no silent, systematic misread:

| page | tess·surya | tess·vision | vision·surya | rapid·surya |
|---|--:|--:|--:|--:|
| res1975 (2-col 1975) | 98.7 | 98.7 | 98.0 | 98.0 |
| res1960 (1960) | 92.5 | 96.2 | 89.8 | 92.0 |
| vol2003 (volume) | 99.7 | 99.8 | 99.9 | 94.4 |

The instructive contrast: before the column-aware sort, Vision agreed only **57 %** with
the others on the two-column res1975; after it, **98 %**. The whole Vision gap was reading
order, not recognition. The oldest page (res1960) is where engines diverge most (~90–96 %)
— older typefaces and footnote superscripts — but even there they broadly concur.

## 7. Projections

Serial Mac (30k pages × canonical s/page):

| engine | s/page (cold) | 30k serial | 30k parallel (Mac) | comment |
|---|--:|--:|--:|---|
| Apple Vision | 0.82 | **6.8 h** | ~5.3 h (1.29×) | ANE-bound; threads barely help; Mac-only |
| Tesseract | 2.14 | 17.8 h | **~6.2 h (2.86×)** | 4 processes; no gain past 4 (E-cores don't help) |
| RapidOCR | 6.77 | 56.4 h | — | not worth it |
| Surya (M1 Metal) | 67 | **558 h (23 d)** | — | GPU-only in practice |

**Parallel speedup was measured, not assumed** (27-page batch, uncontended): Tesseract
**2.86×** with 4 worker processes and **no further gain at 6** — the four performance
cores carry it and the efficiency cores add nothing. Apple Vision only **1.29×** with 4
threads — it is bound by the single shared Neural Engine, not CPU, so parallelism barely
helps. So realistically: **Tesseract ~6 h parallel, Vision ~5 h.**

**Thermal caveat (fanless M1 Air):** absolute per-page times drift up ~2× under sustained
load — a *cold* Tesseract page measured 2.14 s but a *warm* one (after ~1 h of continuous
OCR) 5.11 s. The speedup ratios above hold within a session; the wall-clock projections
use the cold numbers and should be read as optimistic — budget closer to **10–12 h** for
the real, thermally-throttled 30k local run. (Another reason the rented GPU wins.)

### Rented GPU path (Surya, and only Surya, benefits)

Tesseract is CPU-bound (a rented GPU does nothing for it) and Apple Vision **cannot run
off-Mac at all** (Apple framework). Only Surya (and a hypothetical PaddleOCR/GPU) gain
from a GPU. Surya's own guidance and third-party benchmarks put it at **~5–10 pages/s on
a modern GPU** (e.g. ~5 pg/s on an RTX 5090 at high concurrency); the 0.22 VLM path is
heavier, so a conservative planning range is **2–4 pg/s single-to-moderate concurrency**.

At 3 pg/s, 30k pages = **~2.8 h**. Current on-demand rates (July 2026):

| provider / GPU | $/hr | Surya 30k @ 3 pg/s (~2.8 h) |
|---|--:|--:|
| Vast.ai RTX 4090 (interruptible) | 0.29–0.39 | **$0.8–1.1** |
| RunPod RTX 4090 (community) | 0.34 | $1.0 |
| RunPod RTX 4090 (secure) | 0.69 | $1.9 |
| RunPod L40 | 0.86 | $2.4 |
| RunPod A100 / Lambda A100 | 1.29–1.99 | $2–4 (≈2× faster ⇒ similar $) |

Even at a pessimistic 1 pg/s the whole backlog is **under $10**. Surya on a rented 4090
is roughly **200× faster wall-clock than on this M1** for a couple of dollars.

**Data transfer:** 30k pages as 300 DPI PNGs is **~13 GB clean, ~180 GB for noisy
grayscale scans** (degraded pages measured at ~6 MB each). Do **not** ship pages — ship
the **~3–4 GB of source PDFs and render server-side** on the GPU box (pymupdf at 300 DPI),
which also keeps the rendering identical to what was benchmarked.

## 8. Quality verdict — is OCR good enough for the sem-v4 parser?

The parser needs three things and tolerates a fourth's absence:
1. **Reading order / paragraph structure** — required (its state machine walks
   opening-formula → preamble → first-operative in order).
2. **Readable operative enumerators** (`1.`, `(a)`, `57/401.`) — required; sequence
   confirmation can bridge a single garbled one but not a run.
3. **Lead-verb first words** (`Recalling`, `Noting`, …) for the verb annotation — nice
   to have; matched against an exact set, so a mangled verb loses its label but the
   state machine still places the clause.
4. **Letter-level prose noise** — tolerated (the lexical path already survives things
   like `Recallinx`; footnote-marker garbage like `Decides#8` is cosmetic).

Measured CER by era, and the resulting expected **usable-document yield**:

| era / class | representative CER | limiting failure modes | expected usable yield |
|---|---|---|---|
| **1990s+ scans / born-digital** | 1–4 % | none material | **~95 %+** |
| **1970s–80s** (res1975 class) | ~4–10 % (Tier-2 proxy) | two-column reading order (solved w/ column split), footnote superscripts | **~85–90 %** |
| **1946–60s** (res1960 class) | ~5–10 % | superscript footnote refs, older typefaces, occasional special chars | **~80–85 %** |
| **Two-column "Res. & Decisions" volumes** | body ~4–8 % | column gutter detection is the whole game; enumerators otherwise clean | **~85 %** with column handling, near-0 without |

The dominant risk is **structure, not characters**: an engine that scrambles reading
order (Vision on two-column / dense blocks without our column split) produces
low-character-error but structurally-broken text the parser cannot walk. An engine that
preserves order with 4–8 % character noise (Tesseract everywhere; Surya everywhere;
Vision *with* our column logic) yields text the parser handles well.

## 9. Recommendation

**Primary: Surya on a rented RTX 4090.** For a *one-time* backfill of ~30k pages of the
*oldest, most-degraded, most two-column* material we have, quality is the objective and
Surya wins it outright, at trivial cost:

- **Best and most robust measured quality**: 1.9 % CER, and — uniquely — **flat from
  clean to degraded** (the VLM reads through blur/noise/rotation). It is the only engine
  that handled the two-column res1975 *and* the footnote-heavy 1960 page *and* the volume
  page natively, with correct reading order and **paragraph reconstruction** built in.
- **Least downstream work**: because Surya emits reading order, columns, italics and
  merged paragraphs itself, we do **not** have to build column-cropping or fragment
  re-ordering around it. That removes the single biggest failure mode (structure, not
  characters) for free.
- **Cost is a rounding error**: the whole backlog is **~3 h and ~$1–4** on a Vast.ai /
  RunPod RTX 4090. **Render pages server-side from the 3–4 GB of source PDFs** (pymupdf
  300 DPI, identical to the benchmark) rather than shipping tens of GB of PNGs.
- 67 s/page locally on this M1 rules out running Surya here — the GPU is what makes it
  practical, and it is cheap enough that there is no reason not to.

**Local fallback if a rental is genuinely off the table: Tesseract** (not Apple Vision).
Tesseract is 1.6 % clean / 4.5 % degraded CER, **handles columns and reading order
natively** (no preprocessing to build), has **no bad outliers**, costs nothing, needs no
model download, and finishes the backlog in **17.8 h serial** (a few hours across the
M1's four P-cores — see §7). Its only real weakness is superscript footnote markers.

Apple Vision is the *fastest* per page (0.82 s) but is the wrong batch default: it is
**Mac-only**, it scrambles two-column and dense numeric blocks unless *we* first crop
pages into single-column strips (our gutter-valley logic can do this), and even then it
trails Tesseract on consistency. Keep it as an **optional fast local pass on the easy
1990s+ single-column strata**, where its 2–4 % CER and 0.82 s/page shine — not as the
engine for the hard old scans this backlog is mostly made of.

**Drop RapidOCR** (worst CER, erratic, 1 GB RSS, no upside).

Decision in one line: **rent a 4090 and run Surya over everything (~$1–4, ~3 h); if you
must stay local, run Tesseract; use Vision only to sprint through the clean modern
single-column pages.**

### Preprocessing
- **Surya / Tesseract**: 300 DPI pymupdf render → grayscale → OCR the whole page (both
  handle columns and reading order themselves). Deskew helps the oldest scans; a clean
  binarization threshold helps Tesseract specifically (Surya prefers grayscale).
- **Apple Vision only** (if used on multi-column pages): add **our column split (gutter
  valley) into single-column strips** before OCR, then concatenate — this is what
  rescues it from the two-column interleave and dense-block fragment scramble.

## 10. Following-phase design sketch (feeding `document_paragraphs_raw`)

Mirror the existing **pdf-v1** contract so the frozen parser needs zero changes:

- New extractor **`fulltext_ocr_pdf.py`**, `extractor_version = 'ocr-v1'`, targeting
  `document_files.status = 'no_text_layer'`.
- Per page: render 300 DPI → OCR (Surya on the GPU box, or Tesseract locally) → feed the
  text into the **same line→paragraph reconstruction already in
  `fulltext_extract_pdf.py`** (left-edge indent + vertical gaps + terminal punctuation,
  conservative de-hyphenation, header/footer drop). OCR only replaces the *span source*
  (image→text) ahead of that geometry stage. With Surya, whose paragraphs already come
  reconstructed, that stage mostly validates and drops headers/footers.
- Emit `document_paragraphs_raw` rows: `kind='paragraph'/'empty'`, `props` carrying the
  usual size/indent/all_caps plus **`ocr=true`, `ocr_engine`, `ocr_confidence`,
  `textlayer_score=0`**; advance the ledger `no_text_layer → extracted` (add an
  `ocr_extracted` sub-state if we want to keep it distinct).
- **Confidence gating**: keep the engine's per-line/'page confidence; a page below a
  confidence threshold — or, if running two engines, where they disagree beyond a CER
  bound — gets `status='ocr_low_confidence'` for review rather than silent ingestion.
- Then run `fulltext_parse.py` unchanged. Backfill the compilation **volumes** through
  the existing `fulltext_split_volumes.py` TOC splitter first, so each decision becomes
  its own crop before OCR.

## 11. Honest limitations

- **No true paired ground truth on real scans.** Tier-1/2 CER is measured against
  Word-derived text on *clean and synthetically-degraded* born-digital pages; the
  degradation is a plausible model of a scan, not a specific real scan. Real
  1940s–60s microfiche may be worse than Tier-2 suggests. Tier-3 gives real-scan
  *qualitative* evidence but only silver (legacy-OCR) references.
- **n is small** (5 ground-truth docs, 27 pages/tier; Surya on 2 docs to bound compute
  at 67 s/page). Enough to separate the engines by an order of magnitude, not to nail a
  CER to a decimal.
- **Surya 0.22 is a moving target** — the llama.cpp VLM backend is new; its GPU
  throughput is quoted from vendor/third-party figures, not measured here (this M1
  cannot represent GPU speed).
- Timing is **M1 MacBook Air (8 GB)** specific; a Mac with more RAM / an M-series Pro/Max
  would move Vision and Tesseract faster and might make local Surya merely slow rather
  than infeasible.

---
*Sources for GPU pricing/throughput: RunPod, Vast.ai, Lambda published rates (July 2026);
Surya throughput from surya-ocr project docs and third-party OCR benchmarks.*
