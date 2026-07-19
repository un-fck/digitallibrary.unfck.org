#!/usr/bin/env python3
"""Raw paragraph extractor — full-text pipeline (Track A, stage 3).

Reads archived .docx files (native, or LibreOffice-converted from doc/wpd) and
writes a LOW-INTERPRETATION, document-ordered paragraph stream to
digitallibrary.document_paragraphs_raw. It interprets nothing semantically: it
preserves everything a future semantic parser might need — styles, numbering,
italic/bold, indentation, leading tabs, table cells, footnotes, hyperlinks,
section breaks, empty spacer paragraphs — and leaves the meaning to layer 3.

The SSD archive is ground truth; this table is a disposable re-parse substrate,
so re-extraction is cheap. The extractor still aims to be lossless enough that
parser iteration never needs to reopen the files.

Uses python-docx (document load, style-name resolution, body element) PLUS raw
lxml for everything python-docx drops: numbering.xml resolution, footnotes /
endnotes, field-code hyperlinks, text boxes, section breaks, run-level italic
sequences, literal tabs.

Ledger status lifecycle touched here (digitallibrary.document_files.status):
  - reads rows with status='converted' (native docx: converted_path NULL, use
    archive_path; else use converted_path)
  - on success: status='extracted'
  - on failure: status='extract_failed' (error recorded) — never crashes the run;
    this status is not in the schema comment but needs no schema change.

Usage:
    uv run python python/fulltext_extract_raw.py
    uv run python python/fulltext_extract_raw.py --limit 20
    uv run python python/fulltext_extract_raw.py --symbols A/RES/71/257,S/PRST/1994/51
    uv run python python/fulltext_extract_raw.py --force   # re-extract 'extracted' rows too
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from psycopg.types.json import Jsonb

from fulltext_common import ARCHIVE_ROOT, get_conn, upsert_document_file

EXTRACTOR_VERSION = "raw-v1"
BATCH_DOCS = 20  # docs per short-lived DB connection

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HYPERLINK_RE = re.compile(r'HYPERLINK\s+"([^"]+)"', re.I)


def _ln(el: etree._Element) -> str:
    return etree.QName(el).localname


def _bool_prop(rPr: etree._Element | None, name: str) -> bool:
    """A boolean run property (w:i, w:b, ...) that is present and not turned off."""
    if rPr is None:
        return False
    e = rPr.find(W + name)
    if e is None:
        return False
    return e.get(W + "val") not in ("0", "false", "off")


# ---------------------------------------------------------------------------
# Run tokenisation (direct-formatting only — the preambular italic signal in
# native UN docs is applied as direct rPr/w:i, so this is sufficient and
# conservative; style/character-style-inherited italic is deliberately ignored).
# ---------------------------------------------------------------------------

def tokenize(p: etree._Element) -> tuple[list[dict], list[etree._Element]]:
    """Return (run_tokens, textbox_paragraphs) for a w:p element.

    run_tokens are ordered {text, italic, bold, caps} dicts covering the visible
    text (following into hyperlinks / insertions / smartTags / fields). Text-box
    paragraphs (w:txbxContent) are collected separately, NOT merged into the
    parent text — they are re-emitted in place as their own rows.
    """
    runs: list[dict] = []
    textboxes: list[etree._Element] = []
    _walk_runs(p, runs, textboxes)
    return runs, textboxes


def _walk_runs(el: etree._Element, runs: list[dict], textboxes: list[etree._Element]) -> None:
    for child in el:
        tag = _ln(child)
        if tag == "r":
            runs.append(_run_token(child))
        elif tag == "txbxContent":
            # A text box: its paragraphs become separate rows, not parent text.
            textboxes.extend(child.findall(W + "p"))
        elif tag == "del":
            continue  # tracked-change deletion: not visible text
        elif tag == "AlternateContent":
            # mc fallback: process only the first Choice/Fallback to avoid
            # double-counting the same text box in both branches.
            branch = child.find("{http://schemas.openxmlformats.org/markup-compatibility/2006}Choice")
            if branch is None:
                branch = child.find("{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback")
            if branch is not None:
                _walk_runs(branch, runs, textboxes)
        else:
            # hyperlink, ins, smartTag, fldSimple, sdt/sdtContent, drawing, etc.
            _walk_runs(child, runs, textboxes)


def _run_token(r: etree._Element) -> dict:
    rPr = r.find(W + "rPr")
    caps = rPr is not None and (
        rPr.find(W + "caps") is not None or rPr.find(W + "smallCaps") is not None
    )
    parts: list[str] = []
    for c in r:
        t = _ln(c)
        if t == "t":
            parts.append(c.text or "")
        elif t == "tab":
            parts.append("\t")
        elif t in ("br", "cr"):
            parts.append("\n")
        elif t == "noBreakHyphen":
            parts.append("-")
    return {
        "text": "".join(parts),
        "italic": _bool_prop(rPr, "i"),
        "bold": _bool_prop(rPr, "b"),
        "caps": caps,
    }


def runs_text(p: etree._Element) -> str:
    """Plain concatenated text of a paragraph (tabs/breaks preserved)."""
    runs, _ = tokenize(p)
    return "".join(t["text"] for t in runs)


# ---------------------------------------------------------------------------
# Paragraph-level structure
# ---------------------------------------------------------------------------

def _pPr(p: etree._Element) -> etree._Element | None:
    return p.find(W + "pPr")


def para_style_id(p: etree._Element) -> str | None:
    pPr = _pPr(p)
    if pPr is None:
        return None
    ps = pPr.find(W + "pStyle")
    return ps.get(W + "val") if ps is not None else None


def para_numbering(p: etree._Element, num_map: dict, abs_map: dict) -> dict | None:
    pPr = _pPr(p)
    if pPr is None:
        return None
    np = pPr.find(W + "numPr")
    if np is None:
        return None
    nid_el = np.find(W + "numId")
    num_id = nid_el.get(W + "val") if nid_el is not None else None
    if num_id in (None, "0"):  # numId 0 == numbering removed
        return None
    ilvl_el = np.find(W + "ilvl")
    ilvl = int(ilvl_el.get(W + "val")) if ilvl_el is not None else 0
    out: dict = {"num_id": int(num_id), "ilvl": ilvl}
    # Resolve num -> abstractNum -> level format, tolerating failures.
    abstract_id = num_map.get(num_id)
    if abstract_id is not None:
        lvl = abs_map.get(abstract_id, {}).get(ilvl)
        if lvl:
            fmt, lvl_text = lvl
            if fmt is not None:
                out["num_fmt"] = fmt
            if lvl_text is not None:
                out["lvl_text"] = lvl_text
    return out


def para_props(p: etree._Element, runs: list[dict], text: str, textbox: bool) -> dict | None:
    """Only-non-default formatting props (see schema)."""
    props: dict = {}
    non_ws = [t for t in runs if t["text"].strip()]
    whole_italic = bool(non_ws) and all(t["italic"] for t in non_ws)
    whole_bold = bool(non_ws) and all(t["bold"] for t in non_ws)
    if whole_italic:
        props["italic"] = True
    if whole_bold:
        props["bold"] = True

    # Leading italic run-sequence (the preambular-verb signal, e.g. "Recalling").
    if not whole_italic:
        i = 0
        while i < len(runs) and not runs[i]["text"].strip():
            i += 1
        if i < len(runs) and runs[i]["italic"]:
            buf: list[str] = []
            for k in range(i, len(runs)):
                tok = runs[k]
                if tok["text"].strip() and not tok["italic"]:
                    break
                buf.append(tok["text"])
            lead = "".join(buf).strip()
            if lead:
                props["lead_italic_text"] = lead

    pPr = _pPr(p)
    if pPr is not None:
        jc = pPr.find(W + "jc")
        if jc is not None:
            val = jc.get(W + "val")
            if val not in (None, "left", "start"):
                props["alignment"] = val
        ind = pPr.find(W + "ind")
        if ind is not None:
            left = ind.get(W + "left") or ind.get(W + "start")
            hanging = ind.get(W + "hanging")
            first = ind.get(W + "firstLine")
            if left and int(left) != 0:
                props["indent_left"] = int(left)
            if hanging and int(hanging) != 0:
                props["indent_hanging"] = int(hanging)
            if first and int(first) != 0:
                props["indent_firstline"] = int(first)

    # all_caps: explicit caps run property, or the text is entirely uppercase.
    alpha = [c for c in text if c.isalpha()]
    text_upper = len(alpha) >= 2 and all(c.isupper() for c in alpha)
    if any(t["caps"] for t in runs) or text_upper:
        props["all_caps"] = True

    # Leading literal tabs — WP conversions express indentation as literal tabs,
    # a critical hierarchy signal.
    n_tabs = len(text) - len(text.lstrip("\t"))
    if n_tabs:
        props["tabs_leading"] = n_tabs

    if textbox:
        props["textbox"] = True

    return props or None


def para_footnote_refs(p: etree._Element) -> dict | None:
    note_ids = [int(r.get(W + "id")) for r in p.iter(W + "footnoteReference") if r.get(W + "id")]
    end_ids = [int(r.get(W + "id")) for r in p.iter(W + "endnoteReference") if r.get(W + "id")]
    out: dict = {}
    if note_ids:
        out["note_ids"] = note_ids
    if end_ids:
        out["endnote_ids"] = end_ids
    return out or None


def para_hyperlinks(p: etree._Element, rels: dict) -> list[dict] | None:
    """Real w:hyperlink links (r:id resolved via rels) + field-code HYPERLINK
    links, merged in document order (port of un80-docs docx_utils.sort_links)."""
    links_normal: list[tuple[str, str]] = []
    for hl in p.iter(W + "hyperlink"):
        rid = hl.get(qn("r:id"))
        if not rid:
            continue  # internal anchor, no URL
        url = rels.get(rid)
        if not url:
            continue
        text = "".join(t.text or "" for t in hl.iter(W + "t"))
        links_normal.append((text, url))

    links_field = _field_links(p)
    merged = _sort_links(links_normal, links_field)
    return [{"text": t, "url": u} for t, u in merged] or None


def _field_links(p: etree._Element) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    url: str | None = None
    display: list[str] = []
    in_field = False
    after_sep = False
    for node in p.iter():
        tag = _ln(node)
        if tag == "fldChar":
            ft = node.get(qn("w:fldCharType"))
            if ft == "begin":
                url, display, in_field, after_sep = None, [], True, False
            elif ft == "separate" and in_field:
                after_sep = True
            elif ft == "end" and in_field:
                if url and display and "http" in url:
                    links.append(("".join(display), url))
                in_field = after_sep = False
        elif tag == "instrText" and in_field:
            m = HYPERLINK_RE.search(node.text or "")
            if m:
                url = m.group(1)
        elif after_sep and tag == "t" and node.text:
            display.append(node.text)
    return links


def _sort_links(normal: list[tuple[str, str]], field: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Merge the two link lists keeping document order without duplicating a link
    found by both methods (same recipe as un80-docs docx_utils.sort_links)."""
    normal = list(normal)
    field = list(field)
    out: list[tuple[str, str]] = []
    while normal or field:
        if normal and field:
            if normal[0][1] == field[0][1]:
                out.append(normal.pop(0))
                field.pop(0)
            elif normal[0][1] not in [f[1] for f in field]:
                out.append(normal.pop(0))
            else:
                out.append(field.pop(0))
        elif normal:
            out.append(normal.pop(0))
        else:
            out.append(field.pop(0))
    return out


