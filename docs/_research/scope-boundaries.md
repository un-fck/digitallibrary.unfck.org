# Scope boundaries — what we have and what we do not

Regenerate: `uv run python python/fulltext_scope_report.py` (never through a pipe).
Denominator is the catalog itself; buckets are a partition and the partition is asserted.

```
Catalog universe (source-derived denominator): 41,802

  1 text on site                                     22,322   53.40%
  2 stored but invisible to reader                    3,098    7.41%
  3 bracket part, parent has text                     2,723    6.51%
  4 bracket part, parent has no text                    799    1.91%
  5 image scan, no text layer (OCR, out of scope)     4,889   11.70%
  6 decision in scanned volume (OCR, out of scope)    5,247   12.55%
  7 decision, born-digital volume, NOT recovered      1,103    2.64%
  8 confirmed absent from ODS                         1,586    3.79%
  X never probed (should be zero)                        35    0.08%

  DELIVERED (reader can read it)                     25,045   59.91%
  OUT OF SCOPE (OCR, named + counted)                10,136   24.25%
  GAP (in scope, not delivered)                       6,621   15.84%
```

FAIL — 35 symbols matched no bucket ('never probed') — these are unaccounted for, not 'fine'
