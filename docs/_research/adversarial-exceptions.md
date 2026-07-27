# Adversarial audit of pipeline exceptions

**FAIL — three separate defects, each hidden by an exception nobody had tested. (1) The
volume gate's cross-volume exception, added today, is tuned-to-green: its 15 "two
printings of the same decision" are 15 *different* GA actions at different plenary
meetings, one of each is discarded (23,378 characters, incl. `A/DEC/77/408`,
`A/DEC/65/407`), and removing that one exception restores 10 of the 11 original failures.
(2) The gate around it scores coverage against the whole volume rather than its children,
so `A/HRC/10/29` (51 adopted texts, ZERO children written) scores 1.000 PASS, 3 volumes
are graded against an EMPTY ground truth and also score 1.000, and 1,004 of 4,594 in-scope
catalog decisions (21.9%) have no stored text at all. (3) Worse than either: the PDF
extractor **fabricates 263 operative-paragraph numbers that are not in the source**
(`S/RES/661(1990)` stores an invented `19.` on a mid-sentence fragment of operative 3(c)),
**silently truncates 145 documents to 20.9% of their source with `flags=[]`**
(`A/RES/701(VII)` on Korea = 47 characters, the title line only; `A/RES/1016(XI)` on
apartheid = 127 characters), deletes ~2,060 operative markers as "page numbers" and 8.12%
of hyphenated compounds — all behind a `--self-test` that carries no negative control for
any of them, while the PDF acceptance gate covering 40% of the corpus anchors its ground
truth on the parse it is grading and prints `preserved=100.00%`, exit 0, on documents with
90% of their content deleted. Emitted token conservation on a 500-document sample:
**53.60%**. End to end, of 4,594 in-scope decisions only 707 (15.4%) are readable, and none
of these gates has ever run in CI because the workflow exists only on this branch.**

Date: 2026-07-27. Worktree `documents.unfck.org/.claude/worktrees/fulltexts`,
branch `feature/fulltexts` @ `79bdd29`. Production DB and the SSD archive were read
only; all scratch work is under `/Users/david/.claude/jobs/3f4ded06/tmp/adv2/`.

Doctrine applied: `programme-budget-data/CONTRIBUTING.md` rules 1, 2, 3, 5, 6 and
`docs/LESSONS.md` ("an exception is a claim like any other"; "denominators come from
the source"; "absence is invisible to anything that iterates over what is present";
"a check that has never been shown to fail is absent, not passing").

---

## Part 1 — the three changes that took the volume gate from 78/89 to 89/89

All three live in `python/fulltext_verify_volumes.py`, introduced together in
`72d5101` (the file has exactly one commit, so there is no before/after diff to read;
each claim below was tested against the archive and the DB instead).

### Change (1) — read the CONVERTED docx when `archive_path` is a binary `.doc`
`fulltext_verify_volumes.py:101-111`

**Claim.** "archive_path points at the ORIGINAL, which for the HRC `.doc` reports is
an OLE binary python-docx cannot open — ground truth must come from the converted/
sibling."

**Verified true, as a crash fix.** `Document('.../original/A_HRC_10_29.doc')` raises
`PackageNotFoundError`. All 20 HRC volumes are `format='doc'`, and for all 20 the
guessed path `converted/<stem>.docx` exists and equals the ledger's `converted_path`
(0 mismatches). LibreOffice conversion is not lossy either: an independent read of the
original `.doc` with macOS `textutil` yields 6,502 token types for `A/HRC/10/29`
against 6,497 in the extractor's stored rows — the 5-type difference is
`['020210','09','17451','ge','mergeformat']`, i.e. field codes.

**But it silently destroys the check's stated independence, and nothing records that.**
The module docstring promises the ground truth "RE-EXTRACTS the archived volume file
through a DIFFERENT code path". After this change the HRC ground truth reads *the same
converted file* with *the same library* (`python-docx`) that `fulltext_extract_raw.py`
uses (`fulltext_extract_raw.py:609` `rel = converted_path or archive_path`;
`:428 Document(str(path))`). Worse, the gate uses `doc.paragraphs` — top-level body
paragraphs only — while the extractor also walks tables, text boxes and footnotes. The
ground truth is therefore a strict **subset** of the thing it is grading:

| HRC volume | GT token types | extractor token types | GT ∖ extractor | extractor ∖ GT |
|---|---:|---:|---:|---:|
| A/HRC/10/29 | 6,061 | 6,497 | **0** | 436 |
| A/HRC/7/78 | 4,218 | 5,667 | **0** | 1,449 |
| A/HRC/4/123 | 2,433 | 3,680 | **0** | 1,247 |
| … all 20 | — | — | **0 in every case** | 22 – 1,449 |

`GT ∖ extractor = 0` for all 20 volumes is not evidence of fidelity — it is a
structural certainty. Coverage 1.000 on the HRC track is a tautology, and the check
can never see loss of table, text-box or footnote content.

**Verdict: justified as a crash fix, but it converts the HRC half of the gate into a
circular check, undeclared.** Fix: read the original `.doc` through a genuinely
independent reader (`textutil -convert txt`, `antiword`), or at minimum walk
`document.element.body` rather than `doc.paragraphs` and state in the docstring that
HRC ground truth is LibreOffice-mediated.

### Change (2) — exclude "page-bottom footnote apparatus" from the round trip
`fulltext_verify_volumes.py:194-209`

**Claim.** "Page-bottom footnote apparatus (the underscore rule, footnote-numbered
citations, recorded-vote country lists) is inlined into slices by the PDF extractor but
dropped by the parser corpus-wide by design — it stays in the raw layer. Its tokens are
an allowed round-trip drop."

**The claim is a non-sequitur, and the exception excuses nothing.**

1. *The parser is not in this comparison at all.* `read_db_children`
   (`:151-161`) reads `digitallibrary.document_paragraphs_**raw**`, not
   `document_paragraphs`. The round trip compares the fresh split's slices against the
   **raw** child rows. The stated justification — "dropped by the parser … it stays in
   the raw layer" — is an argument for why the tokens **should be present** on both
   sides of this comparison. It cannot explain a difference here.
2. *Measured excuse: zero.* Re-running the round trip over all 89 volumes with and
   without the apparatus term: `lost` is identical in every volume.
   **Tokens excused by this exception across the whole corpus: 0.**
3. *But the blanket it holds is large.* The predicate matches rows carrying **9,236
   token types summed over the 68 of 89 volumes where it fires at all** (601 in
   `A/57/49(VOL.II)`, 533 in `A/58/49(VOL.II)`, …). Any future round-trip divergence whose tokens
   happen to occur anywhere in an underscore rule, a numbered citation, or a
   recorded-vote country list will be waived silently. Country lists in particular are
   long and lexically rich — a recorded-vote list is most of the world's country names.

**Verdict: unjustified. Its stated mechanism does not describe the code it guards, and
it currently absorbs nothing, so removing it costs nothing and restores 9,236 token
types of sensitivity.** This is the shape LESSONS.md warns about: a band whose
*mechanism* and whose *width* are unrelated.

### Change (3) — existence, not token identity, for cross-volume multi-part children
`fulltext_verify_volumes.py:210-232`

**Claim.** "A fresh child stored under the SIBLING volume (cross-volume longest-wins)
is fine — but its two printings extract with different page artifacts, so token
identity cannot hold across them. Require existence; exclude its tokens from the
comparison."

**The claim is false, and the exception is hiding real, quantified content loss.**

I re-split every volume and collected every child symbol produced by more than one
volume: **15 symbols**. For each I pulled both printings and diffed them. In **all 15**
the two printings record **different plenary meetings on different dates** — they are
different GA actions taken under the same decision number at different parts of the
session (the resumed-session Vol III action vs the main-part Vol II action), not one
text printed twice:

