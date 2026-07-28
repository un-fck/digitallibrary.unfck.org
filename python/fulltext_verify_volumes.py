#!/usr/bin/env python3
"""Volume-aware acceptance gate for the volume-split pipeline (Track A).

It grades THE CHILDREN THAT WERE WRITTEN, against the archived volume FILE.

Until 2026-07-27 it graded a split it recomputed in memory: `accounted` was built
from the volume's OWN raw rows, so deleting every child, misattributing every
child, truncating a child, leaking a heading across a boundary or inserting
invented paragraphs all left coverage at exactly 0.993 and the gate at rc=0
(controls V-DEL-CHILDREN … V-FABRICATE). The split is still recomputed, but only
to CLASSIFY which volume rows are routed into a child and which are allowed
drops; every number the gate reports is now measured on the STORED rows.

Checks per volume
-----------------
 1. COVERAGE (file -> stored children). Ground truth is the volume file read
    through an independent code path (pymupdf `get_text` / python-docx). Every
    content token type of the decisions section must appear in
        stored children  ∪  cross-volume stored children  ∪  allowed-drop rows
    where the allowed drops are exactly the volume rows the split did NOT route
    into a child: front matter, TOC/checklist dot-leader lines, running headers,
    catalog-absent headings, cross-check (E/RES) slices, HRC Part-Two narrative.
    Deleting or truncating a stored child now MOVES this number.

 2. PER-CHILD CONSERVATION, both directions. For each routed child the stored
    token bag must equal the slice's: tokens missing = truncation/deletion,
    tokens extra = leak/fabrication.

 3. ORDER AND BOUNDARIES, positionally, over the STORED rows. Each stored child
    is located back in the volume's own row sequence; consecutive children must
    not overlap and a child's stored row count must equal its span. A stored row
    whose text does not occur in the volume extraction at all is fabricated.
    (The old test was `next_heading[:40] in child_text` over the RECOMPUTED
    split, so a stored one-sentence leak was invisible even in principle.)

 4. FABRICATION (stored -> file). Token types in the stored children that occur
    nowhere in the volume file, after the same enumerated allowances the PDF gate
    uses (bare numbers, <=2 chars, joins of a CONTIGUOUS run of file tokens).

 5. UNMATCHED HEADINGS. A printed decision heading the split could not route is a
    dropped decision. It fails the gate; it used to be printed and ignored.

 6. A volume with ledger status 'split' and NO stored children fails. The gate
    used to print "children not yet in DB" — a normal state — and pass.

Child fidelity  (`--children`)
------------------------------
The 3,590 volume-split children have `archive_path IS NULL`, so both text gates
skip them: 13.9% of the corpus was ungated. Their ground truth is the parent
volume's PRINTED RANGE — from the child's own printed heading to the next printed
heading — read out of the volume PDF with poppler (an extractor independent of
the pymupdf path that produced the rows). `--children` measures each child's
preservation and fabrication against that range.

Usage:
    uv run python python/fulltext_verify_volumes.py
    uv run python python/fulltext_verify_volumes.py --symbols 'A/80/49(VOL.II)'
    uv run python python/fulltext_verify_volumes.py --children
    uv run python python/fulltext_verify_volumes.py --self-test

Exit code: 0 iff at least one volume was checked and every check passed.
Checking zero volumes is a FAILURE.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
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
from fulltext_verify_pdf import (
    contiguous_joins,
    file_region,
    line_print_key,
    pdftotext_lines,
    symbol_print_key,
)

DEFAULT_MIN_COVERAGE = 0.97
DEFAULT_CHILD_MIN_PRESERVED = 95.0
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
    # must come from the converted/ sibling in that case. (This is a crash fix,
    # not a tolerance: without it the gate cannot read its ground truth at all.)
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
        self.coverage = 0.0
        self.gt_tokens = 0
        self.missing_tokens = 0
        self.problems: list[str] = []
        self.unmatched = 0
        self.children_split = 0
        self.children_db = 0
        self.cross_volume = 0
        self.invented = 0
        self.note = ""
        self.ok = False


def read_db_children(conn, volume: str, lang: str) -> dict[str, list[str]]:
    """{child_symbol: [row texts in stored order]} written from this volume."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized, text FROM digitallibrary.document_paragraphs_raw "
            "WHERE source_symbol = %s AND lang = %s ORDER BY symbol_normalized, position",
            [volume, lang])
        out: dict[str, list[str]] = {}
        for sym, text in cur.fetchall():
            out.setdefault(sym, []).append(text)
        return out


