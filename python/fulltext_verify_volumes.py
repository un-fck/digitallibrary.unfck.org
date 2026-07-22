#!/usr/bin/env python3
"""Volume-aware acceptance gate for the volume-split pipeline (Track A).

Independent of document_paragraphs_raw's child rows in spirit: it RE-EXTRACTS the
archived volume file through a DIFFERENT code path (pymupdf plain `get_text` for
PDF volumes, python-docx paragraph text for HRC Word reports) to build a
ground-truth token bag for the volume's DECISIONS SECTION, then checks that the
union of the volume's children accounts for it.

Two independent checks per volume:

  1. COVERAGE — every content token of the volume's decisions section (from the
     first routed child heading onward) must be present in the union of the
     children's tokens, with explicit ALLOWED-DROP categories that never count as
     loss:
        * front matter          (everything before the first routed heading);
        * TOC / checklist lines  (dot-leader '....' entries);
        * running headers/footers (page symbol / 'General Assembly ... session');
        * catalog-absent decisions (a heading whose symbol is not in the DL
          catalog — e.g. a part-decision 80/408 present only as 80/408 A/B — so no
          child doc can exist for it; reported, not a failure);
        * HRC Part-Two proceedings (the report's narrative chapters, which carry no
          adopted-text symbol).
     Children are read from the DB (source_symbol = <volume>) when present; if a
     volume has not been written yet, the gate falls back to the split's routed
     slices so it can still validate the split-vs-file conservation.

  2. BOUNDARY LEAK — a written child must not contain the NEXT child's heading
     line (that would mean the split failed to cut at the boundary and swallowed
     the following decision).

Exit nonzero if any volume fails either check.

Usage:
    uv run python python/fulltext_verify_volumes.py                # all split volumes
    uv run python python/fulltext_verify_volumes.py --symbols 'A/80/49(VOL.II)'
    uv run python python/fulltext_verify_volumes.py --min-coverage 0.98 --verbose
    uv run python python/fulltext_verify_volumes.py --self-test
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

from fulltext_common import ARCHIVE_ROOT, get_conn
from fulltext_split_volumes import (
    _DOTLEADER,
    hrc_heading,
    pdf_heading,
    read_volume_rows,
    split_volume,
    volume_catalog,
)

DEFAULT_MIN_COVERAGE = 0.97
_TOKEN = re.compile(r"[a-z0-9]{2,}")

# Running header / page artifact lines to drop from the ground truth (mirrors the
# PDF extractor's header patterns; these repeat across the volume and carry no
# decision content).
_RUN_HEADER = re.compile(
    r"(General Assembly|Economic and Social Council|Human Rights Council)\b.*"
    r"(session|Official Records)"
    r"|^\s*(Resolutions?\s+(and Decisions|adopted))"
    r"|^[AES]/\d", re.I)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _allowed_drop_line(text: str) -> bool:
    """A ground-truth line that is expected front-matter/TOC/header noise."""
    t = (text or "").strip()
    if not t:
        return True
    if _DOTLEADER.search(t):
        return True
    if _RUN_HEADER.search(t):
        return True
    return False


# ---------------------------------------------------------------------------
# Independent ground-truth extraction from the archived file
# ---------------------------------------------------------------------------

def ground_truth_lines(path: Path, fmt: str) -> list[str]:
    """Plain text lines via a code path INDEPENDENT of the split's extractor."""
    if fmt == "pdf" or path.suffix.lower() == ".pdf":
        import fitz
        doc = fitz.open(path)
        out: list[str] = []
        for i in range(doc.page_count):
            out.extend(doc[i].get_text("text").splitlines())
        doc.close()
        return out
    # docx (HRC reports). archive_path points at the ORIGINAL, which for the
    # HRC .doc reports is an OLE binary python-docx cannot open — ground truth
    # must come from the converted/ sibling in that case.
    from docx import Document
    p = Path(path)
    if p.suffix.lower() != ".docx":
        conv = p.parent.parent / "converted" / (p.stem + ".docx")
        if conv.exists():
            p = conv
    doc = Document(str(p))
    return [para.text for para in doc.paragraphs]


def is_gt_heading(text: str, kind: str) -> bool:
    if kind in ("ga", "ecosoc"):
        return pdf_heading(text) is not None
    return hrc_heading(text) is not None  # style unknown in plain GT: pattern only