def has_sectpr(p: etree._Element) -> bool:
    pPr = _pPr(p)
    return pPr is not None and pPr.find(W + "sectPr") is not None


# ---------------------------------------------------------------------------
# Part maps (styles / numbering / relationships) read from the archive file.
# ---------------------------------------------------------------------------

def build_style_names(doc) -> dict:
    names: dict = {}
    try:
        for s in doc.styles:
            if s.style_id:
                names[s.style_id] = s.name
    except Exception:
        pass
    return names


def read_part_maps(path: Path) -> tuple[dict, dict, dict, etree._Element | None, etree._Element | None]:
    """From the zip: (num_map, abs_map, rels_map, footnotes_root, endnotes_root)."""
    num_map: dict = {}
    abs_map: dict = {}
    rels_map: dict = {}
    footnotes = endnotes = None
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/numbering.xml" in names:
            nb = etree.fromstring(z.read("word/numbering.xml"))
            for an in nb.findall(W + "abstractNum"):
                aid = an.get(W + "abstractNumId")
                lvls: dict = {}
                for lvl in an.findall(W + "lvl"):
                    ilvl = int(lvl.get(W + "ilvl"))
                    nf = lvl.find(W + "numFmt")
                    lt = lvl.find(W + "lvlText")
                    lvls[ilvl] = (
                        nf.get(W + "val") if nf is not None else None,
                        lt.get(W + "val") if lt is not None else None,
                    )
                abs_map[aid] = lvls
            for num in nb.findall(W + "num"):
                nid = num.get(W + "numId")
                a = num.find(W + "abstractNumId")
                if a is not None:
                    num_map[nid] = a.get(W + "val")
        if "word/_rels/document.xml.rels" in names:
            rels = etree.fromstring(z.read("word/_rels/document.xml.rels"))
            for rel in rels:
                if rel.get("TargetMode") == "External":
                    rels_map[rel.get("Id")] = rel.get("Target")
        if "word/footnotes.xml" in names:
            footnotes = etree.fromstring(z.read("word/footnotes.xml"))
        if "word/endnotes.xml" in names:
            endnotes = etree.fromstring(z.read("word/endnotes.xml"))
    return num_map, abs_map, rels_map, footnotes, endnotes


