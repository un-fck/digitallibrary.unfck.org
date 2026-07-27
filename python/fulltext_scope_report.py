#!/usr/bin/env python3
"""Scope boundaries: where every catalog document stands, in exactly one bucket.

Card C9. Answers the only question that matters for scoping: **which documents do
we have, and which do we not** — with the denominator taken from the CATALOG
(digitallibrary.documents), never from what processing produced.

Doctrine this script is built to satisfy (see docs/_research/adversarial-*.md and
/Users/david/UN/programme-budget-data/docs/LESSONS.md):

- **Total, not sampled.** Every catalog symbol is classified. A symbol that fits no
  bucket is a FAILURE, not a rounding error — `unclassified > 0` exits non-zero.
- **Denominator from the source.** The universe is the catalog; buckets are assigned
  by CASE over that universe, so a bucket cannot shrink the denominator with itself.
- **Partition is asserted, not assumed.** Bucket counts must sum to the universe and
  each symbol must appear exactly once; both are checked and both can fail.
- **"Has rows" is not "has text".** A document whose stored rows are all hidden by the
  website's render predicate is counted as *stored but invisible*, because a reader
  sees nothing. The predicate is mirrored from ParagraphsSection.isContentElement.
- **Silence is not success.** Writes a result file as well as stdout, so a swallowed
  pipe or a crash cannot read as a pass. Never pipe this script.

Read-only. Usage:
    uv run python python/fulltext_scope_report.py            # table + result file
    uv run python python/fulltext_scope_report.py --json     # machine-readable too
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fulltext_common import get_conn

RESULT_MD = Path(__file__).resolve().parent.parent / "docs" / "_research" / "scope-boundaries.md"
RESULT_JSON = Path(__file__).resolve().parent.parent / "docs" / "_research" / "scope-boundaries.json"

CATALOG_RE = r"^(A/RES/|A/DEC/|S/RES/|S/PRST/|E/RES/|E/DEC/|A/HRC/RES/|A/HRC/PRST/)"

# Mirrors website/src/components/ParagraphsSection.tsx :: isContentElement.
# A row is visible unless its type is hidden; 'title' is visible only inside an annex.
HIDDEN_TYPES = ("frontmatter", "backmatter", "footnote", "footer", "signature",
                "vote_record", "divider", "table")

# One CASE, evaluated top to bottom: the first matching arm wins, so the buckets are a
# partition by construction. The assertions below prove it stayed one.
PARTITION_SQL = f"""
WITH catalog AS (
  SELECT DISTINCT ON (symbol_normalized)
         symbol_normalized,
         date_publication,
         position('[' in symbol_normalized) > 0            AS is_bracket,
         symbol_normalized ~ '^(A/DEC/|E/DEC/)'            AS is_decision
  FROM digitallibrary.documents
  WHERE deleted_at IS NULL AND symbol_normalized ~ %(catalog_re)s
  ORDER BY symbol_normalized, recid DESC
),
visible AS (
  SELECT symbol_normalized,
         count(*) FILTER (
           WHERE type <> ALL(%(hidden)s)
             AND (type <> 'title' OR section = 'annex')
             AND coalesce(text, '') <> ''
         ) AS visible_rows,
         count(*) AS stored_rows
  FROM digitallibrary.document_paragraphs
  GROUP BY 1
),
led AS (
  SELECT symbol_normalized, status, format
  FROM digitallibrary.document_files
  WHERE lang = 'en'
),
parents AS (SELECT DISTINCT symbol_normalized FROM digitallibrary.document_paragraphs)
SELECT
  CASE
    WHEN v.visible_rows > 0                       THEN '1 text on site'
    WHEN v.stored_rows  > 0                       THEN '2 stored but invisible to reader'
    WHEN c.is_bracket AND EXISTS (
           SELECT 1 FROM parents p
           WHERE p.symbol_normalized = regexp_replace(c.symbol_normalized, '\\s*\\[.*$', '')
         )                                        THEN '3 bracket part, parent has text'
    WHEN c.is_bracket                             THEN '4 bracket part, parent has no text'
    WHEN l.status = 'no_text_layer'               THEN '5 image scan, no text layer (OCR, out of scope)'
    WHEN c.is_decision AND c.date_publication < DATE '2003-01-01'
                                                  THEN '6 decision in scanned volume (OCR, out of scope)'
    WHEN c.is_decision                            THEN '7 decision, born-digital volume, NOT recovered'
    WHEN l.status = 'unavailable'                 THEN '8 confirmed absent from ODS'
    WHEN l.status IS NOT NULL                     THEN '9 fetched, not yet through the pipeline'
    ELSE                                               'X never probed (should be zero)'
  END AS bucket,
  count(*) AS docs