| symbol | Vol II printing | Vol III printing | Jaccard (token types) | stored | discarded |
|---|---|---|---:|---|---:|
| A/DEC/77/408 | 34th plenary, 15 Nov 2022 | 87th plenary, 30 Jun 2023 | 0.799 | Vol III | **1,879 c** |
| A/DEC/65/407 | 51st plenary, 19 Nov 2010 | 78th plenary, 15 Mar 2011 | 0.661 | Vol III | 1,879 c |
| A/DEC/76/414 | 52nd plenary, 16 Dec 2021 | (announcement 8 Mar 2022) | 0.478 | Vol II | 976 c |
| A/DEC/78/528 | 45th plenary, 7 Dec 2023 | 55th/77th/107th plenary 2024 | 0.155 | Vol III | 1,733 c |
| A/DEC/60/503 | 4 meetings, Sep–Dec 2005 | 7 meetings, Feb–Aug 2006 | 0.221 | Vol III | 2,167 c |
| A/DEC/59/551 | 76th plenary, 23 Dec 2004 | 91st/104th/117th plenary 2005 | 0.350 | Vol III | 4,889 c |
| A/DEC/58/564 | 79th plenary, 23 Dec 2003 | 83rd/91st plenary 2004 | 0.372 | Vol III | 2,491 c |
| A/DEC/59/406 | 57th plenary, 19 Nov 2004 | 80th/116th plenary 2005 | 0.209 | Vol III | 1,166 c |
| A/DEC/60/502 | 4 meetings, Sep–Dec 2005 | 77th plenary, 28 Apr 2006 | 0.217 | Vol II | 557 c |
| A/DEC/60/402 | 26th plenary, 4 Oct 2005 | 76th plenary, 13 Apr 2006 | 0.464 | Vol III | 769 c |
| A/DEC/60/405 | 43rd plenary, 3 Nov 2005 | 74th plenary, 27 Mar 2006 | 0.556 | Vol III | 1,201 c |
| A/DEC/60/409 | 53rd plenary, 23 Nov 2005 | 73rd plenary, 16 Mar 2006 | 0.482 | Vol III | 381 c |
| A/DEC/60/411 | 53rd plenary, 23 Nov 2005 | 73rd plenary, 16 Mar 2006 | 0.839 | Vol III | 1,401 c |
| A/DEC/58/411 | 72nd plenary, 9 Dec 2003 | 80th plenary, 9 Feb 2004 | 0.683 | Vol II | 651 c |
| A/DEC/59/408 | 69th plenary, 8 Dec 2004 | 101st plenary, 6 Jun 2005 | 0.825 | Vol II | 1,357 c |

**23,378 characters of adopted GA decision text are discarded**, one whole printing per
symbol (the "discarded" column above is the joined slice length; subtracting the
row separators gives the exact per-symbol figures 647 / 2,469 / 1,151 / 1,350 / 4,854 /
769 / 1,194 / 381 / 1,399 / 555 / 2,159 / 1,874 / 970 / 1,879 / 1,727 = 23,378). Verbatim proof for `A/DEC/77/408` — what is stored (Vol III) and what was
thrown away (Vol II):

```
STORED   77/408. Appointment of members of the ACABQ / B1
         At its 87th plenary meeting, on 30 June 2023, … appointed Minhong Yi
         (Republic of Korea) as a member …
DISCARDED 77/408. Appointment of members of the ACABQ
         At its 34th plenary meeting, on 15 November 2022, …
         (a) Udo Fenchel (Germany), Olivio Fermín (Dominican Republic),
             Carlo Jacobucci (Italy), Ji Haojun (China), Ji-sun Jun (Republic of
             Korea) and Matsuda Yukiko (Japan) for a three-year term …
         (b) Stephani Scheer (United States of America) …
         (c) Surendra Kumar Adhana (India) …
```

The source itself labels the parts: the stored Vol III slice literally begins
`B1` / `B8` / `B9` — the part letter with its footnote marker — and the DL catalog
carries the part symbols for two of them (`A/DEC/60/502B`, `A/DEC/65/407B`), neither of
which the pipeline ever writes. These are multi-part decisions and the pipeline stores
exactly one part.

Three compounding defects sit behind the exception:

* **`write_children` picks the winner by ROW COUNT, not by content**
  (`fulltext_split_volumes.py:453` `if existing.get(child, 0) >= len(slice_rows)`).
  A printing fragmented into more rows by page breaks beats a longer one. For
  `A/DEC/77/408` the stored version is 9 rows / 1,361 chars and the discarded one is
  6 rows / 1,879 chars. `A/DEC/65/407`, `A/DEC/60/409`, `A/DEC/60/411` are the same
  shape.
* **The `elsewhere` probe queries the wrong table and the wrong thing.**
  `:222-224` selects from `digitallibrary.document_paragraphs` — the semantic layer,
  with no `source_symbol` filter — and asks only whether *some row exists*. It never
  compares the two printings, never checks that the stored one is the longer, and
  cannot distinguish "the sibling volume stored the other part" from "some unrelated
  document with that symbol was parsed".
* **Reverting this one exception reproduces the failures it was written to remove.**
  Exactly **10 volumes** (A/58 II & III, A/59 II & III, A/60 II & III, A/65 II,
  A/76 III, A/77 II, A/78 II) go from PASS to a round-trip leak without it, excusing
  **559 token types**. 89 − 10 = 79, against the "78/89" that preceded the change.

**Verdict: tuned-to-green, and the most damaging finding in this audit.** The
failures were true positives pointing at a real data defect.

---

## Part 2 — the volume gate's own structure

### 2.1 The coverage check does not measure what it is named for

`fulltext_verify_volumes.py:181-184, 239-251`. The docstring (`:12-13`) and
`docs/fulltexts.md:634-637` both say the union of the volume's **children** must
account for the ground truth. The code builds `accounted` from **every row of the
volume**:

```python
accounted: Counter = Counter()
for r in rows:                      # rows = read_volume_rows(...) = the WHOLE volume
    accounted.update(tokens(r["text"]))
```

The comment at `:174-180` makes the substitution explicit and calls the unrouted
remainder "allowed drops". The consequence is that **coverage is a comparison of the
PDF extractor against pymupdf on the same file** and is structurally blind to every
failure of the split. Numbers, all 89 volumes, from
`tmp/adv2/vol/instrument_gate.py` and `instrument2.py`:

| metric | median | min | volumes < 0.97 (the bar) |
|---|---:|---:|---:|
| coverage as shipped (types, all rows) | 0.999 | 0.977 (E/2010/99) | **0** |
| same, on token occurrences | 0.9995 | 0.9968 | 0 |
| coverage of the **routed slices** (what the docstring claims), types | 0.559 | 0.000 | **67** |
| same, occurrences | 0.891 | 0.000 | **62** |

Route census across the 89 volumes: `write` 3,687, `crosscheck` 11,
**`skip_existing` 0**, **`unmatched` 383**, from 4,081 detected headings. Because
`skip_existing` is zero, none of the routed/unrouted gap is explained by "the decision
already had its own full text".

**Negative controls.** The shipped metric *does* move when rows are removed from the
extractor's output, so it is not a `len(x)/len(x)` tautology:

| volume | baseline | −1 decision | −10 decisions | −all children | keep 50% rows | keep 10% |
|---|---:|---:|---:|---:|---:|---:|
| A/80/49(VOL.II) | 1.0000 | 0.8389 | 0.6197 | 0.2607 | 0.6979 | 0.3015 |
| E/2025/99 | 0.9850 | 0.8870 | 0.6875 | 0.6523 | 0.7201 | 0.2968 |
| A/HRC/8/52 | 1.0000 | 0.3972 | 0.2027 | 0.1410 | 0.7308 | 0.3162 |

It is sensitive to **raw-extraction** loss and completely insensitive to **routing**
loss, which is the failure mode it exists to catch. The real-world proof is in the
corpus already: `A/HRC/10/29` routes **zero** children and scores **1.000**.

### 2.2 Three volumes are graded against an EMPTY ground truth and score 1.000

`:251` `rep.coverage = 1.0 - (len(missing)/len(gt_types)) if gt_types else 1.0`.
When `decisions_section_tokens` finds no heading it returns an empty Counter, and the
volume passes with a perfect score having verified nothing. This is live:

* **`A/69/49(VOL.II)`** — pymupdf's `get_text("text")` puts the number and the title on
  separate lines (`'69/401. '`, then the title), so `_PDF_HEADING`'s
  `\.\s+[\"“‘(]?[A-Z]` never matches. 214 number-lines in the ground truth, **0**
  headings, gt_types = 0, gate says 1.000 PASS.
* **`A/HRC/S-11/2`** and **`A/HRC/S-4/2`** — gt_types = 0, 0 children, 1.000 PASS.

Silence is being read as success, on 3 of 89 volumes (3.4%).

### 2.3 `A/HRC/10/29`: a whole HRC session lost, gate says PASS

4,131 raw rows. 51 heading-pattern lines exist, all of them the table of contents. The
real body headings look like `'\t\t10/1\nQuestion of the realization…'` (style
`_ H_1_G`) — an embedded **newline** between number and title, so `_HRC_HEADING`'s
`(?=[\t ]…)` lookahead fails; `is_heading2` is also false because the style is
`_ H_1_G`, not `Heading 2`. Children written: **0**. All **51** catalog symbols
`A/HRC/{RES,DEC,PRST}/10/*` have **0 raw rows and 0 semantic rows**. Gate: `coverage=1.000
children=0 leaks=0` → **PASS**.

### 2.4 A live regex bug: `[\t\xa0]` instead of `[\t ]`

`fulltext_split_volumes.py:149` contains a **non-breaking space (U+00A0)** where a
space was meant:

