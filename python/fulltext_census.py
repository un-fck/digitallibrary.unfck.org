#!/usr/bin/env python3
"""Read-only OOXML corpus census for the full-text pipeline (Track A, stage 3).

Surveys what OOXML constructs actually occur across the archived .docx corpus so
we KNOW what the raw extractor (fulltext_extract_raw.py) keeps or drops. It opens
each document as a zip and walks the XML with lxml directly — deliberately NOT
python-docx — to see the low-level constructs unfiltered.

For each document (digitallibrary.document_files rows with status in
('converted','extracted')) it counts: body paragraphs, tables, footnotes/endnotes,
headers/footers, text boxes, images/drawings, section breaks, numbering references
vs literal-number paragraph starts, real vs field-code hyperlinks, content
controls, and the distinct paragraph styles used (plus which styles carry the
text bulk).

Output:
  - a per-document TSV (default: the job tmp dir, since data/ is not gitignored)
  - a printed aggregate summary: construct -> #docs containing it, total count,
    % of docs.

Usage:
    uv run python python/fulltext_census.py
    uv run python python/fulltext_census.py --limit 20
    uv run python python/fulltext_census.py --out /tmp/census.tsv
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

from lxml import etree

from fulltext_common import ARCHIVE_ROOT, get_conn

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Leading literal number, e.g. "1. Requests ..." (operative-paragraph signal in
# converted files that lost their real numbering). Tabs/spaces before the digit
# are common in the WP/doc conversions, so tolerate leading whitespace.
LITERAL_NUM_RE = re.compile(r"^\s*\d+\.\s")
HYPERLINK_INSTR_RE = re.compile(r"HYPERLINK", re.I)


def _text_of(el: etree._Element) -> str:
    """Concatenate all w:t descendants of an element (tabs/breaks ignored here)."""
    return "".join(t.text or "" for t in el.iter(W + "t"))


def _para_style(p: etree._Element) -> str | None:
    pPr = p.find(W + "pPr")
    if pPr is None:
        return None
    ps = pPr.find(W + "pStyle")
    return ps.get(W + "val") if ps is not None else None


def _has_real_numpr(p: etree._Element) -> bool:
    """True if the paragraph carries a real w:numPr (numId present and != 0).

    numId 0 is the OOXML convention for 'numbering removed', so it is not a real
    numbered paragraph.
    """
    pPr = p.find(W + "pPr")
    if pPr is None:
        return False
    np = pPr.find(W + "numPr")
    if np is None:
        return False
    nid = np.find(W + "numId")
    if nid is None:
        return False
    return nid.get(W + "val") not in (None, "0")


def census_one(path: Path) -> dict:
    """Return a dict of construct counts for a single .docx file."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        doc = etree.fromstring(z.read("word/document.xml"))
        footnotes = (
            etree.fromstring(z.read("word/footnotes.xml"))
            if "word/footnotes.xml" in names
            else None
        )
        endnotes = (
            etree.fromstring(z.read("word/endnotes.xml"))
            if "word/endnotes.xml" in names
            else None
        )

    body = doc.find(W + "body")
    body_children = list(body) if body is not None else []
    body_paras = [c for c in body_children if etree.QName(c).localname == "p"]

    # Footnotes / endnotes: count only real notes (skip separators, id < 1).
    def _real_notes(part, tag):
        if part is None:
            return 0
        n = 0
        for note in part.findall(W + tag):
            typ = note.get(W + "type")
            if typ in ("separator", "continuationSeparator"):
                continue
            n += 1
        return n

    n_footnotes = _real_notes(footnotes, "footnote")
    n_endnotes = _real_notes(endnotes, "endnote")

    headers = sum(1 for n in names if re.match(r"word/header\d+\.xml$", n))
    footers = sum(1 for n in names if re.match(r"word/footer\d+\.xml$", n))

    # Numbering vs literal-number starts (on body paragraphs).
    n_numpr = sum(1 for p in body.iter(W + "p") if _has_real_numpr(p))
    n_literal = sum(1 for p in body_paras if LITERAL_NUM_RE.match(_text_of(p)))

    # Hyperlinks: real w:hyperlink vs field-code HYPERLINK instructions.
    n_hyperlink = len(list(doc.iter(W + "hyperlink")))
    n_field_link = sum(
        1 for it in doc.iter(W + "instrText") if HYPERLINK_INSTR_RE.search(it.text or "")
    )

    # Section breaks: every w:sectPr (final body one + mid-doc breaks in pPr).
    n_sectpr = len(list(doc.iter(W + "sectPr")))

    # Distinct paragraph styles + which styles carry the text bulk.
    style_chars: dict[str, int] = {}
    for p in body_paras:
        sid = _para_style(p) or "(default)"
        style_chars[sid] = style_chars.get(sid, 0) + len(_text_of(p).strip())
    total_chars = sum(style_chars.values()) or 1
    # Top style by text volume.
    top_style, top_chars = ("", 0)
    if style_chars:
        top_style, top_chars = max(style_chars.items(), key=lambda kv: kv[1])

    return {
        "body_paragraphs": len(body_paras),
        "empty_paragraphs": sum(1 for p in body_paras if not _text_of(p).strip()),
        "tables": len(list(doc.iter(W + "tbl"))),
        "footnotes": n_footnotes,
        "endnotes": n_endnotes,
        "headers": headers,
        "footers": footers,
        "textboxes": len(list(doc.iter(W + "txbxContent"))),
        "drawings": len(list(doc.iter(W + "drawing"))),
        "pict": len(list(doc.iter(W + "pict"))),
        "sect_breaks": n_sectpr,
        "num_refs": n_numpr,
        "literal_num_starts": n_literal,
        "hyperlinks": n_hyperlink,
        "field_links": n_field_link,
        "content_controls": len(list(doc.iter(W + "sdt"))),
        "distinct_styles": len(style_chars),
        "style_list": ",".join(sorted(style_chars)),
        "bulk_style": top_style,
        "bulk_style_pct": round(100 * top_chars / total_chars, 1),
    }