# ---------------------------------------------------------------------------
# Extraction of one document into ordered row dicts.
# ---------------------------------------------------------------------------

def _new_row(kind: str, text: str) -> dict:
    return {
        "kind": kind,
        "text": text,
        "style_id": None,
        "style_name": None,
        "numbering": None,
        "props": None,
        "table_cell": None,
        "hyperlinks": None,
        "footnote_ref": None,
    }


def extract_document(path: Path) -> list[dict]:
    doc = Document(str(path))
    style_names = build_style_names(doc)
    num_map, abs_map, rels_map, footnotes, endnotes = read_part_maps(path)

    rows: list[dict] = []
    table_idx = [0]

    def emit_paragraph(p: etree._Element, textbox: bool = False) -> None:
        runs, textboxes = tokenize(p)
        text = "".join(t["text"] for t in runs)
        sectpr = has_sectpr(p) and not textbox
        stripped_empty = text.strip() == ""

        fn = para_footnote_refs(p)
        links = para_hyperlinks(p, rels_map)

        # A truly empty spacer with no refs/links -> 'empty'. But if it carries a
        # mid-doc section break, the section_break marker below subsumes it.
        if stripped_empty and fn is None and links is None:
            if not sectpr:
                r = _new_row("empty", "")
                _decorate(r, p, runs, text, textbox)
                rows.append(r)
        else:
            r = _new_row("paragraph", text)
            _decorate(r, p, runs, text, textbox)
            r["footnote_ref"] = fn
            r["hyperlinks"] = links
            rows.append(r)

        # Text boxes anchored in this paragraph, re-emitted in place.
        for tb_p in textboxes:
            emit_paragraph(tb_p, textbox=True)

        if sectpr:
            rows.append(_new_row("section_break", ""))

    def _decorate(r: dict, p: etree._Element, runs: list[dict], text: str, textbox: bool) -> None:
        sid = para_style_id(p)
        r["style_id"] = sid
        r["style_name"] = style_names.get(sid) if sid else None
        r["numbering"] = para_numbering(p, num_map, abs_map)
        r["props"] = para_props(p, runs, text, textbox)

    def emit_table(tbl: etree._Element) -> None:
        my_idx = table_idx[0]
        table_idx[0] += 1
        for r_i, tr in enumerate(tbl.findall(W + "tr")):
            for c_i, tc in enumerate(tr.findall(W + "tc")):
                cell_paras = tc.findall(W + "p")
                cell_text = "\n".join(runs_text(cp) for cp in cell_paras)
                row = _new_row("table_cell", cell_text)
                row["table_cell"] = {"table": my_idx, "row": r_i, "col": c_i}
                rows.append(row)
                for nested in tc.findall(W + "tbl"):  # depth-first
                    emit_table(nested)

    def walk_body(container: etree._Element) -> None:
        for child in container:
            tag = _ln(child)
            if tag == "p":
                emit_paragraph(child)
            elif tag == "tbl":
                emit_table(child)
            elif tag == "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    walk_body(content)
            # bare body-level sectPr = final section props, not a mid-doc break.

    walk_body(doc.element.body)

    # Footnotes then endnotes, appended after the body flow.
    _emit_notes(footnotes, "footnote", "footnote", rows)
    _emit_notes(endnotes, "endnote", "endnote", rows)

    for pos, row in enumerate(rows):
        row["position"] = pos
    return rows


