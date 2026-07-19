#!/usr/bin/env python3
"""Acceptance gate for the DETERMINISTIC PDF path (Track A, pre-1994).

The twin of `fulltext_verify_text.py`, but for PDF-sourced documents. It checks
that the content words of the TARGET resolution survive from the archived PDF
into the parsed JSON — using an INDEPENDENT extractor (`pdftotext -layout`,
poppler) as ground truth, so a bug in the pymupdf extractor cannot hide behind
itself.

What this gate can and cannot prove (be honest about excerpts):

  * The pre-1994 PDFs are mostly EXCERPTS of "Resolutions adopted ..." supplement
    pages: a file holds the END of the previous resolution, the TARGET, and the
    START of the next one, plus running headers/footers, page numbers, and (in old
    volumes) a FRENCH copy alongside the English. The extractor deliberately CROPS
    to the target and DROPS that apparatus. So a naive "every pdftotext word must
    appear in the parse" check would flood with false losses from content the crop
    was RIGHT to discard.

  * Therefore the ground truth is RESTRICTED to the cropped region: we anchor the
    parse's first and last content lines inside the pdftotext stream (fuzzy) and
    only compare words BETWEEN those anchors. Words outside — neighbour
    resolutions, headers, the French column — are an EXPECTED, counted crop-loss
    category, not a failure.

  * Within the cropped region the gate is strict (same benign allowlist ideas as
    the docx gate: bare numbers, <=2-char fragments, the doc's own symbol,
    hyphen/apostrophe normalisation). It PASSES a 'text'-class doc only when the
    in-region words are preserved. 'poor'-class docs are checked but held to a
    looser bar (--poor-max-loss); 'none'-class docs never reach here (skipped at
    extraction as no_text_layer). What it CANNOT prove: that the crop boundary is
    semantically perfect, or anything about a pure-scan doc with no text layer.

Usage:
    uv run python python/fulltext_verify_pdf.py
    uv run python python/fulltext_verify_pdf.py --symbols A/RES/1260(XIII) A/RES/42/33
    uv run python python/fulltext_verify_pdf.py --verbose --max-loss 0

Exit code: 0 iff every checked 'text'-class doc preserves its in-region words
(within tolerance); nonzero otherwise — so it can gate the bulk PDF run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from fulltext_common import ARCHIVE_ROOT, get_conn, sanitize_symbol

PARSED_DIR = ARCHIVE_ROOT / "parsed_dev"
PDFTOTEXT = "/opt/homebrew/bin/pdftotext"

_STRIP = str.maketrans("", "", "-­‐‑’'")
_TOKEN = re.compile(r"[a-z0-9]+")

# Facing-language (French) line detection — mirrors the extractor so the pdftotext
# ground truth excludes the French column of old bilingual supplement volumes (an
# expected crop-loss category, not a genuine drop). Kept as an INDEPENDENT copy so
# the gate stays decoupled from the pymupdf extractor. High precision: a tiny set
# of French function words that essentially never occur in English UN prose; a line
# counts as French only with >=3 of them.
_FRENCH_STOPWORDS = frozenset("""
le la les des du et aux une dans par qui que pour avec sur ses leur leurs cette
ces entre ainsi dont sont elle ils nous vous tous comme sans sous deux cet celle
ceux votre notre seance pleniere economique institutions specialisees
renseignements secretaire egalement competentes territoires autonomes assemblee
generale conseil comite novembre decembre janvier fevrier avril juin juillet
septembre octobre adoptee mondiale examine informer presenter maintenir
""".split())
_FR_TOKEN = re.compile(r"[a-zà-ÿ']+")


def _french_line(text: str) -> bool:
    toks = _FR_TOKEN.findall(text.lower())
    if len(toks) < 4:
        return False
    return sum(1 for t in toks if t in _FRENCH_STOPWORDS) >= 3


def words(text: str | None) -> Counter:
    if not text:
        return Counter()
    return Counter(_TOKEN.findall(text.lower().translate(_STRIP)))


def _wordset(text: str) -> list[str]:
    return _TOKEN.findall(text.lower().translate(_STRIP))


# ---------------------------------------------------------------------------
# Ground truth: pdftotext -layout (independent of pymupdf)
# ---------------------------------------------------------------------------

def pdftotext_lines(path: Path) -> list[str]:
    # Default mode (NOT -layout): poppler emits reading order (column-by-column,
    # matching the parser) and rejoins soft hyphens — so the token stream aligns
    # with the parse and cross-line word fragments do not create false losses.
    out = subprocess.run([PDFTOTEXT, "-enc", "UTF-8", str(path), "-"],
                         capture_output=True, text=True, timeout=120)
    return [ln.strip() for ln in out.stdout.splitlines()
            if ln.strip() and not _french_line(ln)]


def _flat_tokens(lines: list[str]) -> list[str]:
    toks: list[str] = []
    for ln in lines:
        toks.extend(_wordset(ln))
    return toks


def _anchor_index(gt_toks: list[str], probe: list[str], lo: int, hi: int,
                  *, prefer_last: bool) -> int | None:
    """Best fuzzy position of `probe` in gt_toks[lo:hi]. Returns a token index or
    None. prefer_last keeps the LATEST best match (for the region-end anchor,
    where a repeated line like a meeting date recurs across neighbours)."""
    if not probe:
        return None
    w = len(probe)
    probe_set = set(probe)
    best_i, best_score = None, 0.0
    for i in range(lo, max(lo, hi - w + 1)):
        window = gt_toks[i:i + w]
        score = len(probe_set & set(window)) / w
        if score > best_score or (prefer_last and score == best_score and score > 0):
            best_score, best_i = score, i
    return best_i if best_score >= 0.5 else None


def region_ground_truth(path: Path, doc: dict) -> tuple[Counter, Counter, int]:
    """Return (in_region_words, out_region_words, n_region_tokens).

    Restricts the pdftotext ground truth to the cropped target region by anchoring
    the parse's first and last distinctive content elements in the (reading-order)
    pdftotext token stream. Words outside the region — neighbour resolutions, the
    French column, running headers — are the expected crop-loss category."""
    gt_toks = _flat_tokens(pdftotext_lines(path))
    # Anchorable elements: skip the bare opening formula (not distinctive) and the
    # appended footnotes (may belong to a neighbour). Keep the number heading.
    anchorable = [e for e in doc.get("elements", [])
                  if e.get("type") in ("title", "frontmatter", "paragraph", "heading",
                                        "signature", "vote_record")
                  and len(_wordset(e.get("text") or "")) >= 3]
    if not anchorable or not gt_toks:
        return Counter(gt_toks), Counter(), len(gt_toks)

    gt_set = set(gt_toks)

    def _probe(text: str, from_end: bool) -> list[str]:
        # Anchor only on tokens PRESENT in the (French-filtered) ground truth, so a
        # French-tailed bilingual line or an OCR-only variant cannot break anchoring.
        ws = [t for t in _wordset(text) if t in gt_set]
        if len(ws) < 3:
            ws = _wordset(text)  # fall back to raw tokens if nothing survives
        return ws[-9:] if from_end else ws[:9]

    first_probe = _probe(anchorable[0].get("text", ""), from_end=False)
    start = _anchor_index(gt_toks, first_probe, 0, len(gt_toks), prefer_last=False) or 0
    # End anchor: walk anchorable elements from the LAST backward and take the
    # furthest one that still anchors AFTER start. Neighbouring resolutions are
    # often adopted at the SAME plenary meeting, so 'prefer last' would jump past
    # the target; and if the very last element cannot be located (OCR/French tail),
    # defaulting to end-of-document would swallow the next resolution — hence the
    # walk-back, with a token-count bound as the final fallback.
    end: int | None = None
    for e in reversed(anchorable):
        probe = _probe(e.get("text", ""), from_end=True)
        idx = _anchor_index(gt_toks, probe, start, len(gt_toks), prefer_last=False)
        if idx is not None:
            end = min(len(gt_toks), idx + len(probe))
            break
    if end is None:
        ntok = sum(len(_wordset(e.get("text", ""))) for e in anchorable)
        end = min(len(gt_toks), start + int(ntok * 1.4) + 10)
    if end <= start:
        start, end = 0, len(gt_toks)
    in_region = Counter(gt_toks[start:end])
    out_region = Counter(gt_toks[:start]) + Counter(gt_toks[end:])
    return in_region, out_region, end - start


# ---------------------------------------------------------------------------
# Parsed-side words
# ---------------------------------------------------------------------------

def parsed_words(doc: dict) -> Counter:
    c: Counter = Counter()
    for e in doc.get("elements", []):
        c += words(e.get("text"))
        if e.get("prefix"):
            c += words(e["prefix"])
        for lst in (e.get("vote") or {}).values():
            for name in lst:
                c += words(name)
    return c


def genuine_loss(in_region: Counter, pw: Counter, out_region: Counter,
                 symbol_tokens: frozenset[str]) -> tuple[Counter, int]:
    """In-region words missing from the parse, minus benign classes.

    Returns (genuine_loss, crop_loss) where crop_loss is the count of missing
    tokens that ALSO appear out-of-region (i.e. explained by the crop / dedup of a
    running header or the French column), reported separately, not as a failure."""
    missing = in_region - pw
    genuine: Counter = Counter()
    crop_loss = 0
    for tok, cnt in missing.items():
        if tok.isdigit() or len(tok) <= 2:
            continue
        if tok in symbol_tokens:
            continue
        if out_region.get(tok, 0) > 0:
            crop_loss += cnt   # same token lives outside the region: crop artefact
            continue
        genuine[tok] = cnt
    return genuine, crop_loss


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def fetch_targets(limit: int | None, symbols: list[str] | None):
    sql = ("SELECT symbol_normalized, archive_path, error "
           "FROM digitallibrary.document_files "
           "WHERE format='pdf' AND status IN ('extracted','parsed') ")
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
        return cur.fetchall()


def _klass(error: str | None) -> str:
    if error and "class=" in error:
        m = re.search(r"class=(\w+)", error)
        if m:
            return m.group(1)
    return "text"


def main() -> int:
    ap = argparse.ArgumentParser(description="Acceptance gate: PDF->parsed text preservation.")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--parsed-dir", type=Path, default=PARSED_DIR)
    ap.add_argument("--max-loss", type=int, default=5,
                    help="small absolute in-region genuine-loss tolerance, any class (default 5) "
                         "— covers a handful of OCR letter-substitutions in a small doc")
    ap.add_argument("--min-preserved", type=float, default=95.0,
                    help="in-region preservation %% a 'text' doc must reach to pass (default 95)")
    ap.add_argument("--poor-min-preserved", type=float, default=85.0,
                    help="looser preservation %% bar for 'poor'-class OCR docs (default 85)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    targets = fetch_targets(args.limit, args.symbols)
    print(f"PDF acceptance gate: {len(targets)} extracted docs; parsed_dir={args.parsed_dir}")

    n_pass = n_fail = n_skip = 0
    tot_region = tot_genuine = tot_crop = 0
    failures: list[tuple[str, str, Counter]] = []

    for symbol, rel, error in targets:
        klass = _klass(error)
        pj = args.parsed_dir / f"{sanitize_symbol(symbol)}.json"
        pdf = (ARCHIVE_ROOT / rel) if rel else None
        if not pj.exists() or not pdf or not pdf.exists():
            print(f"  SKIP  {symbol:<22} [{klass}] missing parsed JSON or PDF")
            n_skip += 1
            continue
        try:
            doc = json.loads(pj.read_text(encoding="utf-8"))
            in_region, out_region, n_region = region_ground_truth(pdf, doc)
            pw = parsed_words(doc)
        except Exception as exc:
            print(f"  FAIL  {symbol:<22} [{klass}] {type(exc).__name__}: {exc}")
            n_fail += 1
            failures.append((symbol, klass, Counter({f"<{type(exc).__name__}>": 1})))
            continue

        symtok = frozenset(_TOKEN.findall(symbol.lower().translate(_STRIP)))
        lost, crop_loss = genuine_loss(in_region, pw, out_region, symtok)
        n_lost = sum(lost.values())
        tot_region += n_region
        tot_genuine += n_lost
        tot_crop += crop_loss
        preserved = 100.0 * (n_region - n_lost) / max(n_region, 1)
        # Pass on EITHER a small absolute loss (a few OCR letter-substitutions in a
        # small doc) OR a high preservation fraction (distributed OCR noise across a
        # large doc). This gates real extraction drops, not inherent scan noise.
        bar = args.poor_min_preserved if klass == "poor" else args.min_preserved
        ok = n_lost <= args.max_loss or preserved >= bar

        if not ok:
            n_fail += 1
            failures.append((symbol, klass, lost))
            print(f"  FAIL  {symbol:<22} [{klass}] region={n_region} preserved={preserved:.2f}% "
                  f"genuine_lost={n_lost} crop_loss={crop_loss} "
                  f"{dict(sorted(lost.items(), key=lambda x: -x[1])[:8])}")
        else:
            n_pass += 1
            if args.verbose:
                print(f"  pass  {symbol:<22} [{klass}] region={n_region} "
                      f"preserved={preserved:.2f}% genuine_lost={n_lost} crop_loss={crop_loss}")

    print("\n" + "=" * 64)
    print(f"pass={n_pass}  FAIL={n_fail}  skip={n_skip}")
    print(f"in-region words={tot_region}  genuine lost={tot_genuine}  "
          f"crop-loss (expected, outside target)={tot_crop}  "
          f"aggregate in-region preserved={100.0*(tot_region-tot_genuine)/max(tot_region,1):.3f}%")
    if failures:
        print(f"\n{len(failures)} doc(s) failed:")
        for sym, kl, lost in failures:
            print(f"  {sym:<22} [{kl}] {dict(sorted(lost.items(), key=lambda x:-x[1])[:12])}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
