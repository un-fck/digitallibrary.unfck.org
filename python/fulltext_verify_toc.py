#!/usr/bin/env python3
"""TOC verification: compare each document's SELF-DECLARED heading structure
against the heading structure our parser produced (digitallibrary.document_paragraphs).

Motivation
----------
The text-preservation gate (fulltext_verify_text.py) proves *words* survive the
docx -> parsed pipeline. It says nothing about *structure*: a document can keep
every word yet lose its outline when a heading the author marked as a heading is
demoted to an ordinary paragraph. This tool audits exactly that, by extracting the
list of headings the document itself declares and checking each one against the
parsed elements.

Self-declared structure sources (priority order per doc)
--------------------------------------------------------
For docx (native .docx or LibreOffice-converted doc/wpd):
  1. bookmark  - w:bookmarkStart whose name starts '_Toc' marks a heading TARGET;
                 the paragraph that contains it is a self-declared heading. This is
                 the strongest signal and works even with no visible TOC page.
  2. tocfield  - a Word TOC field (w:fldSimple instr contains 'TOC', or an
                 instrText/fldChar 'TOC' block); its entry lines / _Toc hyperlink
                 lines are the declared headings.
  3. contents  - a 'Contents'/'Table of Contents' paragraph followed by short lines
                 carrying trailing page numbers or dot leaders.
  4. style     - paragraphs whose Word paragraph style is a heading style (the UN
                 house styles H1/H2/H23/H4.., HCh, or standard Heading1-9, and the
                 numbered-title style TitleH1). Front-matter styles (session line,
                 agenda item/title, "Resolution adopted by...", committee-reference
                 brackets) are excluded so they don't masquerade as body headings.
  5. bold      - bold run-in headings: a whole-paragraph-bold line in a body style
                 that begins with a structural label (Action/Goal/Article/Annex/...)
                 or is a short title-case fragment. Catches e.g. the Pact for the
                 Future's 56 bold "Action N." commitments, which use the body style.
For pdf:
  6. outline   - pymupdf doc.get_toc() bookmarks.
  7. contents  - same 'Contents' heuristic run over the first pages' text.

NOTE (empirical): across this corpus the ODS Word files essentially never carry
Word TOC fields, _Toc bookmarks, or Contents pages, and UN PDFs have no outline;
the workhorse signal is the paragraph *style* source (+ bold run-ins). The tool
still implements and reports all sources so coverage is measured, not assumed.

Matching
--------
Each declared heading is normalised (lowercase, collapse whitespace, strip dot
leaders / trailing page numbers / a leading enumerator such as "79/1.", "I.",
"Goal 1.", "(a)") and matched against the parsed elements:
  MATCHED             - a heading/title element matches (containment with comparable
                        length, or difflib ratio > 0.75).
  MISCLASSIFIED (paragraph)
                      - it matches only a non-heading element (the parser demoted a
                        real heading to body text).
  SPLIT/MERGED        - it appears only as a substring of a much longer element, or
                        across two adjacent elements (heading fused/fragmented).
  MISSING             - not found anywhere.

Output
------
Per-doc verdict lines, an overall summary (docs with self-declared structure /
fully matched / with misses) and per-source coverage, plus a TSV at
<archive>/audit/toc_check.tsv with (symbol, check, severity, detail) rows,
matching the invariants-TSV column convention (severity: error for MISSING /
MISCLASSIFIED, warn for SPLIT/MERGED).

Conventions mirror the other python/ scripts: standalone, DATABASE_URL from .env
via fulltext_common, short-lived psycopg (v3) connection, no new tables, no writes
to the database.

Usage:
    uv run python python/fulltext_verify_toc.py                 # audit-set if present, else defaults
    uv run python python/fulltext_verify_toc.py --limit 40
    uv run python python/fulltext_verify_toc.py --symbols A/RES/79/1 A/RES/70/1
    uv run python python/fulltext_verify_toc.py --audit-set /path/audit_set.json --verbose
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import lxml.etree as ET

from fulltext_common import ARCHIVE_ROOT, get_conn

try:
    import fitz  # PyMuPDF, for PDF outlines
except Exception:  # pragma: no cover - optional
    fitz = None

# ---------------------------------------------------------------------------
# WordprocessingML namespace shortcuts
# ---------------------------------------------------------------------------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
QP = W + "p"
QR = W + "r"
QT = W + "t"
QPPR = W + "pPr"
QRPR = W + "rPr"
QSTYLE = W + "pStyle"
QB = W + "b"
QBOOKMARK = W + "bookmarkStart"
QFLDSIMPLE = W + "fldSimple"
QINSTRTEXT = W + "instrText"
QHYPERLINK = W + "hyperlink"

AUDIT_DIR = ARCHIVE_ROOT / "audit"
DEFAULT_AUDIT_SET = AUDIT_DIR / "audit_set.json"
TSV_OUT = AUDIT_DIR / "toc_check.tsv"
REPO_ROOT = Path(__file__).resolve().parent.parent
# Checked-in, triaged findings. Before 2026-07-27 this gate exited 1 on pristine
# correct input (6 misclassified + 2 split on A/RES/79/1 — findings its own
# docstring calls expected), so its exit code carried no information and nobody
# could gate on it. It is now green on the corpus as triaged and red on MOVEMENT.
BASELINE = REPO_ROOT / "docs" / "_baselines" / "toc-baseline.tsv"

# Validation / calibration anchors (always checked when no audit set / symbols given).
KNOWN_SYMBOLS = [
    "A/RES/79/1",       # Pact for the Future: 56 bold "Action N." -> should be misses
    "A/RES/70/1",       # 2030 Agenda: Goal/section structure largely missing
    "A/RES/79/226",     # QCPR: section titles split from roman numerals
    "A/RES/77/230",     # ordinary HR resolution -> few/zero findings (calibration)
    "S/RES/2803(2025)", # ordinary SC resolution -> few/zero findings (calibration)
]

# ---------------------------------------------------------------------------
# Style classification (UN house styles + standard Word)
# ---------------------------------------------------------------------------

# Body heading styles: H1, H2, H23, H3..H6 (UN combined levels), HCh/HCH, Heading1-9,
# and a few section/article heading styles seen in reports.
_HEADING_STYLE = re.compile(r"^(H\d+|HCh|HCH|Heading\d+|ArtHead|SectionHead|ChapterHead)$", re.I)
# The numbered resolution-title style ("79/1.The Pact for the Future") -> title level.
_TITLE_STYLE = re.compile(r"^(TitleH1|Title\d*)$", re.I)
# Front-matter title-ish styles that must NOT be treated as body headings.
_FRONTMATTER_STYLE = re.compile(r"^(TitleHC[hH]|AgendaTitle.*|Session.*|Distr.*)$", re.I)

# Structural labels that mark a bold run-in heading even in a body style.
_LABEL_RE = re.compile(
    r"^(action|goal|article|annex|appendix|chapter|section|part|target|principle|"
    r"rule|objective|pillar|phase|step|figure|table)\s+[\dIVXLCivxlc]+\b",
    re.I,
)

# Front-matter text patterns to skip regardless of style.
_FRONTMATTER_TEXT = re.compile(
    r"^(\[.*\]$|agenda item\b|distr\b|resolution adopted by\b|adopted by the\b|"
    r"[A-Z]/(RES|PRST|DEC)/|seventy-|sixty-|fifty-|forty-|"
    r"\d{1,2}\s+(january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+\d{4}$)",
    re.I,
)

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_WS = re.compile(r"[\s ]+")
# A leading enumerator: "79/1.", "I.", "IV", "Goal 1.", "Action 12.", "(a)", "1.", "1)"
_ENUM = re.compile(
    r"^\s*("
    r"\d+/\d+\.?"                                              # resolution number 79/1.
    r"|(?:action|goal|article|annex|appendix|chapter|section|part|target|"
    r"principle|rule|objective|pillar|phase|step)\s+[\dIVXLCivxlc]+[.:)]?"  # Goal 1.
    r"|[IVXLC]{1,6}[.:)]"                                      # roman with delimiter  IV.
    r"|\d+[.:)]"                                               # arabic  12.
    r"|\([a-z0-9]{1,3}\)"                                      # (a) (iv)
    r")\s+",
    re.I,
)
# Trailing dot leaders + page number ("Introduction .... 5").
_TRAIL_PAGE = re.compile(r"[\s.…·]{2,}\d{1,4}\s*$")


def norm(text: str | None) -> str:
    """Full normalisation: lowercase, strip page numbers/dot leaders, collapse ws."""
    if not text:
        return ""
    t = _WS.sub(" ", text).strip()
    t = _TRAIL_PAGE.sub("", t).strip()
    return t.lower().strip(" .–—…-:")


def norm_stripped(text: str | None) -> str:
    """Normalisation that also removes a single leading enumerator.

    Falls back to the un-stripped form when stripping would empty the string
    (e.g. a bare roman numeral "I"), so pure enumerators still match the parser's
    equally-bare enumerator headings."""
    base = norm(text)
    stripped = _ENUM.sub("", base).strip(" .–—…-:")
    return stripped if stripped else base


# ---------------------------------------------------------------------------
# docx helpers
# ---------------------------------------------------------------------------

def _ptext(p: ET.Element) -> str:
    return "".join(t.text or "" for t in p.iter(QT))


def _pstyle(p: ET.Element) -> str:
    pPr = p.find(QPPR)
    if pPr is None:
        return ""
    ps = pPr.find(QSTYLE)
    return (ps.get(W + "val") or "") if ps is not None else ""


def _bool_on(el: ET.Element | None) -> bool:
    """A boolean toggle property (w:b) is 'on' unless val is 0/false/off."""
    if el is None:
        return False
    val = el.get(W + "val")
    return val not in ("0", "false", "off")


def _whole_bold(p: ET.Element) -> bool:
    """True iff every non-empty run in the paragraph is bold."""
    saw = False
    for r in p.iter(QR):
        if not "".join(t.text or "" for t in r.iter(QT)).strip():
            continue
        saw = True
        rpr = r.find(QRPR)
        if rpr is None or not _bool_on(rpr.find(QB)):
            return False
    return saw


@dataclass
class Declared:
    text: str
    source: str
    level: int | None = None


_SRC_TOKEN = re.compile(r"[a-z0-9]+")


def _src_tokens(text: str) -> list[str]:
    return _SRC_TOKEN.findall((text or "").lower())


def docx_source_text(path: Path) -> str:
    """Every paragraph of the docx, concatenated.

    Ground truth for the REVERSE direction: a parsed heading whose word sequence
    occurs nowhere in the source document was invented by the parser. Inserting
    three fabricated headings into the parse used to leave every one of this
    gate's finding counts identical (control C-FABRICATE).
    """
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except Exception:
        return ""
    body = root.find(W + "body")
    if body is None:
        return ""
    return " ".join(_ptext(p) for p in body.iter(QP))


def pdf_source_text(path: Path) -> str:
    if fitz is None:
        return ""
    try:
        doc = fitz.open(path)
    except Exception:
        return ""
    try:
        return " ".join(doc[i].get_text("text") for i in range(doc.page_count))
    finally:
        doc.close()


def source_signature(text: str) -> str:
    """Letters and digits only, whitespace and punctuation removed.

    Word/OOXML splits a line into runs at arbitrary points ('Reinvigorat'+'ing')
    and fuses others ('II'+'Reinvigorating' in one paragraph), so neither a
    character substring of the rendered text nor a word-sequence match is
    reliable. Collapsing both sides to their letters makes the comparison immune
    to every one of those, while a sentence that was never in the document still
    cannot be found. Measured on the 100-document audit set: 8 false positives
    with a raw substring test, 13 with word-sequence matching, 0 with this.
    """
    return "".join(_SRC_TOKEN.findall((text or "").lower()))


def occurs_in_source(heading: str, src_signature: str) -> bool:
    sig = source_signature(heading)
    if len(sig) < 20:
        return True                      # too little evidence either way
    return sig in src_signature


def _extract_docx(path: Path) -> tuple[list[Declared], set[str]]:
    """Return (declared headings in document order, set of source tags present)."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    if body is None:
        return [], set()
    paras = [p for p in body.iter(QP)]

    out: list[Declared] = []
    sources: set[str] = set()

    # 1. _Toc bookmark targets (strongest).
    for p in paras:
        names = [b.get(W + "name") or "" for b in p.iter(QBOOKMARK)]
        if any(n.startswith("_Toc") for n in names):
            t = _ptext(p).strip()
            if t:
                out.append(Declared(t, "bookmark"))
                sources.add("bookmark")

    # 2. TOC field entries: fldSimple with instr TOC, or paragraphs whose hyperlink
    #    anchors point at _Toc bookmarks (the visible TOC lines), or instrText TOC.
    has_toc_field = any(
        "TOC" in (f.get(W + "instr") or "").upper() for f in root.iter(QFLDSIMPLE)
    ) or any("TOC" in (i.text or "").upper() for i in root.iter(QINSTRTEXT))
    if has_toc_field:
        sources.add("tocfield")
        for f in root.iter(QFLDSIMPLE):
            if "TOC" in (f.get(W + "instr") or "").upper():
                t = "".join(x.text or "" for x in f.iter(QT)).strip()
                if t:
                    out.append(Declared(t, "tocfield"))
        for p in paras:
            if any((h.get(W + "anchor") or "").startswith("_Toc") for h in p.iter(QHYPERLINK)):
                t = _ptext(p).strip()
                if t:
                    out.append(Declared(t, "tocfield"))

    # 3. Contents-page heuristic.
    lines = [_ptext(p).strip() for p in paras]
    for d in _contents_entries(lines):
        out.append(Declared(d, "contents"))
        sources.add("contents")

    # 4. Heading styles + numbered-title style.
    for p in paras:
        st = _pstyle(p)
        if not st or _FRONTMATTER_STYLE.match(st):
            continue
        t = _ptext(p).strip()
        if not t or _FRONTMATTER_TEXT.match(t):
            continue
        if _TITLE_STYLE.match(st):
            out.append(Declared(t, "style", level=0))
            sources.add("style")
        elif _HEADING_STYLE.match(st):
            m = re.match(r"^H(\d)", st)
            lvl = int(m.group(1)) if m else (int(re.search(r"\d", st).group()) if re.search(r"\d", st) else 1)
            out.append(Declared(t, "style", level=lvl))
            sources.add("style")

    # 5. Bold run-in headings (whole-paragraph bold in a non-heading style).
    for p in paras:
        st = _pstyle(p)
        if _HEADING_STYLE.match(st) or _TITLE_STYLE.match(st):
            continue  # already captured by the style source
        t = _ptext(p).strip()
        if not t or _FRONTMATTER_TEXT.match(t) or not _whole_bold(p):
            continue
        labelled = bool(_LABEL_RE.match(t))
        short_titlecase = len(t) <= 60 and len(t.split()) <= 8 and not t.endswith(".")
        if labelled or short_titlecase:
            out.append(Declared(t, "bold"))
            sources.add("bold")

    return _dedup(out), sources