```
_HRC_HEADING = re.compile(r"^\s*(PRST/|DEC/)?(S-\d+|\d+)/(\d+)\s*(?:\.|(?=[\t\xa0]|  +\S)(?=.*[A-Z]))")
```

So the "dot-less numbering (S-9/1)" branch advertised in `72d5101`'s commit message
matches a tab or an NBSP, never an ordinary space:

```
hrc_heading("S-9/1 The grave violations of human rights") -> None
hrc_heading("S-11/1 Assistance to Sri Lanka …")           -> None      # real line, style 'Heading 2'
hrc_heading("10/1 Question of the realization")           -> None
```

`A/HRC/S-11/2` row 24 is exactly that line and is why that report yields 0 children.
The module's `--self-test` does not catch it because it only tests the **dotted** form
(`"S-2/1. The grave situation"`). A check that has never been shown to fail.

### 2.5 The boundary-leak check cannot fire

`:253-259`. A slice is `rows[idx:end]` where `end` is the next member of `heading_idx`,
and `heading_idx` contains every heading-pattern line (`_NUM_HEADING_ANY` for GA/ECOSOC,
every confirmed heading for HRC). The next child's heading is by construction in
`heading_idx`, therefore never inside the slice. Empirically `leaks=0` on all 89
volumes. Also, `ordered = list(sp.children)` is sorted by **symbol**
(`fulltext_split_volumes.py:404 sorted(best_write.items())`), not by document order as
the comment claims, so "the next child" is not even the document-adjacent one.
**Absent, not passing.**

### 2.6 Allowed-drop line filters shrink the denominator by 10.35%

`_allowed_drop_line` (`:75-84`) and the front-matter cut (`:123`) are applied to the
**ground truth**, i.e. they remove content from the denominator — the shape LESSONS.md
calls out ("denominators come from the source, never from what processing produced").
Measured over all 89 volumes (`tmp/adv2/vol/alloweddrop.py`):

| category | lines | tokens |
|---|---:|---:|
| front matter (everything before the first heading OUR detector finds) | 78,351 | **539,466** |
| TOC dot-leader lines | 2,756 | 21,814 |
| running headers `_RUN_HEADER` | 2,096 | 40,765 |
| kept (the denominator) | 513,715 | 5,216,899 |
| **excluded from the denominator** | | **602,045 = 10.35%** |

Two of these are over-broad against real content:

* **`_RUN_HEADER`'s `^[AES]/\d` alternative** drops 173 body lines that are the printed
  **draft-sponsor records** of GA Vol III, e.g.
  `A/63/49(VOL.III)`: `'A/63/L.69 and Add.1, sponsored by: Albania, Algeria, Andorra, Antigua and Barbuda, Argentina, …'`
  and `'A/63/L.76, submitted by the President of the General Assembly'`. These are
  content, not running headers.
* **`(General Assembly|…)\b.*(session|Official Records)`** drops line-wrapped fragments
  of operative paragraphs, e.g. `A/58/49(VOL.III)`:
  `'General Assembly at its fifty-ninth session on the placement of'`,
  `'reviewed by the General Assembly at its sixty-first session with'`,
  `'on its work to the General Assembly at its fifty-ninth session;'`.
* **Front matter is the largest and most variable.** `E/2010/99` — the volume with the
  *lowest* reported coverage (0.977) — has **74.7% of its tokens** (65,801 of 88,032)
  classified as front matter because the first heading our detector accepts is at line
  7,517 of 10,809. Its coverage is computed over a quarter of the document.

### 2.7 The PASS bar

`DEFAULT_MIN_COVERAGE = 0.97` (`:58`). Observed minimum across all 89 volumes is
**0.9770** (E/2010/99) — 0.7 percentage points of headroom. On the metric as shipped
the bar is not the binding constraint (nothing comes near it, and my negative controls
show a single missing decision costs 5–60 points), so I have no evidence it was tuned.
On the metric the docstring *claims*, the bar is catastrophically breached: 62–67 of
89 volumes.

### 2.8 The gate's `--self-test` has no negative control

`:283-302` exercises `decisions_section_tokens` and `_allowed_drop_line` on a
5-line fixture. Nothing damages a real volume and asserts rejection; the coverage
formula, the round trip, the leak test and every exception above are untested. Per
CONTRIBUTING rule 2 they are **absent, not passing**.

### 2.9 A volume-gate failure prints `gates: pass`

`fulltext_nightly.py:309-310` records the volume stage through `record()`, which sets
`stage_failed`, while the `gates` line (`:322`) is driven by `gate_failed`, set only by
the text/pdf gates. The process still exits non-zero, but the human-readable summary
says `gates: pass` on a night when the volume gate failed.

---

## Part 3 — the honest numbers

**Corpus denominator taken from the source (the DL catalog), not from what parsing
produced.** In-scope children per `docs/fulltexts.md` (GA sessions 57–80, ECOSOC
2003–2025, HRC sessions 2–11 and S-2..S-11):

| family | in catalog | has stored text | **missing** |
|---|---:|---:|---:|
| `A/DEC/*` | 2,460 | 1,854 | **606** |
| `E/DEC/*` | 1,843 | 1,500 | **343** |
| early `A/HRC/{RES,DEC,PRST}/*` | 291 | 236 | **55** |
| **total** | **4,594** | **3,590** | **1,004 (21.9%)** |

None of the 1,004 is a bracket pseudo-symbol and none has raw text from any other
source: they simply have no full text. The gate reports 89/89 PASS.

The dominant mechanism is **unmatched headings** — 383 of them, spread over 61 of the
89 volumes. **630 of the 949 missing `A/DEC`+`E/DEC` symbols carry a trailing part
letter** (`…A`, `…B`, `…C`); only 319 are plain numbers. The volume prints a
single heading for a multi-part decision, the derived symbol is not in the catalog
(only the lettered parts are), so nothing is written, and the text is left sitting in
an unrouted volume row that the coverage formula counts as "accounted". Opened the
source to confirm (rule 5):

* `A/80/49(VOL.II)` position 514 prints
  `'80/516. Increase in the membership of the Committee on the Peaceful Uses of Outer Space'`
  as one heading. Catalog has `A/DEC/80/516A`, `B`, `C`, `D`. Written: none.
* `A/79/49(VOL.II)` position 146 prints `'79/407. Appointment of members of the ACABQ  At its 38th plenary meeting, on 13 November 2024, …'`.
  Catalog has `A/DEC/79/407A` and `A/DEC/79/407B`. Written: none.
* Same pattern for `80/408`, `80/526`, `58/419` and 380 others.

### Recomputed gate results

| gate configuration | passes | notes |
|---|---|---|
| as shipped today | 89/89 | the published number |
| revert change (2) (apparatus) | 89/89 | the exception excuses 0 tokens |
| revert change (3) (cross-volume existence) | **79/89** | 10 volumes fail on genuine loss of a decision part |
| revert change (1) (converted docx) | **run aborts** | `PackageNotFoundError` on the 47th volume; no number at all |
| coverage measured over the ROUTED slices (occurrences), bar 0.97 | **27/89** | 62 fail |
| coverage measured over the ROUTED slices (types), bar 0.97 | **22/89** | 67 fail |
| add: fail on `unmatched > 0` | ≤ 28/89 | 61 volumes have ≥1 unmatched heading |
| add: fail on empty ground truth | −3 | A/69/49(VOL.II), A/HRC/S-11/2, A/HRC/S-4/2 |
| add: fail on 0 children written | −3 | A/HRC/10/29, A/HRC/S-11/2, A/HRC/S-4/2 |

**The real defects behind the original 11 failures** were not gate bugs. Ten of them
were the cross-volume multi-part decisions of §Change (3) — a true positive naming a
real, still-present data loss. The eleventh is not reproducible from today's DB state
(the corpus has been re-split since); the two candidates are the `A/HRC/6/22`
Normal-styled heading and the `S-9/1` dot-less form, and the latter is **still broken**
(§2.4).

---

## Part 4 — corrections required, in damage order

1. **Remove change (3).** Replace the existence probe with: for a child produced by
   more than one volume, require that the stored version's text is a superset of every
   printing, or that each printing is written under its own part symbol
   (`A/DEC/<n>/<m>A|B|…`). Change `write_children`'s longest-wins from row count to
   character count in the same edit. Restores the 10 honest failures and the ~23.5k
   characters.
2. **Route multi-part decisions.** When the derived symbol is absent from the catalog
   but `<symbol>A`/`<symbol>B`/… exist, that is a part-decision heading — split it or
   write the whole slice under each part. This is the single biggest lever on the
   1,004 missing decisions.
3. **Make coverage measure the children.** `accounted` must be built from the routed
   slices, with each allowed-drop category counted and printed separately rather than
   folded into the same bag. Publish the honest number (median 0.891 on occurrences)
   instead of 0.979–1.000.
4. **Fail on `gt_types == 0`.** An empty ground truth is an extraction failure, never a
   pass.