# TSV columns (per-document row).
TSV_COLS = [
    "symbol_normalized", "lang", "format", "source",
    "body_paragraphs", "empty_paragraphs", "tables", "footnotes", "endnotes",
    "headers", "footers", "textboxes", "drawings", "pict", "sect_breaks",
    "num_refs", "literal_num_starts", "hyperlinks", "field_links",
    "content_controls", "distinct_styles", "bulk_style", "bulk_style_pct",
    "style_list",
]

# Boolean-presence constructs for the aggregate "% of docs" summary.
PRESENCE_KEYS = [
    "tables", "footnotes", "endnotes", "textboxes", "drawings", "pict",
    "num_refs", "literal_num_starts", "hyperlinks", "field_links",
    "content_controls",
]


def fetch_targets(limit: int | None) -> list[tuple[str, str, str, str]]:
    """Return [(symbol, lang, format, rel_path)] for docs to census."""
    sql = (
        "SELECT symbol_normalized, lang, format, "
        "       COALESCE(converted_path, archive_path) AS rel "
        "FROM digitallibrary.document_files "
        "WHERE status IN ('converted','extracted') "
        "  AND COALESCE(converted_path, archive_path) IS NOT NULL "
        "ORDER BY symbol_normalized"
    )
    params: list[object] = []
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]


def print_aggregate(rows: list[dict], n_docs: int) -> None:
    print("\n=== Aggregate census over", n_docs, "documents ===")
    print(f"{'construct':<22}{'docs':>6}{'docs%':>8}{'total':>10}")
    for key in PRESENCE_KEYS:
        docs_with = sum(1 for r in rows if r[key] > 0)
        total = sum(r[key] for r in rows)
        pct = round(100 * docs_with / n_docs, 1) if n_docs else 0
        print(f"{key:<22}{docs_with:>6}{pct:>7}%{total:>10}")

    # Style usage: which styles carry the bulk of text, across the corpus.
    print("\n=== Bulk text-carrying style (per doc) ===")
    bulk_counts: dict[str, int] = {}
    for r in rows:
        bulk_counts[r["bulk_style"]] = bulk_counts.get(r["bulk_style"], 0) + 1
    for sid, cnt in sorted(bulk_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {sid or '(none)':<20}{cnt:>5} docs")

    # Distinct styles distribution.
    only_normal = sum(
        1 for r in rows if r["style_list"] in ("Normal", "(default)", "")
    )
    print(f"\nDocs whose only paragraph style is Normal/default: {only_normal}/{n_docs}")
    print("(these are the style-stripped conversions — WP fallback territory)")


def main() -> int:
    ap = argparse.ArgumentParser(description="OOXML corpus census (read-only)")
    ap.add_argument("--limit", type=int, help="census at most N documents")
    ap.add_argument(
        "--out",
        default="/Users/david/.claude/jobs/3f4ded06/tmp/census.tsv",
        help="TSV output path (data/ is not gitignored, so default is the job tmp dir)",
    )
    args = ap.parse_args()

    targets = fetch_targets(args.limit)
    print(f"Census targets: {len(targets)} documents")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    errors = 0
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(TSV_COLS) + "\n")
        for i, (symbol, lang, fmt, rel) in enumerate(targets, 1):
            path = ARCHIVE_ROOT / rel
            source = "converted" if rel.startswith("converted/") else "original"
            try:
                c = census_one(path)
            except Exception as exc:  # never crash the survey on one bad file
                errors += 1
                print(f"  ! {symbol}: {type(exc).__name__}: {exc}")
                continue
            c["symbol_normalized"] = symbol
            c["lang"] = lang
            c["format"] = fmt
            c["source"] = source
            rows.append(c)
            fh.write("\t".join(str(c.get(col, "")) for col in TSV_COLS) + "\n")
            if i % 25 == 0:
                print(f"  censused {i}/{len(targets)}")

    print(f"\nWrote {out_path} ({len(rows)} rows, {errors} errors)")
    print_aggregate(rows, len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