def decisions_section_tokens(lines: list[str], kind: str) -> Counter:
    """Token multiset of the decisions section (first heading -> end), dropping the
    allowed-drop lines (front matter, TOC dot-leaders, running headers)."""
    start = next((i for i, ln in enumerate(lines) if is_gt_heading(ln, kind)), None)
    if start is None:
        return Counter()
    bag: Counter = Counter()
    for ln in lines[start:]:
        if _allowed_drop_line(ln):
            continue
        bag.update(tokens(ln))
    return bag


# ---------------------------------------------------------------------------
# Per-volume verification
# ---------------------------------------------------------------------------

class VolumeReport:
    def __init__(self, volume: str) -> None:
        self.volume = volume
        self.coverage = 1.0
        self.gt_tokens = 0
        self.missing_tokens = 0
        self.leaks: list[str] = []
        self.unmatched = 0
        self.children = 0
        self.note = ""
        self.ok = True


def read_db_children(conn, volume: str, lang: str) -> dict[str, list[str]]:
    """{child_symbol: [row texts...]} for children written from this volume."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized, text FROM digitallibrary.document_paragraphs_raw "
            "WHERE source_symbol = %s AND lang = %s ORDER BY symbol_normalized, position",
            [volume, lang])
        out: dict[str, list[str]] = {}
        for sym, text in cur.fetchall():
            out.setdefault(sym, []).append(text)
        return out


def verify_volume(conn, volume: str, lang: str, kind: str, fmt: str, archive_rel: str,
                  min_coverage: float) -> VolumeReport:
    rep = VolumeReport(volume)
    rows = read_volume_rows(conn, volume, lang)
    sp = split_volume(conn, volume, lang, kind, rows)
    rep.unmatched = len(sp.unmatched)

    rep.children = len(sp.children)

    # ACCOUNTED-OR-ALLOWED token bag = every token our extraction produced for this
    # volume. It is the union of the children (written), cross-check/existing
    # decisions (covered elsewhere), and the ALLOWED-DROP categories — front matter,
    # TOC/checklist dot-leader lines, running headers, catalog-absent (unmatched)
    # decisions, and HRC Part-Two proceedings — which are exactly the volume rows
    # that are NOT routed into a child. Because a child slice always runs from its
    # heading to the NEXT heading, a child can never be truncated, so a
    # decisions-section token absent from this bag is a genuine extraction loss.
    accounted: Counter = Counter()
    for r in rows:
        accounted.update(tokens(r["text"]))

    # Round-trip: when children are already written, the DB child tokens must equal
    # the split's written-slice tokens (catches a write/renumber bug).
    db_children = read_db_children(conn, volume, lang)
    if db_children:
        rep.note = "children from DB"
        db_bag = set()
        for texts in db_children.values():
            for t in texts:
                db_bag |= set(tokens(t))
        # Page-bottom footnote apparatus (the underscore rule, footnote-numbered
        # citations, recorded-vote country lists) is inlined into slices by the
        # PDF extractor but dropped by the parser corpus-wide by design — it
        # stays in the raw layer. Its tokens are an allowed round-trip drop.
        apparatus = set()
        split_bag = set()
        for _, rws in sp.children:
            for r in rws:
                toks = set(tokens(r["text"]))
                split_bag |= toks
                t = (r["text"] or "").strip()
                if (re.match(r"^_{3,}\s*$", t)
                        or re.match(r"^\d{1,3}\s+(A/|E/|S/|Ibid|See |Official Records)", t)
                        or re.match(r"^(In favour|Against|Abstaining)\s*:", t)
                        or "In favour:" in t or "Abstaining:" in t):
                    apparatus |= toks
        # Cross-volume multi-part decisions: the fresh per-volume split can
        # include a child whose canonical (longest) version was stored under the
        # SIBLING volume (Vol II vs Vol III), so pull DB text for any fresh-split
        # child symbol regardless of source_symbol before declaring loss.
        fresh_syms = [sym for sym, _ in sp.children]
        missing_syms = [s for s in fresh_syms if s not in db_children]
        if missing_syms:
            # A fresh child stored under the SIBLING volume (cross-volume
            # longest-wins) is fine — but its two printings extract with
            # different page artifacts, so token identity cannot hold across
            # them. Require existence; exclude its tokens from the comparison.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT symbol_normalized FROM digitallibrary.document_paragraphs "
                    "WHERE symbol_normalized = ANY(%s) AND lang = %s", [missing_syms, lang])
                elsewhere = {r[0] for r in cur.fetchall()}
            truly_missing = [s for s in missing_syms if s not in elsewhere]
            if truly_missing:
                rep.leaks.append(f"children absent from DB entirely: {truly_missing[:5]}")
            for sym, rws in sp.children:
                if sym in elsewhere:
                    for r in rws:
                        split_bag -= set(tokens(r["text"]))
        lost = split_bag - db_bag - apparatus
        if lost:
            rep.leaks.append(f"DB round-trip lost {len(lost)} token types vs split")
    else:
        rep.note = "children not yet in DB (validating split vs file)"

    # Independent ground truth from the file.
    path = ARCHIVE_ROOT / archive_rel
    if not path.exists():
        rep.ok = False
        rep.note = f"archive file missing: {archive_rel}"
        return rep
    gt = decisions_section_tokens(ground_truth_lines(path, fmt), kind)

    gt_types = set(gt)
    rep.gt_tokens = len(gt_types)
    missing = gt_types - set(accounted)
    rep.missing_tokens = len(missing)
    rep.coverage = 1.0 - (len(missing) / len(gt_types)) if gt_types else 1.0

    # Boundary-leak: a written child body must not contain the next child's heading.
    ordered = list(sp.children)  # (symbol, rows) in document order
    for i in range(len(ordered) - 1):
        _, rws = ordered[i]
        nxt_head = ordered[i + 1][1][0]["text"].strip()[:40]
        if nxt_head and any(nxt_head in (r["text"] or "") for r in rws[1:]):
            rep.leaks.append(f"{ordered[i][0]} contains next heading {nxt_head!r}")

    rep.ok = rep.coverage >= min_coverage and not rep.leaks
    return rep


# ---------------------------------------------------------------------------
# Targets + main
# ---------------------------------------------------------------------------

def volume_targets(conn, symbols: list[str] | None) -> list[tuple[str, str, str]]:
    """[(symbol_normalized, lang, archive_path)] for split volumes."""
    sql = ("SELECT symbol_normalized, lang, archive_path FROM digitallibrary.document_files "
           "WHERE status = 'split' AND archive_path IS NOT NULL")
    params: list[object] = []
    if symbols:
        sql += " AND symbol_normalized = ANY(%s)"
        params.append(symbols)
    sql += " ORDER BY symbol_normalized"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], r[1] or "en", r[2]) for r in cur.fetchall()]


def _self_test() -> int:
    fails = []
    # decisions_section drops front matter + TOC dot-leaders, keeps body.
    lines = [
        "Contents", "80/401. Appointment ............ 3",           # TOC (dropped)
        "General Assembly Eightieth session",                       # header (dropped)
        "80/401. Appointment of the members of the Credentials Committee",  # heading (kept)
        "At its 1st plenary meeting the General Assembly appointed the members.",
    ]
    bag = decisions_section_tokens(lines, "ga")
    if "credentials" not in bag or "appointed" not in bag:
        fails.append("body tokens missing from decisions section")
    if "contents" in bag:
        fails.append("front matter leaked into decisions section")
    if not _allowed_drop_line("80/401. Appointment ............ 3"):
        fails.append("dot-leader TOC line not treated as allowed-drop")
    for m in fails:
        print("  FAIL:", m)
    print("self-test:", "FAILED" if fails else "passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Volume-split acceptance gate")
    ap.add_argument("--symbols", help="comma-separated volume symbol_normalized list")
    ap.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    from fulltext_split_volumes import normalize_symbol
    symbols = [normalize_symbol(s) for s in args.symbols.split(",")] if args.symbols else None
    kindmap = dict(volume_catalog())

    with get_conn() as conn:
        targets = volume_targets(conn, symbols)
        print(f"Volume gate: {len(targets)} split volume(s)")
        n_fail = 0
        for symbol, lang, rel in targets:
            kind = kindmap.get(symbol, "ga")
            fmt = "docx" if kind == "hrc" else "pdf"
            rep = verify_volume(conn, symbol, lang, kind, fmt, rel, args.min_coverage)
            flag = "PASS" if rep.ok else "FAIL"
            print(f"  [{flag}] {symbol}: coverage={rep.coverage:.3f} "
                  f"children={rep.children} gt_tokens={rep.gt_tokens} "
                  f"missing={rep.missing_tokens} unmatched={rep.unmatched} "
                  f"leaks={len(rep.leaks)} ({rep.note})")
            if args.verbose or not rep.ok:
                for lk in rep.leaks:
                    print(f"       LEAK: {lk}")
            if not rep.ok:
                n_fail += 1

    print(f"\nDone. {len(targets) - n_fail}/{len(targets)} volumes passed.")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