def read_sibling_children(conn, syms: list[str], volume: str, lang: str
                          ) -> dict[str, list[str]]:
    """Children of `syms` stored under a DIFFERENT volume (cross-volume
    multi-part decisions: `write_children` keeps the LONGEST version, so a child
    this volume also produces may legitimately live under its sibling).

    This is NOT an exemption: the rows must EXIST and their tokens are counted
    into `accounted`, so the file's coverage still has to be satisfied by
    something that is actually stored. The old rule instead SUBTRACTED the
    child's tokens from the comparison on mere existence, which excused a total
    deletion (control V-DEL-ONE-CHILD).
    """
    if not syms:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized, text FROM digitallibrary.document_paragraphs_raw "
            "WHERE symbol_normalized = ANY(%s) AND lang = %s "
            "AND source_symbol IS NOT NULL AND source_symbol <> %s "
            "ORDER BY symbol_normalized, position",
            [syms, lang, volume])
        out: dict[str, list[str]] = {}
        for sym, text in cur.fetchall():
            out.setdefault(sym, []).append(text)
        return out


def _bag(texts: list[str]) -> set[str]:
    b: set[str] = set()
    for t in texts:
        b |= set(tokens(t))
    return b


def verify_volume(conn, volume: str, lang: str, kind: str, fmt: str, archive_rel: str,
                  min_coverage: float) -> VolumeReport:
    rep = VolumeReport(volume)
    rows = read_volume_rows(conn, volume, lang)
    sp = split_volume(conn, volume, lang, kind, rows)
    rep.unmatched = len(sp.unmatched)
    rep.children_split = len(sp.children)

    path = ARCHIVE_ROOT / archive_rel
    if not path.exists():
        rep.problems.append(f"archive file missing: {archive_rel}")
        rep.note = "unverifiable"
        return rep

    db_children = read_db_children(conn, volume, lang)
    rep.children_db = len(db_children)
    if not db_children:
        rep.problems.append(
            "no children stored for a volume whose ledger status is 'split' — "
            "'not yet in DB' is legitimate only before the write")
        rep.note = "no stored children"

    fresh = {sym: rws for sym, rws in sp.children}
    absent = [s for s in fresh if s not in db_children]
    siblings = read_sibling_children(conn, absent, volume, lang)
    rep.cross_volume = len(siblings)

    # ---- 1. COVERAGE: file decisions section -> what is actually STORED -------
    routed_ids = {id(r) for _, rws in sp.children for r in rws}
    allowed_rows = [r for r in rows if id(r) not in routed_ids]
    accounted: set[str] = set()
    for texts in db_children.values():
        accounted |= _bag(texts)
    for texts in siblings.values():
        accounted |= _bag(texts)
    for r in allowed_rows:
        accounted |= set(tokens(r["text"]))

    gt_lines = ground_truth_lines(path, fmt)
    gt = decisions_section_tokens(gt_lines, kind)
    gt_types = set(gt)
    rep.gt_tokens = len(gt_types)
    if not gt_types:
        rep.problems.append("no decisions section found in the file — nothing to verify")
        rep.note = "empty ground truth"
        return rep
    missing = gt_types - accounted
    rep.missing_tokens = len(missing)
    rep.coverage = 1.0 - len(missing) / len(gt_types)

    # ---- 2. PER-CHILD CONSERVATION, both directions ---------------------------
    truncated: list[str] = []
    leaking: list[str] = []
    gone: list[str] = []
    for sym, rws in sp.children:
        split_bag = _bag([r["text"] for r in rws])
        if sym in db_children:
            stored_bag = _bag(db_children[sym])
        elif sym in siblings:
            # Cross-volume longest-wins: the sibling's stored version must be at
            # least as long as this volume's slice — the writer's own rule,
            # checked, not assumed.
            if len(siblings[sym]) < len(rws):
                truncated.append(f"{sym}(sibling shorter: {len(siblings[sym])}<{len(rws)})")
            continue
        else:
            gone.append(sym)
            continue
        lost = split_bag - stored_bag
        extra = stored_bag - split_bag
        if lost:
            truncated.append(f"{sym}(-{len(lost)})")
        if extra:
            leaking.append(f"{sym}(+{len(extra)})")
    if gone:
        rep.problems.append(f"{len(gone)} routed child(ren) not stored anywhere: {gone[:5]}")
    if truncated:
        rep.problems.append(f"{len(truncated)} child(ren) lost tokens vs their slice: "
                            f"{truncated[:5]}")
    if leaking:
        rep.problems.append(f"{len(leaking)} child(ren) carry tokens their slice does not: "
                            f"{leaking[:5]}")

    # ---- 3. ATTRIBUTION AND BOUNDARIES over the STORED rows -------------------
    index: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        index[(r["text"] or "").strip()].append(i)

    # (a) ATTRIBUTION: a stored child must OPEN with the printed heading of its
    #     own symbol. Moving every child's rows under the neighbouring decision's
    #     symbol (control V-MISATTRIB) leaves coverage and every token bag
    #     untouched; only this check sees it.
    misattributed: list[str] = []
    heading_pos: dict[str, int] = {}
    for sym, texts in db_children.items():
        want = symbol_print_key(sym)
        got = line_print_key((texts[0] or "").strip()) if texts else None
        if want is not None and got != want:
            misattributed.append(f"{sym}(opens with {got})")
        occ = index.get((texts[0] or "").strip(), [])
        if occ:
            heading_pos[sym] = occ[0]
    if misattributed:
        rep.problems.append(f"{len(misattributed)} child(ren) do not open with their own "
                            f"printed heading: {misattributed[:5]}")

    # (b) FABRICATED ROWS: a stored row whose text occurs nowhere in the volume's
    #     own extraction.
    orphan = [(sym, t) for sym, texts in db_children.items() for t in texts
              if (t or "").strip() and (t or "").strip() not in index]
    if orphan:
        rep.problems.append(
            f"{len(orphan)} stored child row(s) have text that does not occur in the "
            f"volume extraction at all: {[o[0] for o in orphan[:5]]}")

    # (c) BOUNDARY LEAK, positionally: every row of a child must live BEFORE the
    #     next child's heading in the volume's own row order. A one-sentence leak
    #     is invisible to the old `next_heading[:40] in child_text` substring test.
    order = sorted(heading_pos.items(), key=lambda kv: kv[1])
    for i, (sym, h) in enumerate(order):
        nxt = order[i + 1][1] if i + 1 < len(order) else len(rows)
        for t in db_children[sym][1:]:
            occ = index.get((t or "").strip())
            if occ and all(p >= nxt for p in occ):
                rep.problems.append(
                    f"{sym}: a stored row belongs after the next child's heading "
                    f"(row at {occ[0]} >= {nxt}) — boundary leak: {(t or '')[:60]!r}")
                break

    # ---- 4. FABRICATION: stored -> file ---------------------------------------
    file_toks = [t for ln in gt_lines for t in tokens(ln)]
    file_bag = set(file_toks)
    joins = contiguous_joins(file_toks)
    stored_types: set[str] = set()
    for texts in db_children.values():
        stored_types |= _bag(texts)
    inv = {t for t in stored_types - file_bag
           if not t.isdigit() and len(t) > 2 and t not in joins}
    rep.invented = len(inv)
    if inv:
        rep.problems.append(f"{len(inv)} stored token type(s) occur nowhere in the volume "
                            f"file: {sorted(inv)[:8]}")

    # ---- 5. UNMATCHED HEADINGS ------------------------------------------------
    if rep.unmatched:
        rep.problems.append(
            f"{rep.unmatched} printed decision heading(s) were not routed into any child "
            f"(dropped decisions): {sp.unmatched[:5]}")

    if rep.coverage < min_coverage:
        rep.problems.append(f"coverage {rep.coverage:.3f} < {min_coverage}")

    if not rep.note:
        rep.note = f"{rep.children_db} stored / {rep.children_split} routed"
    rep.ok = not rep.problems
    return rep