FROM catalog c
LEFT JOIN visible v USING (symbol_normalized)
LEFT JOIN led     l USING (symbol_normalized)
GROUP BY 1
ORDER BY 1
"""

UNIVERSE_SQL = f"""
SELECT count(*) FROM (
  SELECT DISTINCT symbol_normalized FROM digitallibrary.documents
  WHERE deleted_at IS NULL AND symbol_normalized ~ %(catalog_re)s
) t
"""

# Buckets that mean "a reader can read this document today".
DELIVERED = {"1 text on site", "3 bracket part, parent has text"}
# Buckets deliberately outside the current scope — named and counted, never silently dropped.
OUT_OF_SCOPE = {
    "5 image scan, no text layer (OCR, out of scope)",
    "6 decision in scanned volume (OCR, out of scope)",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="also print machine-readable JSON")
    args = ap.parse_args()

    params = {"catalog_re": CATALOG_RE, "hidden": list(HIDDEN_TYPES)}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(UNIVERSE_SQL, params)
        universe = cur.fetchone()[0]
        cur.execute(PARTITION_SQL, params)
        rows = cur.fetchall()

    buckets = {name: n for name, n in rows}
    total = sum(buckets.values())
    unclassified = buckets.get("X never probed (should be zero)", 0)
    delivered = sum(n for b, n in buckets.items() if b in DELIVERED)
    out_of_scope = sum(n for b, n in buckets.items() if b in OUT_OF_SCOPE)
    gap = total - delivered - out_of_scope

    lines = []
    lines.append(f"Catalog universe (source-derived denominator): {universe:,}")
    lines.append("")
    width = max(len(b) for b in buckets)
    for bucket in sorted(buckets):
        n = buckets[bucket]
        # Residuals are never rounded to zero: a non-zero share below 0.01% prints '<0.01'.
        pct = n / universe * 100 if universe else 0.0
        pct_s = f"{pct:6.2f}%" if pct >= 0.01 or n == 0 else " <0.01%"
        lines.append(f"  {bucket:<{width}}  {n:>7,}  {pct_s}")
    lines.append("")
    lines.append(f"  {'DELIVERED (reader can read it)':<{width}}  {delivered:>7,}  "
                 f"{delivered / universe * 100:6.2f}%")
    lines.append(f"  {'OUT OF SCOPE (OCR, named + counted)':<{width}}  {out_of_scope:>7,}  "
                 f"{out_of_scope / universe * 100:6.2f}%")
    lines.append(f"  {'GAP (in scope, not delivered)':<{width}}  {gap:>7,}  "
                 f"{gap / universe * 100:6.2f}%")

    failures = []
    if total != universe:
        failures.append(f"partition does not sum: buckets={total:,} universe={universe:,} "
                        f"(diff {universe - total:+,}) — a symbol is in two buckets or none")
    if unclassified:
        failures.append(f"{unclassified:,} symbols matched no bucket "
                        f"('never probed') — these are unaccounted for, not 'fine'")

    verdict = ("PASS — every catalog symbol accounted for in exactly one bucket"
               if not failures else "FAIL — " + "; ".join(failures))
    body = "\n".join(lines)
    print(body)
    print()
    print(verdict)

    RESULT_MD.parent.mkdir(parents=True, exist_ok=True)
    RESULT_MD.write_text(
        "# Scope boundaries — what we have and what we do not\n\n"
        "Regenerate: `uv run python python/fulltext_scope_report.py` (never through a pipe).\n"
        "Denominator is the catalog itself; buckets are a partition and the partition is asserted.\n\n"
        "```\n" + body + "\n```\n\n" + verdict + "\n")
    RESULT_JSON.write_text(json.dumps(
        {"universe": universe, "buckets": buckets, "delivered": delivered,
         "out_of_scope": out_of_scope, "gap": gap, "verdict": verdict}, indent=2) + "\n")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