def _emit_notes(part: etree._Element | None, tag: str, note_type: str, rows: list[dict]) -> None:
    if part is None:
        return
    for note in part.findall(W + tag):
        if note.get(W + "type") in ("separator", "continuationSeparator"):
            continue
        note_id = note.get(W + "id")
        text = "\n".join(runs_text(p) for p in note.findall(W + "p"))
        row = _new_row("footnote", text)
        ref: dict = {"note_id": int(note_id) if note_id is not None else None}
        if note_type == "endnote":
            ref["note_type"] = "endnote"
        row["footnote_ref"] = ref
        rows.append(row)


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

_INSERT = (
    "INSERT INTO digitallibrary.document_paragraphs_raw "
    "(symbol_normalized, lang, position, kind, text, style_id, style_name, "
    " numbering, props, table_cell, hyperlinks, footnote_ref, extractor_version) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)


def write_document(conn, symbol: str, lang: str, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM digitallibrary.document_paragraphs_raw "
            "WHERE symbol_normalized = %s AND lang = %s",
            [symbol, lang],
        )
        params = [
            (
                symbol, lang, r["position"], r["kind"], r["text"],
                r["style_id"], r["style_name"],
                Jsonb(r["numbering"]) if r["numbering"] is not None else None,
                Jsonb(r["props"]) if r["props"] is not None else None,
                Jsonb(r["table_cell"]) if r["table_cell"] is not None else None,
                Jsonb(r["hyperlinks"]) if r["hyperlinks"] is not None else None,
                Jsonb(r["footnote_ref"]) if r["footnote_ref"] is not None else None,
                EXTRACTOR_VERSION,
            )
            for r in rows
        ]
        cur.executemany(_INSERT, params)


# ---------------------------------------------------------------------------
# Target selection + main loop
# ---------------------------------------------------------------------------

def fetch_targets(symbols: list[str] | None, force: bool, limit: int | None):
    statuses = ["converted", "extracted"] if force else ["converted"]
    sql = (
        "SELECT symbol_normalized, lang, format, archive_path, converted_path "
        "FROM digitallibrary.document_files "
        "WHERE status = ANY(%s) "
        "  AND COALESCE(converted_path, archive_path) IS NOT NULL"
    )
    params: list[object] = [statuses]
    if symbols:
        sql += " AND symbol_normalized = ANY(%s)"
        params.append(symbols)
    sql += " ORDER BY symbol_normalized"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description="Raw paragraph extractor (Track A stage 3)")
    ap.add_argument("--limit", type=int, help="extract at most N documents")
    ap.add_argument("--symbols", help="comma-separated symbol_normalized list to (re)extract")
    ap.add_argument("--force", action="store_true", help="also re-extract rows already 'extracted'")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    targets = fetch_targets(symbols, args.force, args.limit)
    print(f"Extraction targets: {len(targets)} documents")

    ok = failed = total_rows = 0
    for start in range(0, len(targets), BATCH_DOCS):
        chunk = targets[start:start + BATCH_DOCS]
        with get_conn() as conn:
            for symbol, lang, fmt, archive_path, converted_path in chunk:
                rel = converted_path or archive_path
                path = ARCHIVE_ROOT / rel
                try:
                    if not path.exists():
                        raise FileNotFoundError(f"archive file missing: {rel}")
                    rows = extract_document(path)
                    write_document(conn, symbol, lang, rows)
                    upsert_document_file(conn, symbol, lang, status="extracted", error=None)
                    conn.commit()
                    ok += 1
                    total_rows += len(rows)
                except Exception as exc:  # never crash the run on one bad file
                    conn.rollback()
                    upsert_document_file(
                        conn, symbol, lang,
                        status="extract_failed", error=f"{type(exc).__name__}: {exc}"[:500],
                    )
                    conn.commit()
                    failed += 1
                    print(f"  ! {symbol}: {type(exc).__name__}: {exc}")
        done = start + len(chunk)
        print(f"  extracted {done}/{len(targets)} ok={ok} failed={failed} rows={total_rows}")

    print(f"\nDone. ok={ok} failed={failed} rows_written={total_rows}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
