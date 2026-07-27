# Adversarial review of the full-text verification gates

`FAIL — 15/50 negative controls detected. Fabrication is invisible to every gate. verify_pdf detected 0/6: its denominator is the parse it is grading, so deleting 90% of a document reads 100.00% preserved. verify_volumes detected 2/10: it re-runs the splitter and grades that, not what was written. Every gate passes on an empty input set, and a production run of the text gate today checks 0 documents and exits 0.`

Date: 2026-07-27. Branch `feature/fulltexts` (79bdd29), repo `documents.unfck.org`
(formerly `digitallibrary.unfck.org`).

Suite: `python/fulltext_negative_controls.py` — runnable, re-runnable, CI-able.
Nothing here was written by the people who built the gates; every verdict below
comes from damaging an input and watching, not from reading the code.

```bash
initdb -D /tmp/advpg -U adv --auth=trust
pg_ctl -D /tmp/advpg -o "-p 55432" start
createdb -h 127.0.0.1 -p 55432 -U adv advtest
export ADV_DATABASE_URL='postgresql://adv@127.0.0.1:55432/advtest'
uv run python python/fulltext_negative_controls.py --seed
uv run python python/fulltext_negative_controls.py ; echo "rc=$?"   # never pipe it
```

Safety: production Postgres is read with `SET default_transaction_read_only`;
the SSD archive is read-only (damaged archive trees are symlink farms); every
mutation lands in a throwaway local cluster the script refuses to run against a
URL matching `DATABASE_URL` or containing `azure`.

---

## 1. The count, honestly

**15 of 50 negative controls detected. 6 of 7 baselines quiet.**

| gate | detected / controls | what that means |
|---|---|---|
| `fulltext_verify_text.py` | 5 / 11 | catches bulk deletion; blind to fabrication, misattribution, labels |
| `fulltext_verify_pdf.py` | **0 / 6** | detected nothing at all |
| `fulltext_verify_volumes.py` | 2 / 10 | both hits are volume-side; child-side is 1/8 |
| `fulltext_verify_toc.py` | 2 / 4 | and it is already red on undamaged input |
| `fulltext_verify_display.py` | 2 / 4 | empty document reads as 100% visible |
| `fulltext_audit_invariants.py` | **0 / 3** | `return 0` unconditionally — it has no failure signal |
| `fulltext_parse.py` accounting | 3 / 5 | the invariant works; the exit code ignores it |
| `fulltext_nightly.py` | 1 / 4 | a night that does nothing exits "clean night" |

Do not read "15/50" as "the pipeline is 30% right". It means: of fifty specific
ways this pipeline could be wrong, thirty-five would ship without anything
saying a word.

---

## 2. Findings that matter most

### 2.1 A gap and a fabrication are not the same failure — and only gaps are checked

Every text gate computes `missing = ground_truth − parsed` and looks only at the
positive part. Nothing anywhere computes `parsed − ground_truth`.

Controls `T-FABRICATE`, `T-FABRICATE-XDOC`, `P-FABRICATE`, `V-FABRICATE`,
`C-FABRICATE`, `A-FABRICATED-TEXT` — six independent injections of text that
exists in no source document, into six different layers — produced **six clean
passes**. `T-FABRICATE-XDOC` spliced half of `S/RES/2824(2026)` into
`S/RES/2825(2026)`'s parse: `rc=0`, `preserved=100.000%`.

`fulltext_parse.py`'s accounting invariant is the closest thing to a conservation
check, and it conserves *positions*, not *text*. An element may carry
`raw_positions = [12, 13]` and any string whatsoever; `_check_accounting` returns
`None`. Replacing every element's text with invented prose leaves the parse
reporting **0 accounting failures** (control `A-FABRICATED-TEXT`).

Worst case this allows: a merge/split bug in the parser that mints clauses —
publishing operative paragraphs the UN never adopted, under a real resolution
symbol, on `mandates.un.org`. That is the exact defect class that put 196
invented staffing rows into `programme-budget-data`.