5. **Fail on `children == 0`** for a volume in the catalog.
6. **Fix `[\t\xa0]` → `[\t ]`** at `fulltext_split_volumes.py:149` and add
   `hrc_heading("S-9/1 The grave violations")` to the self-test. Add the
   newline-separated form (`'10/1\nQuestion…'`) and re-split `A/HRC/10/29`.
7. **Remove change (2).** It excuses nothing and holds a 9,236-token blanket.
8. **Narrow `_RUN_HEADER`.** Anchor `^[AES]/\d` to lines that are *only* a symbol, and
   require the General-Assembly/session pattern to be a whole line, not a `.*` match
   inside body prose.
9. **Report front matter as a number, not a category.** A volume where 74.7% of tokens
   fall before the first detected heading (`E/2010/99`) should fail, not pass at 0.977.
10. **Give the gate a negative control suite** (rule 2): delete one child's rows,
    unroute one decision, blank a volume, corrupt one printing of a cross-volume
    decision — each must be rejected, and an undamaged run must stay quiet.

---

## Part 5 — the parser and the display layer (`fulltext_parse.py`, website render)

Audited independently; every figure below was produced by querying the live corpus, and
I re-ran the load-bearing ones myself.

| # | Exception | where | claim it rests on | evidence | worst case it hides | hiding now | verdict |
|---|---|---|---|---|---|---|---|
| P1 | dotted-enumerator **sequence guard** (chain ≥3, numeric `.1` head) | `fulltext_parse.py:625-648` | "assessment tables and lone decimals fail to chain" | 845 raw rows still start with a dotted enumerator; only 21 pass the lexical screen and are then refused, and all 21 are assessment-scale rows or OCR debris (`A/RES/1223(XII)@142 '13.62 United Kingdom of Great Britain and'`, `A/RES/830(IX)@5 '5.11 th plenary meeting,'`). **0 genuine lists refused** | a real 1–2 item list | 0 | **justified** |
| P2 | same guard's **reach**: `detect_dotted_enum` only scans `lr.kind=='paragraph'` | `:632` | — (undeclared) | Replaying the rule at raw level finds 11 documents with a confirmed chain; the parser promoted 6. `A/RES/71/313` and `E/RES/2017/7` (the SDG Global Indicator Framework, 169 targets each) are missed because their rows are `kind='table_cell'` | the SDG indicator framework | **871 elements in 5 docs**; in `A/RES/71/313` the 169 targets collapse into ONE 9,815-word `type='table'` element, which the site hides | **unjustified** |
| P3 | lexical screen (`major<1`, `len(rest)<15`, dot-leader, alpha≥0.55) | `:580-598` | never promotes assessment scales | 675 rejected by `major<1` (all `0.xx` scale rows), 96 dot-leader, 28 short, 2 alpha — all true negatives | a short genuine target | 0 | **justified** |
| P4 | `_marker_section_heading` verb guard | `:1592-1607` | "a bold `I. Decides …` operative is not swallowed" | Exactly 4 bold long marker-headings corpus-wide are blocked, and all 4 are genuine operatives of lettered consolidated resolutions (`A/RES/39/77@45`, `A/RES/40/137@55,@65`, `A/RES/40/243@42`) | a section titled "Further measures…" | 0 | **justified** |
| P5 | 80-char cap survives for non-bold, non-styled rows | `:1626` | sem-v4 lifted the cap only for bold/styled lines | 421 long `X. Title`-shaped rows exist; 197 became body paragraphs and 16 frontmatter. WordPerfect/PDF sources carry no bold and one 'Normal' style, so **no long heading in a WP document can ever be recognised**. ~19 unambiguous section headings demoted, e.g. `A/RES/50/227@58 'I. FUNDING OF OPERATIONAL ACTIVITIES…'`, `A/HRC/RES/7/29@16,@29,@92` | every top-level section of a WP-era outcome document | ~19 headings in ~10 docs; `A/HRC/DEC/6/102`'s 6 headings became `frontmatter` = invisible | **unjustified (format-blind)** |
| P6 | `_bold_heading` `len≤60 and words≤8` | `:322` | short title fragments only | no negative control; 419 bold, 61–80-char, NULL-type body paragraphs sit just outside the window | a 65-char bold subheading | unmeasured | **tuned-to-green** |
| P7 | `state == "front"` skips all heading detection | `:304, :1617` | "titles/masthead live there" | 11,705 raw rows carrying an explicit heading style (`H23G`, `HChG`, `Heading1`) across 11,295 docs were emitted as `frontmatter`. Blanket rule: `E/DEC/2009/204@34 'B. Operational activities … segment'` is bold, long, marker-matched, verb-clean, still `frontmatter` | any heading before the opening formula | 11,705 rows demoted | **unjustified** |
| P8 | **front-region residue → `type='frontmatter'`** | `:1376-1387` | "session/agenda/masthead lines" | The largest defect found. 5,584 docs never emit an `opening`, so the state machine never leaves `front` and the whole body is stamped frontmatter. Verbatim: `E/DEC/2021/247@2 '(a) Decided to grant consultative status to the following 431 non-governmental organizations:'` (I re-pulled this row myself — confirmed), `A/RES/177(II)` (the Nuremberg Principles, 113 words, all invisible), `A/RES/1760(XVII)@1 'Recalling its resolution 1514 (XV)…'`. 7,423 non-masthead frontmatter rows are body-shaped | a whole GA/ECOSOC decision | reconciled exactly against the live DB with the site's own `HIDDEN_TYPES`: **221 documents have no renderable element at all**, and **3,116 documents render nothing but their `title`** (no body element survives) — i.e. 12.1% of the 25,734 parsed corpus. `frontmatter` alone holds **334,927 words** | **unjustified** |
| P9 | website hides all `frontmatter` client-side, beyond the SQL masthead filter | `mandates/website/src/lib/data/paragraphs.ts:168` (SQL drops only `subtype='masthead'`) + `components/ParagraphsSection.tsx:577-586` | "pure document chrome" | The SQL comment materially misdescribes behaviour: rows are fetched over the wire and then discarded. I confirmed `HIDDEN_TYPES = {frontmatter, backmatter, footnote, footer, signature, vote_record, divider, table}` | see P8 | **748,265 words corpus-wide sit in hidden types** (my count): frontmatter 334,927, footnote 274,051, table 103,896, signature 28,730, vote_record 5,015, divider 1,646 | **unjustified** |
| P10 | `table` hidden + parser collapses a table into one element | `:1096-1130`, `HIDDEN_TYPES` | "deferred future work" | Only 509 `table` elements exist but they carry **103,896 words** (204 avg; agent counted 457,073 including sub-rows). `S/PRST/2018/18@373` is a **single element** holding the Aide Memoire on protection of civilians, ~90% of that document, invisible | an entire annexed instrument | 509 elements / 103,896 words | **unjustified** |
| P11 | `footnote` hidden | `HIDDEN_TYPES` | chrome | 102,723 elements / **274,051 words**. UN footnotes carry the citation chain (`'44 A/78/L.89.'`) | the citation graph | 274,051 words | **unjustified for a document-analysis product** |
| P12 | masthead heuristic `len(cells)<=12` | `:1105-1106` | letterhead only | 476 masthead elements exceed 25 words; `S/RES/714(1991)@0` (62 w) is real preambular text. Dropped in **SQL**, so never reaches the client | preambular text of an SC resolution | ~476 elements | **unjustified** |
| P13 | `dropped[]` reasons `empty` / `section_break` / `page_artifact` / `table_cell` | `:1070-1109, :1379` | artifacts | Total checks, not samples: `empty` 229,205/229,205 blank; `section_break` 12,837/12,837 blank; `page_artifact` 2,055 rows all bare symbols/page numbers; `table_cell` 1,785 all whitespace-only | — | 0 | **justified** |
| P14 | `dropped[]` reason `layout_cell` | `:1107-1109` | drops all-empty cell groups | Unreachable: step 1 already drops every empty `table_cell`. **0 occurrences in 25,734 docs** | — | 0 | **dead — a documented reason that has never fired** |
| P15 | `RESCUE_INFERRED_OPERATIVE` | `:94-104` | opt-in, auditable | Fires 1,771×/1,089 docs, flagged in `inferred_operative`; adds rather than hides | over-labelling | n/a | **justified (audit trail present)** |
| P16 | `OP_PAREN_RE` widened `{1,5}`→`{1,7}` | `:179` | rescues `(xxxviii)` | 133 elements now carry 6–7-char paren markers; 0 rows remain with an 8+-char paren marker | `(xxxviii)` lost | 0 | **justified** |
| P17 | `STYLE_HEADING_FP_RE` front-matter patterns | `:243-250` | ported from the TOC verifier | The `(seventy|sixty|…|first)-?` alternative is unanchored at its tail, so any heading beginning "First…"/"Third…" is excluded. No negative control | a heading beginning "Third…" | not observed | **tuned-to-green** |
| P18 | `document_parses.issues[]` accounting invariant | `:1490, :1801` | "0 accounting failures; preservation 100%" | The instrument measures something adjacent: it asserts every raw *position* is consumed by *an* element, which stays true when the element is `frontmatter` or a 100k-word `table`. `issues[]` is empty for all 25,734 docs | everything in P7–P11 | 100% green while ≥221 docs render nothing | **tuned-to-green** |