# ---------------------------------------------------------------------------
# pdf helpers
# ---------------------------------------------------------------------------

def _extract_pdf(path: Path) -> tuple[list[Declared], set[str]]:
    if fitz is None:
        return [], set()
    out: list[Declared] = []
    sources: set[str] = set()
    try:
        doc = fitz.open(path)
    except Exception:
        return [], set()
    try:
        for entry in doc.get_toc() or []:
            level, title = entry[0], (entry[1] or "").strip()
            if title:
                out.append(Declared(title, "outline", level=level))
                sources.add("outline")
        # Contents heuristic on the first pages' text lines.
        lines: list[str] = []
        for page in doc[: min(6, doc.page_count)]:
            lines.extend(ln.strip() for ln in page.get_text("text").splitlines())
        for d in _contents_entries(lines):
            out.append(Declared(d, "contents"))
            sources.add("contents")
    finally:
        doc.close()
    return _dedup(out), sources


# ---------------------------------------------------------------------------
# Contents-page heuristic (shared docx/pdf)
# ---------------------------------------------------------------------------

_CONTENTS_HDR = re.compile(r"^(table of )?contents$", re.I)


def _contents_entries(lines: list[str]) -> list[str]:
    """Lines forming a Contents block: a 'Contents' header followed by short entry
    lines that carry a trailing page number or dot leaders."""
    entries: list[str] = []
    n = len(lines)
    for i, ln in enumerate(lines):
        if not _CONTENTS_HDR.match(ln.strip()):
            continue
        gap = 0
        for j in range(i + 1, min(i + 200, n)):
            s = lines[j].strip()
            if not s:
                gap += 1
                if gap >= 4:
                    break
                continue
            gap = 0
            if len(s) > 160:
                break
            if _TRAIL_PAGE.search(s) or re.search(r"\s\d{1,4}$", s):
                entries.append(s)
        break
    return entries