### 2.2 `verify_pdf`'s denominator is the artefact it is grading (0/6)

`region_ground_truth(pdf, doc)` anchors the comparison region by locating **the
parse's own first and last elements** inside the pdftotext stream. The
denominator is therefore a function of the numerator. Measured, on
`A/RES/1000(ES-I)`:

| state of the parse | region tokens | reported preserved | rc |
|---|---|---|---|
| pristine | 1,676 | 98.6% | 0 |
| second half deleted | 657 | 99.70% | 0 |
| 90% of elements deleted (7 of 74 kept) | **135** | **100.00%** | **0** |

Deleting nine tenths of the document *improved* the score to a perfect one. This
is `len(x)/len(x)` wearing a different hat, and it is the single most dangerous
instrument in the pipeline because its number is quoted in `docs/fulltexts.md`
("aggregate in-region preservation") as evidence the PDF path is sound.

Two more holes in the same gate:
- `ok = n_lost <= args.max_loss or preserved >= bar` — an **OR**. Any document
  losing ≤ 5 tokens passes regardless of size. Control `P-BAND-5TOKENS` deleted
  5 distinct in-region content words: 24 tokens lost, `rc=0`.
- A missing parsed JSON is `SKIP`, not `FAIL` (control `P-NO-ARTEFACT`), unlike
  the docx gate, which FAILs on the same condition. The two twins disagree about
  whether a missing artefact is an error.

### 2.3 `verify_volumes` grades a split it recomputes, not the split that was written (2/10)

`verify_volume()` calls `split_volume(...)` live and then checks *that* against
the file. The consequences are measurable and identical across every child-side
damage — coverage stays at **exactly 0.993** in all seven cases:

| damage | coverage | leaks | rc |
|---|---|---|---|
| none | 0.993 | 0 | 0 |
| all 44 children deleted from the raw layer | 0.993 | 0 | 0 |
| one child deleted | 0.993 | 0 | 0 |
| every child's rows moved under the next decision's symbol | 0.993 | 0 | 0 |
| a child truncated to half its length | 0.993 | 0 | 0 |
| the next decision's heading appended to its predecessor | 0.993 | 0 | 0 |
| 3 invented paragraphs inserted into a child | 0.993 | 0 | 0 |
| **90% of the volume's own raw rows deleted** | **0.249** | 1 | **1** |

The coverage number measures *file → raw extraction*. It never looks at the
children. `accounted` is built from `read_volume_rows()` — the volume's own rows
— so the split can be arbitrarily wrong and coverage cannot move.

The boundary-leak check does run, but on the freshly recomputed in-memory split,
so a leak in the **stored** children is invisible (`V-LEAK-NEXT-HEADING`, which
is precisely the failure `docs/fulltexts.md` says this gate catches). A
one-sentence leak is invisible even in principle: the test is
`nxt_head[:40] in child_text`, so anything short of swallowing a whole heading
passes.

Deleting all 44 children flips the gate's note from `children from DB` to
`children not yet in DB (validating split vs file)` — a *normal* state, not a
failure. The gate cannot distinguish "not written yet" from "wiped".

