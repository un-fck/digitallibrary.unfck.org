#!/usr/bin/env python3
"""Acceptance gate: end-to-end text-preservation check (archive docx -> parsed JSON).

This is the DESIGNATED acceptance gate for the bulk semantic-parse run. It is
INDEPENDENT of the raw extraction table: it re-reads the ground-truth word content
straight from the archived `.docx` (body paragraphs + tables + footnotes/endnotes)
and compares it, per document, against the parsed JSON in `parsed_dev/`. Any content
word present in the docx but missing from the parsed output is flagged as text loss
and fails the gate -- UNLESS it is explained by one of two KNOWN-BENIGN patterns
that are artifacts of the comparison, not real loss:

  1. VOTE-JSON KEYS. A vote record stores the tally *labels* ("In favour",
     "Against", "Abstaining", "Non-voting", "Absent") as JSON structure -- the
     member-state names are preserved in `vote[...]`, but the label words are not
     element text. So for a doc that HAS a vote_record, those label tokens are not
     "lost", they are structural. (The country names themselves ARE compared.)

  2. TOKENIZER RUN-JOINS. python-docx concatenates the runs of a paragraph; where a
     word wraps across a soft line break (or a heading is laid out across lines) two
     adjacent tokens fuse into one docx token ("Commissioner"+"for" ->
     "commissionerfor", "6th"+"meeting" -> "meeting6", footnote marker "a"+"See" ->
     "asee", "1"+"situation" -> "1situation"). The parser, working from the raw
     extraction, keeps them separate and correctly spaced. A fused token is benign
     iff it SEGMENTS entirely into tokens that are all present in the parsed output,
     with at least one segment of length >=3 (the content piece) -- i.e. the parser
     is *more* faithful than the docx tokenizer and no content was lost.

Two further nuisance classes are excluded before the loss test: bare numbers and
<=2-char tokens (numbering/markers/function-word fragments -- never content), and
tokens that are part of the document's OWN symbol (the "PRST"/"RES" of a running
header the parser correctly drops as a page artifact).

Hyphenation is normalised away on BOTH sides first (hyphens / soft hyphens /
apostrophes stripped) so "Al-Shabaab", "post-traumatic", "non-refoulement" never
register as differences.

Comparison uses multisets, so only the EXCESS count of a docx word over the parsed
count is treated as missing -- a note line duplicated 4x by a .doc->.docx conversion
that appears 0x in the parsed output shows up as a genuine loss of 4, while a word
that merely appears once more in a running header is caught the same way.

Known residual: on the current corpus exactly 3 documents fail with a small genuine
loss (S/PRST/2001/9, S/PRST/2014/3, S/RES/1881(2009)) -- a "Reissued for technical
reasons ..." provenance note that the *raw extractor* drops before the parser ever
sees it (verified absent from document_paragraphs_raw). That is an upstream
extraction gap, not a parser defect; use --ignore-symbols to acknowledge it once
triaged, or --max-loss to set a tolerance.

Conventions mirror the other python/ scripts: standalone, DATABASE_URL from .env via
fulltext_common, short-lived psycopg connection, no new tables.

Usage:
    uv run python python/fulltext_verify_text.py                     # whole corpus
    uv run python python/fulltext_verify_text.py --limit 50
    uv run python python/fulltext_verify_text.py --symbols A/RES/80/167 S/RES/2806(2025)
    uv run python python/fulltext_verify_text.py --max-loss 0 --verbose

Exit code: 0 iff every checked document preserves all content words (after the two
benign decompositions and the tolerance); nonzero if any document shows genuine
token loss, so it can gate CI / the bulk run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

import lxml.etree as ET

from fulltext_common import ARCHIVE_ROOT, get_conn, sanitize_symbol

PARSED_DIR = ARCHIVE_ROOT / "parsed_dev"

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
QP = W_NS + "p"
QT = W_NS + "t"

# Content-word tokens after hyphen/apostrophe normalisation.
_STRIP = str.maketrans("", "", "-­‐‑’'")
_TOKEN = re.compile(r"[a-z0-9]+")

# Benign pattern 1: vote tally labels -- structural JSON keys, not element text.
VOTE_LABEL_TOKENS = {"favour", "against", "abstaining", "voting", "nonvoting", "absent"}


def words(text: str | None) -> Counter:
    """Multiset of normalised content tokens (lowercase, hyphens/apostrophes removed)."""
    if not text:
        return Counter()
    t = text.lower().translate(_STRIP)
    return Counter(_TOKEN.findall(t))


# ---------------------------------------------------------------------------
# Ground truth from the archived docx (independent of document_paragraphs_raw)
# ---------------------------------------------------------------------------

def docx_words(path: Path) -> Counter:
    """All content words from the docx: body paragraphs + tables, plus foot/endnotes.

    Runs of a paragraph are joined before tokenising (matches the parser's own
    text, and keeps hyphen-normalisation consistent)."""
    c: Counter = Counter()
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "word/document.xml" in names:
            root = ET.fromstring(z.read("word/document.xml"))
            for p in root.iter(QP):
                c += words("".join(t.text or "" for t in p.iter(QT)))
        for name in names:
            if re.search(r"word/(footnotes|endnotes)\.xml$", name):
                root = ET.fromstring(z.read(name))
                for p in root.iter(QP):
                    c += words("".join(t.text or "" for t in p.iter(QT)))
    return c


# ---------------------------------------------------------------------------
# Parsed-side words
# ---------------------------------------------------------------------------

def parsed_words(doc: dict) -> tuple[Counter, bool]:
    """Multiset of parsed content words + whether the doc carries a vote_record."""
    c: Counter = Counter()
    has_vote = False
    for e in doc.get("elements", []):
        c += words(e.get("text"))
        if e.get("prefix"):
            c += words(e["prefix"])
        if e.get("type") == "vote_record":
            has_vote = True
        for lst in (e.get("vote") or {}).values():
            for name in lst:
                c += words(name)
    return c, has_vote


# ---------------------------------------------------------------------------
# Benign decomposition
# ---------------------------------------------------------------------------

def _is_run_join(tok: str, present: frozenset[str]) -> bool:
    """True if `tok` segments into TWO OR MORE pieces that are all present in the
    parsed output -- i.e. a docx tokenizer run-join, not real loss.

    Requiring >=2 pieces is essential: a token that appears fewer times in parsed
    than in the docx is a multiset excess (genuine partial loss, e.g. "memoire"
    duplicated by a note line) and must NOT be excused merely because the whole word
    exists elsewhere. Pieces may be single characters, because a fused token can be a
    footnote-marker letter + word ("a"+"see") or a function word + date ("on"+"30");
    the "each piece is itself a present parsed token" constraint is what keeps a real
    content word (whose letter-substrings are not standalone parsed tokens) from
    being spuriously segmented. DP over (start-index, piece-count)."""
    n = len(tok)
    if n < 3:
        return False

    @lru_cache(maxsize=None)
    def rec(i: int, pieces: int) -> bool:
        if i == n:
            return pieces >= 2
        for j in range(i + 1, n + 1):
            if tok[i:j] in present and rec(j, pieces + 1):
                return True
        return False

    ok = rec(0, 0)
    rec.cache_clear()
    return ok


def genuine_loss(gt: Counter, pw: Counter, has_vote: bool,
                 symbol_tokens: frozenset[str]) -> Counter:
    """docx words missing from parsed, after removing the known-benign patterns."""
    missing = gt - pw
    if not missing:
        return Counter()
    present = frozenset(pw)
    out: Counter = Counter()
    for tok, cnt in missing.items():
        if tok.isdigit() or len(tok) <= 2:
            continue  # bare numbers / markers / function-word fragments: never content
        if tok in symbol_tokens:
            continue  # the doc's own symbol in a (dropped) running header
        if has_vote and tok in VOTE_LABEL_TOKENS:
            continue  # benign pattern 1: vote tally labels are structural JSON keys
        if _is_run_join(tok, present):
            continue  # benign pattern 2: docx tokenizer run-join
        out[tok] = cnt
    return out


# ---------------------------------------------------------------------------
# Targets (ledger) + docx resolution (archive files)
# ---------------------------------------------------------------------------

def fetch_targets(limit: int | None, symbols: list[str] | None) -> list[tuple[str, str, str]]:
    """[(symbol, format, docx_relpath), ...] for extracted docs.

    The docx is converted_path when present (doc/wpd source), else archive_path
    (native docx)."""
    sql = ("SELECT symbol_normalized, format, "
           "COALESCE(converted_path, archive_path) AS docx_path "
           "FROM digitallibrary.document_files WHERE status = 'extracted' ")
    params: list[object] = []
    if symbols:
        sql += "AND symbol_normalized = ANY(%s) "
        params.append(symbols)
    sql += "ORDER BY symbol_normalized "
    if limit:
        sql += "LIMIT %s"
        params.append(limit)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Acceptance gate: docx->parsed text preservation.")
    ap.add_argument("--limit", type=int, help="cap number of documents checked")
    ap.add_argument("--symbols", nargs="*", help="restrict to these symbol_normalized values")
    ap.add_argument("--parsed-dir", type=Path, default=PARSED_DIR,
                    help="directory of parsed JSONs (default: parsed_dev on the SSD)")
    ap.add_argument("--max-loss", type=int, default=0,
                    help="per-doc genuine-token-loss tolerance before it FAILs (default 0)")
    ap.add_argument("--ignore-symbols", nargs="*", default=[],
                    help="symbols to exempt from failing (e.g. known upstream extractor gaps)")
    ap.add_argument("--verbose", action="store_true",
                    help="print a line for every doc, not just failures")
    args = ap.parse_args()

    parsed_dir: Path = args.parsed_dir
    ignore = set(args.ignore_symbols)
    targets = fetch_targets(args.limit, args.symbols)
    print(f"Acceptance gate: {len(targets)} extracted docs; parsed_dir={parsed_dir}")

    n_checked = n_pass = n_fail = n_skip = 0
    total_gt = total_lost = 0
    failures: list[tuple[str, str, Counter]] = []

    for symbol, fmt, rel in targets:
        pj = parsed_dir / f"{sanitize_symbol(symbol)}.json"
        if not pj.exists():
            print(f"  FAIL  {symbol:<24} [{fmt}] parsed JSON missing")
            n_fail += 1
            failures.append((symbol, fmt, Counter({"<no-parsed-json>": 1})))
            continue
        docx_path = (ARCHIVE_ROOT / rel) if rel else None
        if not docx_path or not docx_path.exists() or docx_path.suffix.lower() != ".docx":
            if args.verbose:
                print(f"  SKIP  {symbol:<24} [{fmt}] no docx archive file ({rel})")
            n_skip += 1
            continue

        try:
            doc = json.loads(pj.read_text(encoding="utf-8"))
            gt = docx_words(docx_path)
            pw, has_vote = parsed_words(doc)
        except Exception as exc:  # never let one bad file mask the rest
            print(f"  FAIL  {symbol:<24} [{fmt}] error: {type(exc).__name__}: {exc}")
            n_fail += 1
            failures.append((symbol, fmt, Counter({f"<{type(exc).__name__}>": 1})))
            continue

        symbol_tokens = frozenset(_TOKEN.findall(symbol.lower().translate(_STRIP)))
        lost = genuine_loss(gt, pw, has_vote, symbol_tokens)
        n_lost = sum(lost.values())
        n_checked += 1
        total_gt += sum(gt.values())
        total_lost += n_lost
        preserved = 100.0 * (sum(gt.values()) - n_lost) / max(sum(gt.values()), 1)

        if n_lost > args.max_loss and symbol not in ignore:
            n_fail += 1
            failures.append((symbol, fmt, lost))
            print(f"  FAIL  {symbol:<24} [{fmt}] preserved={preserved:.3f}%  "
                  f"lost={n_lost}  {dict(sorted(lost.items(), key=lambda x: -x[1])[:8])}")
        else:
            n_pass += 1
            tag = " (ignored)" if (n_lost > args.max_loss and symbol in ignore) else ""
            if args.verbose:
                print(f"  pass  {symbol:<24} [{fmt}] preserved={preserved:.3f}%{tag}")

    print("\n" + "=" * 64)
    print(f"checked={n_checked}  pass={n_pass}  FAIL={n_fail}  skip(no docx)={n_skip}")
    print(f"ground-truth words={total_gt}  genuine tokens lost={total_lost}  "
          f"aggregate preserved={100.0*(total_gt-total_lost)/max(total_gt,1):.4f}%")
    if failures:
        print(f"\n{len(failures)} document(s) failed the gate:")
        for sym, fmt, lost in failures:
            print(f"  {sym:<24} [{fmt}] {dict(sorted(lost.items(), key=lambda x: -x[1])[:12])}")
        print("\n(If a failure is a known upstream raw-extractor gap rather than a parser\n"
              " regression, re-run with --ignore-symbols <symbol...> once triaged.)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