def _dedup(items: list[Declared]) -> list[Declared]:
    seen: set[str] = set()
    out: list[Declared] = []
    for d in items:
        key = norm_stripped(d.text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Parsed side (DB)
# ---------------------------------------------------------------------------

@dataclass
class ParsedElem:
    type: str
    text: str
    nfull: str = ""
    nstrip: str = ""
    text_only: str = ""      # without the parser's prefix, for source lookup


HEADING_TYPES = {"heading", "title"}


def _fetch_parsed(conn, symbol: str) -> list[ParsedElem]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT type, COALESCE(prefix,'')||COALESCE(text,'') AS text, "
            "       COALESCE(text,'') AS text_only "
            "FROM digitallibrary.document_paragraphs "
            "WHERE symbol_normalized = %s ORDER BY position",
            [symbol],
        )
        rows = cur.fetchall()
    elems = []
    for typ, text, text_only in rows:
        elems.append(ParsedElem(typ, text or "", norm(text), norm_stripped(text),
                                text_only or ""))
    return elems


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _rel(d: str, e: str) -> str | None:
    """Relation of declared string d to parsed element string e.

    'match'     : (near-)equal or contained with comparable length, or ratio>0.75
    'substring' : d strictly contained in a much longer e (heading merged/fused)
    None        : unrelated
    """
    if not d or not e:
        return None
    if d == e:
        return "match"
    if d in e or e in d:
        ratio_len = min(len(d), len(e)) / max(len(d), len(e))
        return "match" if ratio_len >= 0.75 else "substring"
    if difflib.SequenceMatcher(None, d, e).ratio() > 0.75:
        return "match"
    return None