And an exception absorbs a whole deletion: when a fresh-split child is absent
from `document_paragraphs_raw`, the gate looks for it in `document_paragraphs`
and, if found, **excludes its tokens from the comparison** ("cross-volume
multi-part decisions"). Control `V-DEL-ONE-CHILD` deleted a child from the raw
layer: excused. Only deleting it from *both* layers (`V-DEL-CHILD-BOTH`) fired.
That exception is a claim, and it is currently sized to swallow a total loss.

### 2.4 The "benign run-join" allowlist excuses 11% of real content words

`_is_run_join(tok, present)` excuses any missing token that segments into ≥2
pieces all present in the parse. Pieces may be single characters, and UN
resolutions are full of standalone `a`/`b`/`c`/`i` tokens from `(a)`-style
enumerators. Measured over a random sample of 300 parsed documents:

- distinct content tokens of ≥8 characters: **24,327**
- of which the rule would excuse if the parser dropped them: **2,697 = 11.09%**
- documents containing at least one such token: **111 / 300 = 37.0%**

Real segmentations found:

```
A/RES/78/143      international     -> ['inter', 'national']
S/RES/362(1974)   secretarygeneral  -> ['secretary', 'general']
A/HRC/RES/14/4    structuraladjustment -> ['structural', 'adjustment']
A/RES/1620(XV)    paragraphs        -> ['paragraph', 's']
```

If the parser drops every occurrence of "international" from A/RES/78/143, or
"Secretary-General" from S/RES/362(1974), the acceptance gate says
`preserved=100.000%`. Control `T-EXCUSE-RUNJOIN` demonstrates this end to end.

### 2.5 Every gate passes on an empty input set — and this is live today

`T-EMPTY-SET`, `P-EMPTY-SET`, `V-EMPTY-SET`, `C-EMPTY-SET` all pass with zero
documents. This is not hypothetical:

```
$ uv run python python/fulltext_verify_text.py --limit 600 ; echo $?
Acceptance gate: 600 extracted docs; parsed_dir=.../parsed_dev
checked=0  pass=0  FAIL=0  skip(no docx)=600
ground-truth words=0  genuine tokens lost=0  aggregate preserved=0.0000%
0
```

The gate selected 600 real documents, compared **none** of them, and exited 0.
The first 600 symbols in `ORDER BY symbol_normalized` are volume-split children,
which have no `archive_path` — so they are `SKIP`, and a run that checks nothing
is indistinguishable from a run that checks everything.

Corpus-wide, of the **25,734** rows the text gate selects:

| | count | share |
|---|---|---|
| actually compared (resolvable `.docx`) | 15,141 | 58.8% |
| skipped — archive path is not `.docx` | 7,003 | 27.2% |
| skipped — **no archive file at all** (volume-split children) | 3,590 | 13.9% |

All 3,590 volume-split children have `archive_path IS NULL`, so `verify_text`
and `verify_pdf` both skip them silently. Their only cover is `verify_volumes`,
whose child-side controls score 1/8. **13.9% of the corpus is effectively
ungated for text preservation.**

### 2.6 `fulltext_audit_invariants.py` has no failure signal at all (0/3)

`main()` ends `return 0`. Unconditionally. Nulling `paragraph_type` on every
element of three documents moved the finding count 4 → 6 and the exit code 0 → 0.
Deleting the documents outright printed `(audit set: 0 docs scanned)` and exited
0. Writing `FAIL` into a TSV and exiting 0 is a comment, not a check — and this
script is not run by CI at all, so nobody reads the TSV either.

It also degrades silently: if `mandates.paragraphs` is unreadable (which is the
case for the worktree's own `DATABASE_URL` today — `permission denied for schema
mandates`), check (d) — the only cross-corpus structure check — prints a warning
to stderr and stops running. Exit code 0.

Both this script and `fulltext_verify_display.py` hardcode
`WORKTREE_ENV = "/Users/david/UN/digitallibrary.unfck.org/.claude/worktrees/fulltexts/.env"`,
a path that **no longer exists** since the repository was renamed. The fallback
silently takes over. A hardcoded absolute path that has already gone stale once
is a guard waiting to stop running.

### 2.7 `verify_display`: empty renders as perfect, and the gate is off by default

- `pct = (100.0 * vis / total) if total else 100.0`. A document with zero
  countable tokens scores **100% visible** and is never flagged. Control
  `D-ZERO-TOKENS` blanked every element's text: flagged count went 2 → 0, rc 0.
  On production **80 documents** currently have zero countable tokens and are
  therefore reported as perfectly visible.
- Deleting a document's rows removes it from `rows` entirely, so it cannot be
  flagged (`D-DEL-DOCS`). Absence is invisible to a loop over what is present.
- `return 1 if flagged_audit else 0`. With no audit set present, `highlight` is
  empty, so `flagged_audit` is always empty. Control `D-DEFAULT-NO-SET`:
  **45 documents flagged below 60% and the gate exited 0.** The audit set lives
  on an external SSD; on any machine where that SSD is not mounted this gate is
  a no-op that reports success.

### 2.8 `verify_toc` is red on correct input, so it cannot gate anything

`C-BASELINE` on `A/RES/79/1` (pristine, correct): `rc=1`, 6 misclassified +
2 split — the gate's own docstring says those are expected. A check that fires
on undamaged input teaches people to ignore it, and its exit code stops carrying
information. Judged on movement of its finding counts instead, it caught
deletion and demotion, and missed fabrication (identical counts: `(92,84,0,6,2)`
before and after inserting three invented headings).

Additionally, a document that declares no structure is silently unexaminable.
The gate's own docstring concedes "the ODS Word files essentially never carry
Word TOC fields, `_Toc` bookmarks, or Contents pages" — control `C-NO-DECLARED`
confirms `with self-declared structure: 0` for an ordinary S/RES document. The
denominator is what the docx happened to declare, not what the document is.

### 2.9 Orchestration: silence reads as success

`N-ZERO-WORK-NIGHT` — a night in which no document reaches `status='extracted'`
(source blocks every fetch, or a selection bug returns nothing) skips the parse
stage and **both acceptance gates**, prints `gates : n/a`, and exits 0 with
`FINAL: clean night.` A verdict carrying no bars at all must not read as a pass.

`fulltext_nightly.py` writes no machine-readable verdict anywhere. The exit code
is the only signal, and `T-CRASH-ARTEFACT` demonstrates how easily that is lost:
the same gate run that returns `rc=1` returns `rc=0` through `| tail -2`. The
runbook in `docs/fulltexts.md` shows piped invocations.

Automatically-run gates: `verify_text`, `verify_pdf`, `verify_volumes` (the last
via `fulltext_split_volumes.py --nightly`). **`verify_toc`, `verify_display` and
`audit_invariants` are never run by CI** — exactly the three that exist because
"fidelity gates cannot see structure-flattening or display-invisibility".

---

## 3. Negative-controls table

Generated by the suite; regenerated at
`$ADV_SCRATCH/negative-controls.md` on every run.

| control | gate | damage applied | expected | observed | verdict |
|---|---|---|---|---|---|
| `T-BASELINE` | verify_text | none (pristine parse) | quiet | rc=0 | **ok** |
| `T-DEL90` | verify_text | kept 2/20 elements | DETECT | rc=1 | **DETECTED** |
| `T-DEL-ALL` | verify_text | elements=[] | DETECT | rc=1 | **DETECTED** |
| `T-NO-ARTEFACT` | verify_text | parsed JSON file deleted | DETECT | rc=1 | **DETECTED** |
| `T-NO-SOURCE` | verify_text | archived .docx removed (ground truth absent) | DETECT | rc=0 | **MISSED** |
| `T-FABRICATE` | verify_text | 3 invented operative clauses appended to the parse | DETECT | rc=0 | **MISSED** |
| `T-FABRICATE-XDOC` | verify_text | half of S/RES/2824(2026)'s elements spliced into S/RES/2825(2026) | DETECT | rc=0 | **MISSED** |
| `T-MISATTRIB` | verify_text | element texts rotated by 3 (every clause under the wrong number/type) | DETECT | rc=0 | **MISSED** |
| `T-TRUNCATE-TAIL` | verify_text | last 12 words cut off the longest clause (12 tokens actually removed) | DETECT | rc=1 | **DETECTED** |
| `T-EMPTY-SET` | verify_text | 0 documents selected | DETECT | rc=0 | **MISSED** |
| `T-LABEL-SCRAMBLE` | verify_text | operative↔preambular flipped, headings demoted, all prefixes blanked, order reversed | DETECT | rc=0 | **MISSED** |
| `T-EXCUSE-RUNJOIN` | verify_text | every occurrence of a real content word deleted from the parse | DETECT | rc=0 | **MISSED** |
| `T-CRASH-ARTEFACT` | verify_text | parsed JSON corrupted to invalid JSON | DETECT | rc=1 (same run piped to `tail -2`: rc=0) | **DETECTED** |
| `P-BASELINE` | verify_pdf | none (pristine parse) | quiet | rc=0 | **ok** |
| `P-DEL90` | verify_pdf | kept 7/74 elements | DETECT | rc=0, region 1676→135, preserved 100.00% | **MISSED** |
| `P-TRUNCATE-TAIL` | verify_pdf | second half of the parse deleted | DETECT | rc=0, region 1676→657, preserved 99.70% | **MISSED** |
| `P-NO-ARTEFACT` | verify_pdf | parsed JSON file deleted | DETECT | rc=0 (SKIP) | **MISSED** |
| `P-FABRICATE` | verify_pdf | 3 invented clauses appended to the parse | DETECT | rc=0 | **MISSED** |
| `P-BAND-5TOKENS` | verify_pdf | 5 distinct in-region content words deleted (= default `--max-loss`) | DETECT | rc=0, genuine_lost=24 | **MISSED** |
| `P-EMPTY-SET` | verify_pdf | 0 documents selected | DETECT | rc=0 | **MISSED** |
| `V-BASELINE` | verify_volumes | none (pristine split) | quiet | rc=0, coverage 0.993 | **ok** |
| `V-DEL-CHILDREN` | verify_volumes | all 44 children deleted from the raw layer | DETECT | rc=0, coverage 0.993 | **MISSED** |
| `V-DEL-ONE-CHILD` | verify_volumes | one child deleted from the raw layer | DETECT | rc=0, coverage 0.993 | **MISSED** |
| `V-DEL-CHILD-BOTH` | verify_volumes | one child deleted from raw AND semantic layers | DETECT | rc=1, leaks=2 | **DETECTED** |
| `V-MISATTRIB` | verify_volumes | every child's rows moved under the NEXT decision's symbol | DETECT | rc=0, coverage 0.993 | **MISSED** |
| `V-TRUNCATE-CHILD` | verify_volumes | second half of a child deleted | DETECT | rc=0, coverage 0.993 | **MISSED** |
| `V-LEAK-1SENTENCE` | verify_volumes | one extra sentence appended to a child | DETECT | rc=0, leaks=0 | **MISSED** |
| `V-LEAK-NEXT-HEADING` | verify_volumes | the next decision's heading appended to its predecessor | DETECT | rc=0, leaks=0 | **MISSED** |
| `V-FABRICATE` | verify_volumes | 3 invented paragraphs inserted into a child | DETECT | rc=0 | **MISSED** |
| `V-DEL-90-RAW` | verify_volumes | 90% of the volume's own raw rows deleted | DETECT | rc=1, coverage 0.249 | **DETECTED** |
| `V-EMPTY-SET` | verify_volumes | 0 volumes selected | DETECT | rc=0, "0/0 volumes passed" | **MISSED** |
| `C-BASELINE` | verify_toc | none (pristine, correct structure) | quiet | rc=1 | **noisy** |
| `C-NO-DECLARED` | verify_toc | a document whose docx declares no headings (the corpus norm) | DETECT | rc=0, "with self-declared structure: 0" | **MISSED** |
| `C-DEL-STRUCTURE` | verify_toc | every parsed element deleted | DETECT | signal (92,84,0,6,2)→(92,0,92,0,0) | **DETECTED** |
| `C-DEMOTE-HEADINGS` | verify_toc | heading/title rewritten to paragraph | DETECT | signal →(92,0,0,90,2) | **DETECTED** |
| `C-FABRICATE` | verify_toc | 3 invented headings inserted | DETECT | signal unchanged (92,84,0,6,2) | **MISSED** |
| `C-EMPTY-SET` | verify_toc | 0 documents selected | DETECT | rc=0 | **MISSED** |
| `D-BASELINE` | verify_display | none; threshold 0% | quiet | rc=0 | **ok** |
| `D-BASELINE-REAL` | verify_display | none; 60% threshold on 3 known-bad audit docs | DETECT | rc=1, 2 flagged | **DETECTED** |
| `D-NO-AUDIT-SET` | verify_display | explicit `--audit-set` path missing | DETECT | rc=1 (crash) | **DETECTED** |
| `D-DEFAULT-NO-SET` | verify_display | default audit_set.json absent while 45 docs are flagged | DETECT | rc=0 | **MISSED** |
| `D-DEL-DOCS` | verify_display | all rows of the flagged documents deleted | DETECT | rc=0, flagged 2→0 | **MISSED** |
| `D-ZERO-TOKENS` | verify_display | every element's text blanked (total_tokens = 0) | DETECT | rc=0, flagged 2→0, pct=100 | **MISSED** |
| `I-BASELINE` | audit_invariants | none | quiet | rc=0 | **ok** |
| `I-NULL-ALL-LABELS` | audit_invariants | paragraph_type nulled on every element of 3 docs | DETECT | rc=0 (findings 4→6) | **MISSED** |
| `I-DEL-DOCS` | audit_invariants | the audit-set documents deleted from the corpus | DETECT | rc=0, "0 docs scanned" | **MISSED** |
| `I-LEGACY-GONE` | audit_invariants | mandates.paragraphs unreadable → check (d) skipped | DETECT | rc=0 | **MISSED** |
| `A-BASELINE` | parse accounting | none | quiet | `_check_accounting → None` | **ok** |
| `A-UNACCOUNTED` | parse accounting | positions 6,7 consumed by nothing | DETECT | `'unaccounted positions: [6, 7]'` | **DETECTED** |
| `A-DUPLICATE` | parse accounting | position 1 consumed twice | DETECT | `'duplicate positions: [1]'` | **DETECTED** |
| `A-PHANTOM` | parse accounting | position 99 does not exist in the raw layer | DETECT | `'phantom positions: [99]'` | **DETECTED** |
| `A-FABRICATED-TEXT` | parse accounting | every element's text replaced with invented prose; positions intact | DETECT | `None` | **MISSED** |
| `A-EXIT-CODE` | parse accounting | an accounting failure occurs during a `--to-db` run | DETECT | `return 0 if n_failed == 0 else 1` | **MISSED** |
| `N-GATE-FAILURE` | nightly | the text gate fails on tonight's documents | DETECT | rc=1 | **DETECTED** |
| `N-ZERO-WORK-NIGHT` | nightly | 0 documents reach `extracted` — no gate runs | DETECT | rc=0, "clean night" | **MISSED** |
| `N-NO-RESULT-FILE` | nightly | the exit code is lost to a pipe / CI | DETECT | no verdict file written | **MISSED** |
| `N-GATE-COVERAGE` | nightly | a structural/display regression ships overnight | DETECT | only 3 of 6 gates run automatically | **MISSED** |

---

## 4. Patches for every MISSED control

Ordered by the size of the defect each currently admits.

### P1 — `fulltext_verify_pdf.py`: stop anchoring the region on the parse
*Fixes `P-DEL90`, `P-TRUNCATE-TAIL`. Currently admits: silent loss of up to 100%
of a PDF-sourced document with a perfect score, across 10,357 documents (40% of
the corpus).*

The region must be derived from the **document**, not the parse. Options, in
order of preference:

1. Anchor the region on the *symbol headings printed in the PDF* (the
   `<n>/<m>.` / "Resolution NNNN (YYYY)" lines pdftotext emits), i.e. the same
   boundary predicate `fulltext_split_volumes.pdf_heading` already implements.
   The target resolution's region is then "from its own printed heading to the
   next printed heading" — a property of the file.
2. Failing that, record the region at **extraction** time (the pymupdf crop
   already computes page/line bounds) and store it in
   `document_files.error`/a new column, so the gate reads a boundary produced
   before parsing rather than after.

Additionally, in `main()`:
- replace `ok = n_lost <= args.max_loss or preserved >= bar` with `and`
  (fixes `P-BAND-5TOKENS`);
- make a missing parsed JSON a `FAIL`, matching the docx twin
  (fixes `P-NO-ARTEFACT`);
- fail when `n_region` did not grow with the parse: assert
  `n_region >= 0.8 * (expected region size from the file-derived boundary)`.

### P2 — a corpus-wide fabrication check (the missing conservation check)
*Fixes `T-FABRICATE`, `T-FABRICATE-XDOC`, `P-FABRICATE`, `V-FABRICATE`,
`C-FABRICATE`, `A-FABRICATED-TEXT`. Currently admits: invented operative text
under a real resolution symbol, everywhere, undetected.*

New `python/fulltext_verify_fabrication.py`, or an added pass in each text gate:

```python
invented = parsed_words(doc) - docx_words(path)     # the OTHER direction
```
with a narrow, *enumerated* allowlist (inferred operative prefixes flagged by
`inferred_operative`, joined table-cell separators, vote-tally labels), and
non-zero exit on anything else. This is a conservation check, so it cannot miss
what it did not look at; `programme-budget-data` runs the equivalent over its
whole corpus in three minutes.

For the volume gate the same idea is one line: today
`lost = split_bag - db_bag - apparatus`; add
`invented = db_bag - split_bag - apparatus` and fail on it.

For `fulltext_parse.py`, extend `_check_accounting` to conserve *text*, not only
positions: every element's token multiset must be a sub-multiset of the union of
the raw rows at its `positions[]`. That is cheap (the rows are already in
memory) and it makes the fabrication class structurally impossible to hide.

### P3 — `fulltext_verify_volumes.py`: grade what was written, not a re-split
*Fixes `V-DEL-CHILDREN`, `V-DEL-ONE-CHILD`, `V-MISATTRIB`, `V-TRUNCATE-CHILD`,
`V-LEAK-1SENTENCE`, `V-LEAK-NEXT-HEADING`. Currently admits: any corruption of
the 3,590 stored volume-split children.*

1. Build `accounted` from **the children as stored** (`read_db_children`) plus
   the explicitly enumerated allowed-drop rows — not from `read_volume_rows`.
   Then a child that is missing, truncated or emptied moves the coverage number.
2. Make "no children in the DB" a **failure** for a volume whose ledger status is
   `'split'`. `children not yet in DB` is legitimate only before the write, and
   the gate knows the status.
3. Run the boundary-leak test over the **DB** children, and make it positional
   rather than substring-based: for consecutive children *a*, *b*, assert that
   no row of *a* has a raw `position` ≥ the first row of *b*. That catches a
   one-sentence leak, which no substring test can.
4. Add a misattribution check: every child's rows must be contiguous in the
   volume's own `position` sequence and must start at a routed heading. Compare
   `min(position)` per child against the split's routed heading positions.
5. Replace the "stored under the sibling volume" exception with an equality:
   the excused child must exist *and* its token bag must equal the fresh
   slice's, or the exception does not apply. As written it excuses a total
   deletion.

### P4 — exit codes and empty sets (cheap, high value)
*Fixes `T-EMPTY-SET`, `P-EMPTY-SET`, `V-EMPTY-SET`, `C-EMPTY-SET`,
`A-EXIT-CODE`, `I-NULL-ALL-LABELS`, `I-DEL-DOCS`, `D-DEFAULT-NO-SET`,
`N-ZERO-WORK-NIGHT`, `N-NO-RESULT-FILE`.*

- Every gate: `if n_checked == 0: print("FAIL: no documents were checked"); return 1`.
  Force a denominator — a bar that stopped running must show up as a suspicious
  zero, not as silence. `verify_text` additionally needs `--expect N` (or an
  assertion that `n_checked + n_skip == len(targets)` **and** `n_checked > 0`).
- `fulltext_parse.py`: `return 0 if (n_failed == 0 and n_acct_fail == 0) else 1`.
  The docstring's "an accounting failure does not fail the load" is a decision
  that leaves the invariant unenforced; keep the load, fail the process.
- `fulltext_audit_invariants.py`: `return 1 if any(sev == "high" for ...) else 0`,
  and make an unreadable legacy corpus a failure rather than a stderr warning.
- `fulltext_verify_display.py`: `return 1 if (flagged_audit or (not highlight and flagged)) else 0`
  — a missing audit set must not silently disable the gate. Better: fail loudly
  when the audit set cannot be loaded.
- `fulltext_nightly.py`: write `<archive>/audit/nightly.json` **before** printing
  anything (verdict, per-stage rc, counts checked by each gate), and treat
  `gate_ran == False` as a failure unless the ledger proves there was genuinely
  nothing to do. Add the three unrun gates to the nightly, or record explicitly
  why each is not gating.
- The runbook in `docs/fulltexts.md` should show `… > /tmp/gate.log 2>&1 ; rc=$?`
  and never a pipe.

### P5 — `verify_text` blind spots
*Fixes `T-NO-SOURCE`, `T-MISATTRIB`, `T-LABEL-SCRAMBLE`, `T-EXCUSE-RUNJOIN`.*

- `T-NO-SOURCE`: a `status IN ('extracted','parsed')` row whose archive file is
  missing is a broken invariant, not a skip. Count skips by *reason* and fail on
  `missing-file`; only `format='pdf'` (handed to the PDF gate) and
  `source_symbol IS NOT NULL` (handed to the volume gate) may be skipped — and
  print the handoff counts so the three gates' coverage adds up to 25,734.
- `T-EXCUSE-RUNJOIN`: require the fused token to be **absent from the docx as a
  standalone token** and each segment to be ≥3 characters, and cap the excuse at
  tokens the docx actually fuses (verify against the docx run boundaries rather
  than against the parse's vocabulary). Then measure the residue; an 11.09%
  excuse surface is not an allowlist, it is a hole.
- `T-MISATTRIB` / `T-LABEL-SCRAMBLE`: a bag-of-words gate cannot see order or
  labels, and should not pretend to. Add a separate, cheap **order** check:
  the concatenated element texts, tokenised, must be a subsequence of the docx
  token stream (LCS ratio ≥ 0.98). That catches rotation, reordering and
  cross-document splicing in one pass, and it is the check `docs/fulltexts.md`
  implies exists when it says text is "preserved".

### P6 — `verify_toc` / `verify_display` structural issues
*Fixes `C-BASELINE` (noisy), `C-NO-DECLARED`, `D-DEL-DOCS`, `D-ZERO-TOKENS`.*

- `verify_toc`: separate *known, triaged* findings from new ones (a checked-in
  expectations file keyed by symbol+heading), so the gate is green on the
  current corpus and non-zero only on movement. Until then it is a report and
  `docs/fulltexts.md` should stop calling it a verifier.
- `C-NO-DECLARED`: report `documents with no self-declared structure` as a
  coverage figure in the verdict line and fail if it exceeds a stated bar —
  otherwise the gate's silence on 99% of the corpus reads as a pass.
- `verify_display`: `pct = 0.0 if total == 0 else 100.0*vis/total` — an empty
  document is 0% visible, not 100%. There are 80 such documents today.
- `verify_display` / `verify_toc` / `audit_invariants`: the denominator must be
  the **ledger** (`document_files WHERE status='parsed'`), not
  `document_paragraphs`, so a document that vanished from the semantic layer is
  a finding rather than an absence. Iterating over what is present can never see
  what is missing.
- Delete the dead hardcoded `WORKTREE_ENV` path from both scripts and use
  `fulltext_common.get_conn()`.

---

## 5. Two notes on the suite itself

- Three controls initially failed because the *probe* was wrong, not the gate:
  a "delete one sentence" damage that removed zero tokens, a run-join victim
  search that only looked at one small document, and a display baseline whose
  threshold still flagged a document. All three were fixed rather than deleted,
  and every deletion control now prints how many tokens it actually removed —
  a control that damages nothing proves nothing.
- `C-FABRICATE` is judged on finding-count movement rather than exit code,
  because `verify_toc` is already red at baseline. Where a gate is red on
  correct input, its exit code carries no information and must not be scored as
  a detection.