## Part 6 — fetchers, verifiers, rounding

| # | Exception | where | claim | evidence | worst case | hiding now | verdict |
|---|---|---|---|---|---|---|---|
| F1 | `A/DEC` + `E/DEC` excluded as "structurally absent" | `fulltext_fetch.py:97, 599-628` | 0/85 hand sample | Re-tested on a fresh random sample of 12 spanning 1994–2025, `t=doc` and `t=pdf`: 12/12 return the 1,303-byte HTML error page, while control `A/RES/48/263` in the same session returned a 114 KB WordPerfect file (so the probe can hit). DL records 0 files for all 9,713 A/DEC+E/DEC records vs 70% file coverage corpus-wide | — | no | **justified** |
| F2 | the same regex also drops `A/HRC/RES\|PRST` sessions 1–11, under the A/DEC label | `fulltext_fetch.py:97` `DECISION_FAMILY_RE`, printed at `:628` | commit 57b183a says "**speed up** backfill"; `docs/fulltexts.md:70-73` says "HRC is *not* excluded" | 179 catalog symbols excluded under a label naming a different family and a different reason; 144 later recovered by the volume split, **35 have no ledger row at all** — never fetched, never marked unavailable, invisible to `--recheck-unavailable` | 35 HRC texts 2006–09 | **yes, 35** | **unjustified (mislabelled)** |
| F3 | "pre-1994 = PDF only, no Word source" | `fulltext_fetch.py:94` `MIN_DATE`, `fulltext_fetch_pdf.py:2-7,83` | commit ac530c9 | **Falsified at the boundary.** ODS `t=doc` returns real WordPerfect today for `A/RES/48/12` (64,933 B), `A/RES/48/4` (52,962 B), `A/RES/48/30` (67,878 B), `A/RES/48/14` (59,398 B), `S/RES/872(1993)` (78,982 B) — all five stored as `format='pdf'`. The deeper era does hold (1985–91: 6/6 HTML). The era is patchy inside 1993, not clean: `S/RES/883(1993)` has no Word, `S/RES/872(1993)` does | 221 symbols in Sept–Dec 1993 routed to the PDF/OCR path | **yes: 13 with no text at all, 62 needlessly OCR-sourced** | **unjustified** |
| F4 | born-digital volume cutoff (GA ≥57, ECOSOC ≥2003) | `fulltext_split_volumes.py:73-76` | STEP-0 probe sweep | Not re-probed (HTTP budget). Its self-test is **circular**: `:765` asserts `"E/2003/99" in ecosoc_volume_symbols()` — the constant against itself; `:763` is `check("A/56/49(VOL.II)" not in ga_volume_symbols() or True, "")`, an expression that can never be false, with an empty failure message | ~5,200 decisions in pre-2003 volumes | unknown — never measured | **unverified; its self-test is absent** |
| F5 | bracket pseudo-symbols dropped, "the parent's file covers it" | `fulltext_fetch.py:37-45, 308-335` | docstring | Mixed. 3,517 bracket symbols, 0 with a ledger row. Title-token containment against the parent's stored text on 60 samples: 50/51 at ≥80%, none below 50% — claim holds where the parent has text. But `dedupe_brackets` drops against `load_already_done()`, which includes `unavailable`/`no_text_layer`, so **799 brackets have a parent with no text at all** (526 `no_text_layer`, 273 `unavailable`) and are dropped silently while being counted as "collapsed onto parent" | 799 symbols, e.g. `A/RES/1252(XIII)[A-D]` | **yes, 799** | **unjustified for 799 of 3,517** |
| F6 | `CIRCUIT_THRESHOLD = 25`, "consecutive misses are absence clusters" | `fulltext_fetch.py:113, 701-732`; `fulltext_fetch_pdf.py:90, 429-454` | commits be9511a, dff7f10 | `consecutive_block` increments only on 429/403/reset and resets only on `fetched`. A 200-with-HTML outage is sniffed `html` → written **`status='unavailable'`, permanently**; the breaker never trips and `SOFT_BLOCK_SLEEPS = ()` means no in-run retry. `docs/fulltexts.md:106-113` documents this exact ODS behaviour as *observed*, while the module docstring claims "no rate limiting has ever been observed from ODS" | an outage-length run of documents marked permanently absent | 4,844 `unavailable` rows exist; 1,135 written in a single hour on 2026-07-20 | **unjustified** |
| F7 | "`--recheck-unavailable` is the second chance" | `fulltext_fetch.py:256-286`; `fulltext_nightly.py:214` | argparse help: "**or** whose ledger row was (re)touched within N days" | The SQL has no such clause — it filters on `date_publication >= now()-45` only. **3 of 4,844** `unavailable` rows are reachable by the nightly; 4,841 are never retried. A manual `--fallback-recent 12000` rescued 108/1,258 (8.6%) | 4,841 permanently-absent rows | **yes, 4,841** | **unjustified (help text describes code that does not exist)** |
| F8 | `verify_text --max-loss 0` + nuisance classes | `fulltext_verify_text.py:203-210, 254` | "never content" | **Negative controls PASS**: 90% element deletion → 6/6 FAIL rc=1; single-paragraph deletion → 25/25 caught. Over 400 real docx / 735,845 GT tokens the exclusions excuse **911 occurrences = 0.124%**, 0 genuine losses | Documented blind spot: the exclusions cover 39.4% of GT tokens on an otherwise-healthy parse (17.8% ≤2-char, 15.5% run-join-decomposable, 6.1% bare numbers) — **every numeric value in the corpus is outside this gate** | no live loss | **justified, with the blind spot recorded** |
| F9 | `--ignore-symbols` for 3 docs; "99.998%, 760/763 clean" | `fulltext_verify_text.py:42-47`; `docs/fulltexts.md:247-254` | — | Denominator is stale: 763 docs quoted against a corpus of 15,377 Word documents = **5% coverage**. The nightly passes neither flag, so defaults apply | a headline figure computed on 5% of the corpus | yes (the published figure) | **stale, not tuned** |
| F10 | `verify_pdf`: pass on `n_lost <= 5` **OR** `preserved >= 95` (85 for `poor`) | `fulltext_verify_pdf.py:259-265, 305` | "a handful of OCR letter-substitutions" | **Negative control FAILS.** Deleting the single longest paragraph from 20 real parsed PDFs: **10/20 still PASS**. `A/RES/41/98` lost 79 tokens → 95.86% → PASS; `A/RES/33/180` lost 56 → 99.05% → PASS; `A/RES/42/33` lost 129 words → PASS. Because the two conditions are OR'd, an 8-token region may lose 5 (62.5%) and pass | 5% of any document, unconditionally | **10,357 parsed PDF documents — 40% of the corpus — sit behind this bar** | **tuned to green** |
| F11 | `verify_pdf` region anchoring — denominator from the parse's own first/last elements | `fulltext_verify_pdf.py:135-184` | "crop-loss is expected, not failure" | **Negative control fails catastrophically, and I confirmed the mechanism by reading `region_ground_truth`: the anchors are `doc['elements']`, so deleting the parse deletes the ground truth.** Regions shrink with the damage: 401→8, 554→25, 837→61, 1616→48, 435→40. Five 90%-deleted documents run together print `pass=5 FAIL=0`, `aggregate in-region preserved=99.451%`, four of them `preserved=100.00%`, **exit code 0** | an entire document | **yes — the gate cannot see the defect it exists to catch** | **unjustified (Rule 3)** |
| F12 | `verify_pdf` French-line filter (≥3 French stopwords ⇒ drop the line from ground truth) | `fulltext_verify_pdf.py:68-83` | facing-language columns | No self-test, no negative control, never measured against English text. It removes ground-truth lines *before* the loss test, so a false positive deletes the evidence rather than the finding | English lines containing ≥3 of `le la des et sur par section general council` | unmeasured | **absent (untested)** — see also Part 7 |
| F13 | `verify_display --threshold 60`, exit only on the audit set, `pct = 100.0` when `total == 0` | `fulltext_verify_display.py:163, 179, 258` | "gate CI on the docs that matter" | Recorded run flags 1,665 of 15,675 docs (10.6%) and exits **0**, because `return 1 if flagged_audit else 0` counts only the 100-doc audit set. And the zero-denominator branch scores a document with no visible-eligible element as **100.0% visible**; corpus-wide **4,356 documents have zero `heading`/`operative`/`preambular` elements**, i.e. a zero denominator | a document whose entire content is `frontmatter` scores perfect | **yes** | **unjustified** |
| F14 | `audit_invariants` exit code | `fulltext_audit_invariants.py:466` | "tripwires" | `return 0` unconditionally. Silently restricts itself to the audit set when `audit_set.json` is on disk. Recorded run: 45 findings over 100 docs, 31 high severity, exit 0 | any structural defect | 31 high-severity findings | **absent as a gate** |
| F15 | self-tests | all five verifiers | doctrine rule 2 | `verify_text`, `verify_pdf`, `verify_toc`, `verify_display`, `audit_invariants` have **no `--self-test` at all**. The four that do (`nightly`, `split_volumes`, `verify_volumes`, `extract_pdf`) test pure predicates only — none damages a real document | — | — | **absent** |
| F16 | piped checkers | — | doctrine ("exit codes lie in shells") | **Clean.** No `\| tail`, `\| head`, `2>&1 \|` or `tee` in `python/`, `docs/` or `.github/`; every stage runs through `subprocess.run` and the return code is read directly | — | — | **ok** |
| F17 | "the nightly fails the workflow and emails" | `.github/workflows/nightly-sync.yml:39-43`; `docs/fulltexts.md:311-369` | CI enforcement | `git branch --contains 8085d9a` → **`feature/fulltexts` only**; `main`'s workflow contains zero fulltext steps. Scheduled workflows run from the default branch, so **these gates have never run in CI**. Corroborated by `harvest_state.fulltext_fetch`, whose last entry is `mode: symbols-file, 2026-07-22` — a local run | every gate described as CI-enforced | **yes** | **a guard nobody runs** |
| R1 | `verify_text` aggregate `:.4f` | `:317` | — | Over 26,743,031 Word-corpus tokens, **13.4 tokens** can be lost and it still prints `aggregate preserved=100.0000%`. Per-doc `:.3f` is safe (0.009 tokens) | a short sentence, corpus-wide | borderline | **acceptable** |
| R2 | `verify_pdf` aggregate `:.3f` | `:323` | — | Over 5,109,980 PDF tokens, **25.5 tokens** lost still prints `100.000%`; combined with F11 a 90%-deleted document prints `preserved=100.00%` per-doc | whole documents | **yes, via F11** | **unjustified** |
| R3 | `verify_display` `:.1f` / `{v:5.1f}` + `total==0 → 100.0` | `:173, 216, 219` | — | A 3,276-token document may hide 1.6 tokens and print `100.0`; a fully-hidden document prints `100.0` | see F13 | yes | **unjustified** |
| R4 | `fulltext_review.py:306` `round(..., 1) if total else 100.0` | — | — | The same zero-denominator → 100.0 pattern | documents that parsed to nothing | not measured | **same defect, unfixed** |
| R5 | `audit_invariants` `{frac*100:.0f}%` | `:225` | — | 50.4% prints "50%", but the `frac > 0.50` test uses the unrounded value | — | no | **ok** |