def _classify(d: Declared, elems: list[ParsedElem]) -> str:
    dn = norm_stripped(d.text)
    dn_full = norm(d.text)
    if not dn:
        return "MATCHED"  # nothing to check

    substring_hit = False
    for e in elems:
        for etext in (e.nstrip, e.nfull):
            r = _rel(dn, etext)
            if r is None and dn_full != dn:
                r = _rel(dn_full, etext)
            if r == "match":
                return "MATCHED" if e.type in HEADING_TYPES else "MISCLASSIFIED (paragraph)"
            if r == "substring":
                substring_hit = True

    # Split across two adjacent elements?
    for i in range(len(elems) - 1):
        combo = (elems[i].nstrip + " " + elems[i + 1].nstrip).strip()
        if _rel(dn, combo) in ("match", "substring"):
            return "SPLIT/MERGED"

    if substring_hit:
        return "SPLIT/MERGED"
    return "MISSING"


_SEVERITY = {
    "MISSING": "error",
    "MISCLASSIFIED (paragraph)": "error",
    "SPLIT/MERGED": "warn",
}


# ---------------------------------------------------------------------------
# Per-document run
# ---------------------------------------------------------------------------

@dataclass
class DocResult:
    symbol: str
    fmt: str
    sources: set[str] = field(default_factory=set)
    declared: int = 0
    matched: int = 0
    findings: list[tuple[str, Declared]] = field(default_factory=list)  # (verdict, declared)
    invented: list[str] = field(default_factory=list)   # parsed headings not in the source
    error: str | None = None