# ---------------------------------------------------------------------------
# Child fidelity: the parent volume's printed range is the child's ground truth
# ---------------------------------------------------------------------------

def child_texts(conn, sym: str, lang: str) -> tuple[list[str], str]:
    """(texts, layer) — the SEMANTIC layer when the child is parsed, else raw."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(prefix,'') || ' ' || COALESCE(text,'') "
            "FROM digitallibrary.document_paragraphs "
            "WHERE symbol_normalized = %s AND lang = %s ORDER BY position", [sym, lang])
        rows = [r[0] for r in cur.fetchall()]
        if rows:
            return rows, "parsed"
        cur.execute(
            "SELECT text FROM digitallibrary.document_paragraphs_raw "
            "WHERE symbol_normalized = %s AND lang = %s ORDER BY position", [sym, lang])
        return [r[0] for r in cur.fetchall()], "raw"


def verify_children(conn, volume: str, lang: str, fmt: str, archive_rel: str,
                    min_preserved: float, verbose: bool) -> tuple[int, int, list[str]]:
    """(n_pass, n_fail, failures) for one volume's stored children."""
    path = ARCHIVE_ROOT / archive_rel
    if not path.exists():
        return 0, 1, [f"{volume}: archive file missing"]
    if fmt == "pdf" or path.suffix.lower() == ".pdf":
        lines = pdftotext_lines(path)          # poppler: independent of pymupdf
    else:
        lines = ground_truth_lines(path, fmt)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT symbol_normalized FROM digitallibrary.document_paragraphs_raw "
            "WHERE source_symbol = %s AND lang = %s ORDER BY 1", [volume, lang])
        kids = [r[0] for r in cur.fetchall()]

    n_pass = n_fail = 0
    failures: list[str] = []
    for sym in kids:
        if symbol_print_key(sym) is None:
            n_fail += 1
            failures.append(f"{sym}: no printed-heading key derivable from the symbol")
            continue
        start, end, mode = file_region(lines, sym)
        if mode != "heading":
            n_fail += 1
            failures.append(f"{sym}: printed heading not found in {volume} ({mode}) — "
                            f"the child's ground truth cannot be located")
            continue
        gt = Counter(t for ln in lines[start:end] for t in tokens(ln))
        texts, layer = child_texts(conn, sym, lang)
        if not texts:
            n_fail += 1
            failures.append(f"{sym}: no stored content at all")
            continue
        have: Counter = Counter()
        for t in texts:
            have.update(tokens(t))
        n_region = sum(gt.values())
        lost = sum(c for tok, c in (gt - have).items()
                   if not tok.isdigit() and len(tok) > 2)
        if n_region == 0:
            n_fail += 1
            failures.append(f"{sym}: printed range is empty")
            continue
        pres = 100.0 * (n_region - lost) / n_region
        if pres < min_preserved:
            n_fail += 1
            failures.append(f"{sym}: preserved {pres:.1f}% of its printed range "
                            f"({n_region} tok, {lost} lost) [{layer}]")
        else:
            n_pass += 1
            if verbose:
                print(f"    pass  {sym:<22} preserved={pres:.1f}% region={n_region} [{layer}]")
    return n_pass, n_fail, failures


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

    # NEGATIVE CONTROLS for the attribution instrument.
    if line_print_key("78/401. Appointment of the members") != symbol_print_key("A/DEC/78/401"):
        fails.append("a child's own printed heading does not match its symbol key")
    if line_print_key("78/402. Something else") == symbol_print_key("A/DEC/78/401"):
        fails.append("attribution check cannot tell two neighbouring decisions apart")

    for m in fails:
        print("  FAIL:", m)
    print("self-test:", "FAILED" if fails else "passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Volume-split acceptance gate")
    ap.add_argument("--symbols", help="comma-separated volume symbol_normalized list")
    ap.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    ap.add_argument("--children", action="store_true",
                    help="grade each stored child against the parent volume's printed range")
    ap.add_argument("--child-min-preserved", type=float, default=DEFAULT_CHILD_MIN_PRESERVED)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    from fulltext_split_volumes import normalize_symbol
    symbols = [normalize_symbol(s) for s in args.symbols.split(",")] if args.symbols else None
    kindmap = dict(volume_catalog())

    n_fail = 0
    n_child_pass = n_child_fail = 0
    child_failures: list[str] = []
    with get_conn() as conn:
        targets = volume_targets(conn, symbols)
        print(f"Volume gate: {len(targets)} split volume(s)"
              + ("  [+ child fidelity]" if args.children else ""))
        for symbol, lang, rel in targets:
            kind = kindmap.get(symbol, "ga")
            # The archived extension is authoritative: a symbol missing from the
            # volume catalog used to default to kind='ga' -> fmt='pdf' and hand a
            # .doc file to pymupdf, which raised and killed the whole run. A
            # crashed checker prints nothing, and no verdict must never read as
            # "nothing was wrong".
            fmt = "pdf" if str(rel).lower().endswith(".pdf") else "docx"
            try:
                rep = verify_volume(conn, symbol, lang, kind, fmt, rel, args.min_coverage)
            except Exception as exc:
                rep = VolumeReport(symbol)
                rep.note = "crashed"
                rep.problems.append(f"{type(exc).__name__}: {exc}")
            flag = "PASS" if rep.ok else "FAIL"
            print(f"  [{flag}] {symbol}: coverage={rep.coverage:.3f} "
                  f"children={rep.children_db}/{rep.children_split} "
                  f"gt_tokens={rep.gt_tokens} missing={rep.missing_tokens} "
                  f"unmatched={rep.unmatched} invented={rep.invented} "
                  f"problems={len(rep.problems)} ({rep.note})")
            if args.verbose or not rep.ok:
                for p in rep.problems:
                    print(f"       - {p}")
            if not rep.ok:
                n_fail += 1
            if args.children:
                try:
                    cp, cf, cfails = verify_children(conn, symbol, lang, fmt, rel,
                                                     args.child_min_preserved, args.verbose)
                except Exception as exc:
                    cp, cf = 0, 1
                    cfails = [f"{symbol}: child gate crashed: {type(exc).__name__}: {exc}"]
                n_child_pass += cp
                n_child_fail += cf
                child_failures.extend(cfails)
                print(f"       children: {cp} pass / {cf} FAIL")

    print(f"\nDone. {len(targets) - n_fail}/{len(targets)} volumes passed.")
    if args.children:
        total = n_child_pass + n_child_fail
        print(f"Child fidelity: {n_child_pass}/{total} children preserve their printed range "
              f"(bar {args.child_min_preserved}%)")
        for f in child_failures[:40]:
            print(f"  - {f}")
        if len(child_failures) > 40:
            print(f"  ... +{len(child_failures) - 40} more")
        if total == 0 and targets:
            print("FAIL: --children checked 0 children.")
            return 1

    if not targets:
        print("FAIL: the gate checked 0 volumes. A run that verified nothing must never "
              "be indistinguishable from a run that verified the corpus.")
        return 1
    return 1 if (n_fail or n_child_fail) else 0


if __name__ == "__main__":
    raise SystemExit(main())