**Docs-vs-code drift found while checking claims** (`docs/fulltexts.md:106-164`): documented
soft retries at 30 s/120 s (code: `SOFT_BLOCK_SLEEPS = ()`), rest breaks every ~150
requests (code: 500), a circuit breaker at 8 that exits on the 3rd trip (code: 25, never
exits), ~3 s pacing (code: 1.5 s). Each of these is a published claim about a safety
mechanism that does not exist as described.

## Part 7 — the end-to-end number the gates never compute

The volume gate says 89/89. The parser's accounting invariant says 0 issues. The
display gate exits 0. Composing the three audits against a **source-side denominator**:

| stage | count | of 4,594 in-scope catalog decisions |
|---|---:|---:|
| in the DL catalog (GA 57–80, ECOSOC 2003–25, HRC 2–11 + S-2..S-11) | 4,594 | 100% |
| child raw rows written by the split | 3,590 | 78.1% |
| … of which render **nothing but their title** on the site (P8/P9) | 2,883 | — |
| **decisions a user can actually read** | **707** | **15.4%** |

80.3% of everything the volume-split pipeline produced is invisible in the product,
and no gate in the repository reports that number. (The renderer consults `mandates.paragraphs` first
and falls back to the DL arm (`paragraphs.ts:189-196`); I checked that arm — it holds
197,369 rows and **0** `A/DEC`/`E/DEC` symbols, so every split child is served by the DL
arm and the figure is exact, not an assumption.) `fulltext_verify_display.py` is the
one instrument that could — and it exits 0 while flagging 1,665 documents, because its
exit code counts only a 100-document audit set (F13).


### Addendum: `fulltext_verify_toc.py`

Its exit code is honest (`return 1 if (tot_missing or tot_mis or errs) else 0`,
`:694`), but **nothing in the repository invokes it** — `grep -rn verify_toc python/
docs/ .github/` matches only the untracked `python/fulltext_negative_controls.py`
written today by a concurrent session. Its `_FRONTMATTER_TEXT` skip list (`:146-153`)
carries the same unanchored `seventy-|sixty-|fifty-|forty-` alternative that the parser
inherited (P17): any heading whose text begins "Forty-…" is skipped regardless of style.

## Part 8 — the PDF extractor (`fulltext_extract_pdf.py`)

The two *named* targets (French filter, `no_text_layer`) come out largely justified. The
rest of the file does not. **Token conservation over a 500-document random sample,
879,515 source tokens: 53.60% emitted**; crop-before 16.23%, crop-after 27.00%,
header/footer 0.72%, French 0.80%, unaccounted residual 3.31% (dominated by hyphen-join
fragments — `ment`, `dis`, `con`, `tion`, `disarma`).