def _resolve_path(rel: str | None) -> Path | None:
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else (ARCHIVE_ROOT / rel)


def process(conn, symbol: str, fmt: str, rel: str | None) -> DocResult:
    res = DocResult(symbol, fmt)
    path = _resolve_path(rel)
    if not path or not path.exists():
        res.error = f"archive file missing ({rel})"
        return res
    try:
        if path.suffix.lower() == ".docx":
            declared, sources = _extract_docx(path)
        elif path.suffix.lower() == ".pdf" or fmt == "pdf":
            declared, sources = _extract_pdf(path)
        else:
            res.error = f"unsupported archive type ({path.suffix})"
            return res
    except Exception as exc:  # never let one bad file abort the run
        res.error = f"{type(exc).__name__}: {exc}"
        return res

    res.sources = sources
    res.declared = len(declared)

    elems = _fetch_parsed(conn, symbol)

    # REVERSE DIRECTION: a parsed heading/title whose text occurs nowhere in the
    # source document. Runs even for documents that declare no structure — the
    # declared-heading comparison is blind to structure the parser INVENTED.
    src = docx_source_text(path) if path.suffix.lower() == ".docx" else pdf_source_text(path)
    if src:
        sig = source_signature(src)
        for e in elems:
            if e.type not in HEADING_TYPES:
                continue
            if not occurs_in_source(e.text_only, sig):
                res.invented.append(e.text_only[:80])

    if not declared:
        return res

    for d in declared:
        verdict = _classify(d, elems)
        if verdict == "MATCHED":
            res.matched += 1
        else:
            res.findings.append((verdict, d))
    return res


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------