| # | exception | file:line | claim it rests on | evidence | worst case it can hide | hiding any RIGHT NOW | verdict |
|---|---|---|---|---|---|---|---|
| X1 | **French facing-language drop** (`french_line`: ≥4 tokens, ≥3 of 68 stopwords), applied to body, footnotes **and** the verify ground truth | `:102-120`, `:939-942` | "French FUNCTION words that essentially never occur in English UN prose"; "an English line quoting one French name is never dropped" | 15 of the 68 words are English (`examine, informer, presenter, la, les, sur, par, pour, sans, dont, elle, nous, seance, generale, adoptee`). Census over all 7,072 `pdf-v1` documents: 14,213 lines fire in **537** documents, which split cleanly by the fraction of lines hit — **498 are genuine bilingual supplement volumes** (2–23% of lines, 13,210 lines, correct) and **39 are ordinary English documents** (<2%, 1,003 lines). Adversarial control on 579,835 guaranteed-English Word paragraphs: 55 FPs / 2,806,523 wrapped lines (0.0020%), all English sentences naming French-titled armed groups (`S/RES/2717(2023)`, `S/RES/2031(2011)`, `S/PRST/2013/5`) | any English line naming a French-titled entity — sanctioned armed groups, NGOs, treaty titles | **YES — 1,003 lines / 10,317 tokens in 39 documents; 129/129 spot-checked lines confirmed absent from `document_paragraphs_raw`.** All are official names of NGOs in consultative status: E/2024/99 (89), E/2023/99 (87), E/2022/99 (79), E/2019/99 (69), A/78/49(VOL.III) (41)… Verbatim, **A/58/49(VOL.II) p.17**, the line `Association pour le Déploiement Rural, la Protection de` is deleted while its continuation `l'Environnement et l'Artisanat (DERPREA – Cameroon)` survives orphaned — in `pdf-v1` **and** in the split child `A/DEC/58/509` | **justified in scope, unjustified in application** — no per-document gate (the ≥2%-of-lines signal separates the two populations perfectly), and only a count is kept |
| X2 | the same filter strips the **verify ground truth** | `:95-96`, `fulltext_verify_pdf.py:68-83` | "so the gate is not flooded with expected-loss French words" | Structural: a false positive removes the evidence as well as the content, so the gate can never report a French-filter loss. This is why `E/2025/99`'s French residue surfaces only in the **volume** gate, which does not apply the filter | a filter defect invisible to the gate that would detect it | yes, by construction | **unjustified (self-excusing)** |
| X3 | `french_dropped` counted, **never persisted** | `:907, 942, 1101-1102, 1158-1159` | — | Printed at run time only; nothing writes it to `document_files`, `document_parses` or row `props`. There is no way to ask after the fact how much text the filter removed | unbounded, unauditable | the corpus-wide figure does not exist | **unjustified — a drop with no ledger** |
| X4 | **`no_text_layer`** triage `chars_per_page < 80 or n_tok < 20` | `:185-186`, acted on `:914-916` | "a pure image scan, no usable text; this is the coverage loss" | Denominator is `doc.page_count` from the file — **no Rule-3 defect**. Total check, not a sample: all **5,009** rejects re-opened — 2,305 have 0 chars; 2,701 have 1–499 chars of which 2,698 have **zero** `[A-Za-z]{2,}` tokens (`A/57/49(VOL.III)`: 108 pages, 107 chars, all newlines). All **7,072** accepts re-triaged: min cpp = 354, **4.4× the threshold**; buckets 80–159 and 160–319 are **empty**. Nothing sits within 4× on either side | a short genuine resolution, or a document whose text pages are averaged down by blank scan pages | **no English content is being written off** | **justified** |
| X5 | …but the instrument measures Latin-script volume, not text-layer presence | same | — | The 3 rejects with real characters are **Russian** (`A/RES/2532(XXIV)` 4,716 chars / 16 tokens; `A/RES/35/134` 4,679 / 1; `S/RES/2577(2021)` — which is not even that document, its text is E/2021/L.22 in Russian). The same gate **admits** Russian just above the token bar: `A/RES/3446(XXX)` (29 tok) and `A/RES/981(X)` (34 tok) are stored as English. 10 `pdf-v1` documents hold Cyrillic runs, ≈252 rows / 32,858 chars | mis-fetched non-English documents labelled "needs OCR" instead of "needs re-fetch", and others admitted as English | **yes, both directions** | **adjacent measurement** — cheap fix: a script check before the token check |
| X6 | **`crop_to_target`** — "if the anchor cannot be found, keep everything and flag it — **NEVER silently truncate**" | `:765-826`, docstring `:30-31` | no silent truncation | **The claim is false whenever the anchor is *found* but the END is wrong.** Crop removes **43.2%** of all source tokens; 87 of 500 sampled documents lose ≥80%. `ADOPTED_RE = ^\[?\s*Adopted\b` with `re.I` ends the crop on any paragraph beginning with lowercase "adopted": **A/RES/46/68** (American Samoa, Anguilla, Bermuda, BVI, Cayman, Guam, Montserrat, Tokelau, Turks & Caicos, USVI — parts A–P) ends at preambular para 7 → **12 rows / 1,084 chars from 5,477 source tokens (2.7%)**, `anchor_found=True`, `flags=[]`. Source-anchored total check: 178 of 7,072 stored documents contain no organ opening formula anywhere, and **145 of those have one in the source PDF** | entire resolutions | **YES — 145 documents, 201,698 of 963,771 source characters retained (20.9%), every one unflagged and `status='parsed'`.** I re-pulled six from the DB myself: **`A/RES/701(VII)` (Korea / UNKRA) = 1 row, 47 chars, `'701 (VII). Korea: reports of the United Nations'` — the title line only**; **`A/RES/1016(XI)` (race conflict in South Africa / apartheid) = 1 row, 127 chars**; `A/RES/357(IV)` 35 chars; `A/RES/2396(XXIII)` 103 chars; `A/RES/32/1` 50 chars; `A/RES/2396(XXIII)` 0.6% of source. This is a **floor** — A/RES/46/68 keeps its opening formula and is therefore *not* in the 145 | **unjustified as documented** — the ledger's only truncation signal (`crop_anchor_not_found_*`) cannot fire in exactly the cases that lose the most |
| X7 | header / footer / page-number stripping — "body lines are never touched here" | `:278-332` | the parser owns body classification | 694-document sample: 2,242 drops (PAGE_NUM 1,232 / RUN_HEADER 652 / REPETITION 214 / PAGE_LABEL 47 / DOC_SYMBOL 43 / RULE 54). `_PAGE_NUM` matches a bare `"N."`, so a hanging operative marker at a column top is deleted: **202 cases in 149 of 694 documents (21.5%)**, each a bare `N.` at a column left edge whose next line starts with an operative verb — `A/RES/37/18` `'1.'` + "Condemns Israel's refusal to implement resolution"; `S/RES/880(1993)` `'12.'` + "Further decides to establish a team of twenty military liaison"; `A/RES/42/185` `'2.'` + "Adopts the Environmental Perspective to the Year". REPETITION has **no content test** and `_RUN_HEADER` uses `.search`, not `.match` | whole preambular clauses; operative numbering | **YES — ≈2,060 deleted operative markers in ≈1,500 documents** (scaled from 202/694), plus REPETITION deleting prose (32 lines in 13 of 694), e.g. five consecutive preambular lines of **A/RES/35/227** on Namibia ("Recalling the Declaration on Namibia and Pro-", "Mindful that, by assuming direct responsibility for", …) | **unjustified** — no geometric test separates a page number at the page edge from a marker at a column margin |
| X8 | hyphenation "repair" in `_join_lines` — "**conservative** end-of-line hyphenation" | `:599-612` | conservatism asserted | The rule deletes the hyphen whenever the next line starts lowercase — exactly what a genuine compound broken at a line end looks like. Causation proven against the source: `A/RES/2649(XXV)` prints "self-" / "determination" and stores **`selfdetermination`**. Over 33 probes whose concatenation is not an English word: **1,446 lost hyphens in 440 documents against 16,362 correct joins = 8.12% loss** | legal terms of art | **YES.** My own count on the live table: **`selfdetermination` 136 rows in 113 documents**; also worldwide 337, midterm 184, policymaking 167, highlevel 120, followup 57, wellbeing 56, decisionmaking 54, longterm 53, nonnuclear 46, ceasefire 37, nonproliferation 13, nonaligned 8 — from 33 probes only, so the true total is larger | **tuned-to-green wording** — "conservative" was never measured; the honest number is 8.12% |
| X9 | `_merge_hanging_markers` + `_repair_ocr_markers` — "reconstructing `N. <verb>` for the parser's lexical path" | `:527-562`, `:689-715` | reconstruction | 431 stored paragraphs match `^N. <lowercase>` across 233 documents. Automated source verification (is the marker present in the 30 characters preceding the text in the raw PDF?): **263 fabricated**, 151 genuine, 17 unverifiable | invented operative-paragraph numbers on Chapter VII resolutions | **YES — 263 invented numbers.** I confirmed the flagship case in the DB: **`S/RES/661(1990)` position 16 stores `'19. nationals or in their territories which promote or are calculated to promote such sale or supply of such commodities or products;'`** — a mid-sentence continuation of operative 3(c) in a resolution that has 9 operative paragraphs. Also `S/RES/674(1990)` [31] `'26. which the Council will need to take further measures under the Charter;'` (tail of operative 10); `A/RES/47/210B` [54] `'15. the Czech Republic, Eritrea, …'`. Some markers trace to page numbers beside a running header | **unjustified — this is fabrication, not a gap.** "A missing value is a gap; an invented one is a false statement", written into a legal corpus |
| X10 | small-font ⇒ `kind='footnote'` (`size < body_size − 1.5`) | `:931-933`, `:978-997` | "bottom-of-column apparatus" | Content is retained but escapes the crop and is appended out of order. 27,053 footnote rows / 2,349,869 chars; 2,726 exceed 200 chars | body text reclassified as apparatus (and then hidden by the site, cf. P11) | **YES, 18 documents** — `A/RES/1768(XVII)` and `A/RES/1785(XVII)` store operative paragraphs 3, 4 and 6 of the declaration on permanent sovereignty over natural resources as footnotes; also `A/RES/3314(XXIX)` (Definition of Aggression), `A/RES/36/103`, `A/RES/44/33` | **justified in principle, mis-tuned** — one global median size for the whole document |
| X11 | `split_columns` single-column fallback (`n<12`; gutter `cov ≤ 0.06·max`) | `:355-388` | "distinguishes a real two-column page from a hanging-number layout" | Counter-example: `A/RES/32/1`, 1 page, 55 paragraphs, returns **1** group → the columns interleave → the neighbour heading `32/2.` lands immediately after the target → crop `[39,40)` → **2 rows / 50 chars**, `anchor_found=True`, `flags=[]`. Same signature in `A/RES/1948(XVIII)` | entire resolution | **YES** — feeds the 145 above | **unjustified** — silent, with no "≥N paragraphs kept" sanity check |
| X12 | `repair_opening` (SequenceMatcher ≥0.86, ≤46 chars) | `:446-462` | "body prose is far too long to match" | 20 adversarial near-misses tested; only genuinely garbled openings rewritten. "The General Committee,", "The Special Committee,", "The Trusteeship Committee,", "The Human Rights Committee,", "The General Assembly requests," all survive unchanged | renaming one organ as another | **no** | **justified** |
| X13 | `repair_lead_verb` (edit-distance 1, junk letter `xvzjq`, unique) | `:497-524` | "never touches anything past the first word" | Brute-forced all 235k words in `/usr/share/dict/words`: exactly **4** would be rewritten (`shaving→sharing`, `taxing→taking`, + 2 nonce). Live: 8 firings in 500 documents, all correct | a paragraph opening "Taxing…" becoming "Taking…" | **no (0 observed)** | **justified** — but repairs go to stdout only, never persisted, so a wrong repair is unauditable |
| X14 | `--self-test` "all 4 adversarial cases passed" | `:1180-1235` | implies the file's conservatism is verified | Three of the four cases test the marker repair; one tests the lead-verb repair. There is **no negative control** for the French filter, the crop, the header stripper, the hyphen join or the column split — i.e. for every mechanism above that removes or invents content | everything in X1, X6–X9, X11 | — | **tuned-to-green** — a check that cannot fail on the defects that exist is absent, not passing |
| X15 | `--force` reclassification to `no_text_layer` | `:1090-1096` | — | Sets the status without deleting existing raw rows | stale rows outliving their ledger status | **no** (cross-tab: all 5,009 have 0 rows) | **latent** |

Minor: `_FR_TOKEN = [a-zà-ÿ']+` spans U+00E0–U+00FF, which includes `÷` (U+00F7). Cosmetic.

---

## Part 9 — what must be removed or tightened, and the honest numbers that follow

### Remove outright (each is unjustified and currently costing content or sensitivity)

| # | remove / replace | why | what it restores |
|---|---|---|---|
| 1 | `verify_volumes` cross-volume **existence** exception (`:210-232`) | claim falsified: the two printings are different GA actions | 10 honest volume failures; forces the fix that recovers 23,378 characters |
| 2 | `write_children` longest-wins **by row count** (`split_volumes.py:453`) | fragmentation beats content | stops storing the shorter printing for ≥5 symbols |
| 3 | `verify_volumes` **apparatus** exception (`:194-209`) | justification does not describe the code it guards; excuses 0 today | 9,236 token types of round-trip sensitivity |
| 4 | `verify_volumes` `if gt_types else 1.0` (`:251`) | an empty ground truth is a failure, not a pass | 3 volumes stop being silently unverified |
| 5 | `verify_pdf` **region anchoring** on the parse's own elements (`:135-184`) | denominator derived from processing; 90%-deleted docs print `100.00%` | the only gate covering 10,357 PDF documents starts working |
| 6 | `verify_pdf` `n_lost <= 5 **or** preserved >= 95` (`:305`) | OR lets 5 tokens through unconditionally; 10/20 single-paragraph deletions pass | a real bar for 40% of the corpus |
| 7 | `verify_display` `pct = 100.0 if total == 0` (`:163`) and audit-set-only exit (`:258`) | a fully hidden document scores 100%; 1,665 flagged docs exit 0 | the display gate becomes a gate |
| 8 | `fulltext_parse.py` front-region fallthrough to `frontmatter` (`:1376-1387`) | 3,116 docs render nothing but their title | the largest single content recovery available |
| 9 | `DECISION_FAMILY_RE`'s silent inclusion of early HRC under the A/DEC label (`fetch.py:97`) | mislabelled; 35 symbols have no ledger row at all | 35 symbols become visible to recheck |
| 10 | `MIN_DATE` treated as a format-era boundary (`fetch.py:94`) | falsified: 5/5 probed 1993 symbols return WordPerfect today | 62 documents move off the OCR path, 13 gain text |
| 11 | `_repair_ocr_markers` / `_merge_hanging_markers` writing a marker that is not in the source (`extract_pdf.py:527-562, 689-715`) | **263 fabricated operative-paragraph numbers**, incl. `S/RES/661(1990)` | stops writing false statements into a legal corpus — the single most serious defect found |
| 12 | `ADOPTED_RE`'s case-insensitive `^adopted` crop terminator (`extract_pdf.py:765-826`) | 145 documents silently reduced to 20.9% of source with `flags=[]`; `A/RES/701(VII)` = 47 characters | ~760k characters and the credibility of `anchor_found=True` |
| 13 | `_PAGE_NUM`'s bare `"N."` rule (`extract_pdf.py:278-332`) | deletes ≈2,060 operative-paragraph markers at column margins | operative numbering across ~1,500 documents |
| 14 | `_join_lines` hyphen deletion on a lowercase continuation (`extract_pdf.py:599-612`) | 8.12% of hyphenated compounds destroyed; `selfdetermination` in 113 documents | legal terms of art |

### Tighten (justified in principle, wrong in width)

* `_RUN_HEADER` (`verify_volumes.py:64-68`) — anchor `^[AES]/\d` to whole-line symbols;
  require the General-Assembly/session pattern to match the whole line.
* Front matter must be **reported as a number and bounded**, not treated as a free
  category: `E/2010/99` excludes 74.7% of its tokens from the denominator.
* `dedupe_brackets` (`fetch.py:308-335`) — do not drop a bracket whose parent is
  `unavailable`/`no_text_layer` (799 symbols).
* `--recheck-recent-days` SQL must implement the `updated_at` clause its own help text
  promises (4,841 rows are otherwise unreachable).
* `_bold_heading`'s 60-char / 8-word window and `STYLE_HEADING_FP_RE`'s unanchored
  ordinal alternative need evidence or removal.
* `detect_dotted_enum`'s `kind == 'paragraph'` filter (`parse.py:632`) — table cells
  carry the SDG indicator framework.
* The **French facing-language filter** (`extract_pdf.py:102-120`) — the predicate is
  right, the blanket application is not. Gate it per document on the ≥2%-of-lines signal
  that separates the 498 genuine bilingual volumes from the 39 English ones, stop
  applying it to the verify ground truth, and **persist the dropped lines**, not just a
  count. Recovers 1,003 lines / 10,317 tokens of NGO names now missing.
* `no_text_layer` is the one threshold that came through clean — all 5,009 rejects
  re-opened, 5,006 hold no word-token at all, and the nearest accepted document sits at
  4.4× the bar. Add a **script check before the token check** so mis-fetched Russian
  documents are labelled "re-fetch" rather than "needs OCR" (3 cases), and are not
  admitted as English above the token bar (`A/RES/981(X)`, `A/RES/3446(XXX)`; ≈252 rows
  of Cyrillic in the corpus).
* `split_columns`' single-column fallback needs a post-hoc sanity check (`A/RES/32/1`
  stored 50 characters with `flags=[]`), and the small-font footnote split needs a
  per-column basis (18 documents store operative paragraphs as footnotes).

### The honest headline numbers

| claim currently published | honest replacement |
|---|---|
| volume gate **89/89**, coverage 0.979–1.000 | **79/89** with the cross-volume exception removed; **27/89** if coverage measures the routed children as documented; 3 volumes verify nothing at all |
| "3,354 decisions + 236 HRC docs recovered" | 3,590 child documents written, of which **707 (19.7%) render body text** to a user |
| in-scope decision coverage | **3,590 / 4,594 = 78.1%** have stored text; **707 / 4,594 = 15.4%** are readable |
| `verify_text` "99.998%, 760/763 clean" | computed over **5%** of the 15,377-document Word corpus |
| `verify_pdf` "aggregate in-region preserved" | meaningless until the denominator stops shrinking with the damage — it printed **99.451% / exit 0** on five documents with 90% of their content deleted |
| parser "0 accounting issues, preservation 100%" | true and irrelevant: the invariant is satisfied by stamping content `frontmatter` |
| "the nightly fails the workflow and emails" | the workflow exists only on `feature/fulltexts`; **it has never run in CI** |

### Verifiability — the checks that would have caught all of this

Each is cheap, **total** rather than sampled, and comes with the negative control that
proves it can fail.

1. **Source-side decision census.** For every in-scope catalog symbol, assert stored
   text exists. Denominator from `digitallibrary.documents`, never from the split.
   Control: delete one child's rows → must fail. *Current honest score: 3,590/4,594.*
2. **Zero-visible-words invariant.** Every parsed document must render ≥1 body element
   under the site's own `HIDDEN_TYPES`. Control: stamp a known-good resolution's body
   `frontmatter` → must fail. *Current honest score: 22,618/25,734 (3,116 fail).*
3. **Multi-part conservation.** For any child symbol produced by more than one volume,
   assert the stored text contains every printing's distinctive tokens. Control: store
   only one printing → must fail. *Current honest score: 0/15.*
4. **Damage-invariant denominators.** Every gate's ground truth must be re-derived from
   the archived source, never from the parse. Control: delete 90% of the parse → the
   denominator must not move. *`verify_pdf` currently fails this outright.*
5. **Gate self-tests that damage a real document.** Not predicate unit tests. Every
   gate ships one control per exception it grants, and the controls run in the same
   command as the gate.
6. **Wire the nightly to `main`,** or stop describing these gates as CI-enforced.

An untracked `python/fulltext_negative_controls.py` (59 KB, written 2026-07-27 14:45 by
a concurrent session) implements much of (5). It is not committed and nothing invokes
it; it played no part in the findings above.