def _fetch_ledger(conn, symbols: list[str]) -> list[tuple[str, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized, format, COALESCE(converted_path, archive_path) "
            "FROM digitallibrary.document_files "
            "WHERE symbol_normalized = ANY(%s) AND status IN ('extracted','parsed')",
            [symbols],
        )
        by = {r[0]: (r[0], r[1], r[2]) for r in cur.fetchall()}
    return [by[s] for s in symbols if s in by]


def _fetch_random(conn, n: int, exclude: list[str]) -> list[tuple[str, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized, format, COALESCE(converted_path, archive_path) "
            "FROM digitallibrary.document_files "
            "WHERE status = 'parsed' AND NOT (symbol_normalized = ANY(%s)) "
            "ORDER BY md5(symbol_normalized) LIMIT %s",
            [exclude, n],
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def select_targets(conn, args) -> tuple[list[tuple[str, str, str]], str]:
    # Explicit --symbols always wins (targeted runs).
    if args.symbols:
        return _fetch_ledger(conn, args.symbols), f"--symbols ({len(args.symbols)})"
    audit_path = Path(args.audit_set) if args.audit_set else DEFAULT_AUDIT_SET
    # Poll once for the audit set (another agent may be writing it).
    if not audit_path.exists():
        time.sleep(2)
    if audit_path.exists():
        raw = json.loads(audit_path.read_text())
        syms = raw if isinstance(raw, list) else raw.get("symbols", [])
        syms = [s if isinstance(s, str) else s.get("symbol") for s in syms]
        targets = _fetch_ledger(conn, [s for s in syms if s])
        if args.limit:
            targets = targets[: args.limit]
        return targets, f"audit-set {audit_path} ({len(targets)} docs)"
    known = _fetch_ledger(conn, KNOWN_SYMBOLS)
    rand = _fetch_random(conn, 30, KNOWN_SYMBOLS)
    targets = known + rand
    if args.limit:
        targets = targets[: args.limit]
    return targets, f"{len(known)} known + {len(rand)} random parsed docs"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Verify self-declared TOC/heading structure vs parsed structure.")
    ap.add_argument("--audit-set", help=f"path to audit_set.json (default {DEFAULT_AUDIT_SET})")
    ap.add_argument("--symbols", nargs="*", help="restrict to these symbol_normalized values")
    ap.add_argument("--limit", type=int, help="cap number of documents checked")
    ap.add_argument("--verbose", action="store_true", help="print a line for every doc, incl. clean ones")
    ap.add_argument("--tsv", default=str(TSV_OUT), help=f"TSV output path (default {TSV_OUT})")
    ap.add_argument("--baseline", default=str(BASELINE),
                    help="checked-in triaged findings; the gate fails on MOVEMENT")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--max-undeclared", type=int, default=0,
                    help="documents whose source declares NO structure at all are "
                         "UNVERIFIABLE by this gate; more than this many fails it "
                         "(default 0 — the corpus-wide figure is ~99%%, so a "
                         "corpus-wide run is red until a structure source exists "
                         "for those documents)")
    args = ap.parse_args()

    conn = get_conn()
    conn.autocommit = True
    try:
        targets, desc = select_targets(conn, args)
        print(f"TOC verification: {desc}\n" + "=" * 72)

        results: list[DocResult] = []
        for symbol, fmt, rel in targets:
            res = process(conn, symbol, fmt, rel)
            results.append(res)
            _print_doc(res, args.verbose)
    finally:
        conn.close()

    _write_tsv(Path(args.tsv), results)
    _summary(results, Path(args.tsv))

    # ---------------- verdict ------------------------------------------------
    keys = _finding_keys(results)
    base_path = Path(args.baseline)
    if args.update_baseline:
        _write_baseline(base_path, keys)
        print(f"baseline rewritten: {base_path} ({len(keys)} triaged findings)")
        return 0

    known = _load_baseline(base_path)
    new = sorted(keys - known)
    checked = [r for r in results if r.error is None]
    undeclared = [r for r in checked if r.declared == 0]
    invented = [(r.symbol, t) for r in results for t in r.invented]
    errs = [r for r in results if r.error]

    print(f"\nbaseline                    : {len(known)} triaged finding(s) from {base_path}")
    print(f"NEW findings (untriaged)    : {len(new)}")
    print(f"unverifiable (no self-declared structure anywhere in the source): "
          f"{len(undeclared)}/{len(checked)}")
    print(f"parsed headings absent from the source document: {len(invented)}")
    for sym, t in invented[:12]:
        print(f"    INVENTED  {sym:<22} {t!r}")
    for sym, verdict, head in new[:12]:
        print(f"    NEW       {sym:<22} {verdict:<24} {head[:60]!r}")

    problems: list[str] = []
    if not results:
        problems.append("0 documents were checked")
    if errs:
        problems.append(f"{len(errs)} document(s) could not be read")
    if new:
        problems.append(f"{len(new)} untriaged structural finding(s)")
    if invented:
        problems.append(f"{len(invented)} parsed heading(s) occur nowhere in the source")
    if len(undeclared) > args.max_undeclared:
        problems.append(f"{len(undeclared)} document(s) declare no structure, so this "
                        f"gate cannot observe them (bar {args.max_undeclared})")
    if problems:
        print("\nFAIL: " + "; ".join(problems))
        return 1
    print("\nPASS")
    return 0


def _print_doc(res: DocResult, verbose: bool) -> None:
    if res.error:
        print(f"  ERR   {res.symbol:<24} [{res.fmt}] {res.error}")
        return
    if res.declared == 0:
        if verbose:
            print(f"  ----  {res.symbol:<24} [{res.fmt}] no self-declared structure")
        return
    src = ",".join(sorted(res.sources))
    if not res.findings:
        if verbose:
            print(f"  ok    {res.symbol:<24} [{res.fmt}] {res.matched}/{res.declared} declared matched  ({src})")
        return
    n_miss = sum(1 for v, _ in res.findings if v == "MISSING")
    n_mis = sum(1 for v, _ in res.findings if v.startswith("MISCLASS"))
    n_split = sum(1 for v, _ in res.findings if v == "SPLIT/MERGED")
    print(f"  MISS  {res.symbol:<24} [{res.fmt}] declared={res.declared} matched={res.matched} "
          f"missing={n_miss} misclassified={n_mis} split={n_split}  ({src})")
    for verdict, d in res.findings[:12]:
        print(f"          - {verdict:<24} [{d.source}] {d.text[:72]!r}")
    if len(res.findings) > 12:
        print(f"          ... +{len(res.findings) - 12} more")


def _write_tsv(path: Path, results: list[DocResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["symbol", "check", "severity", "detail"])
        for res in results:
            if res.error:
                w.writerow([res.symbol, "toc", "error", f"extract-failed: {res.error}"])
                continue
            for verdict, d in res.findings:
                sev = _SEVERITY.get(verdict, "warn")
                detail = f"{verdict}: heading {d.text[:120]!r} [src={d.source}]"
                w.writerow([res.symbol, "toc", sev, detail])
    print(f"\nTSV written: {path}")


def _finding_keys(results: list[DocResult]) -> set[tuple[str, str, str]]:
    """(symbol, verdict, normalised declared heading) — the triage key."""
    out: set[tuple[str, str, str]] = set()
    for r in results:
        for verdict, d in r.findings:
            out.add((r.symbol, verdict, norm_stripped(d.text)[:120]))
    return out


def _load_baseline(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    out = set()
    for ln in path.read_text().splitlines()[1:]:
        parts = ln.split("\t")
        if len(parts) >= 3:
            out.add((parts[0], parts[1], parts[2]))
    return out


def _write_baseline(path: Path, keys: set[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fh.write("symbol\tverdict\theading\n")
        for k in sorted(keys):
            fh.write("\t".join(k) + "\n")


def _summary(results: list[DocResult], tsv: Path) -> int:
    checked = [r for r in results if r.error is None]
    with_struct = [r for r in checked if r.declared > 0]
    fully = [r for r in with_struct if not r.findings]
    with_miss = [r for r in with_struct if r.findings]
    errs = [r for r in results if r.error]

    # per-source coverage
    src_docs: dict[str, int] = {}
    for r in with_struct:
        for s in r.sources:
            src_docs[s] = src_docs.get(s, 0) + 1

    tot_declared = sum(r.declared for r in with_struct)
    tot_matched = sum(r.matched for r in with_struct)
    tot_missing = sum(1 for r in with_struct for v, _ in r.findings if v == "MISSING")
    tot_mis = sum(1 for r in with_struct for v, _ in r.findings if v.startswith("MISCLASS"))
    tot_split = sum(1 for r in with_struct for v, _ in r.findings if v == "SPLIT/MERGED")

    print("\n" + "=" * 72)
    print(f"documents checked           : {len(checked)}  (extract errors: {len(errs)})")
    print(f"with self-declared structure: {len(with_struct)}")
    print(f"  fully matched             : {len(fully)}")
    print(f"  with misses               : {len(with_miss)}")
    print(f"declared headings           : {tot_declared}  "
          f"(matched {tot_matched}, missing {tot_missing}, misclassified {tot_mis}, split {tot_split})")
    print("per-source coverage (docs carrying each self-declared source):")
    for s in ("bookmark", "tocfield", "contents", "style", "bold", "outline"):
        print(f"    {s:<9}: {src_docs.get(s, 0)}")
    if with_miss:
        print("\ndocuments with the most declared-structure misses:")
        for r in sorted(with_miss, key=lambda r: -len(r.findings))[:12]:
            print(f"    {r.symbol:<24} {len(r.findings):>3} misses  (declared {r.declared})")
    print(f"\nTSV: {tsv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
