#!/usr/bin/env python3
"""fulltext_review.py — static HTML review harness for the UN-document semantic parser.

This is the tool a human (and reviewing agents) use to judge each parser round.
It joins three things per document:

  1. raw rows      digitallibrary.document_paragraphs_raw (the extraction substrate)
  2. parsed JSON   <parsed-dir>/<sanitized>.json, shape:
                     {symbol, format, parser_version,
                      elements: [{positions[], type, section, paragraph_type,
                                  level, prefix, heading_level, text, lead_verb, ...}],
                      dropped:  [{position, reason}],
                      issues:   [ ... ]}
                   (parser may refine this shape; we code defensively — missing keys
                    render as-is, unknown element types render gray.)
  3. ledger        digitallibrary.document_files (format: docx/doc/wpd, status)

OUTPUT (into --out DIR, default the review/ dir on the SSD):
  * index.html          sortable table of every rendered doc + red flags
  * <sanitized>.html    per-doc TWO-COLUMN page (raw | parsed) with anomalies inline
  * _flags.json         {symbol: [flag, ...]} machine-readable summary for iterators

Design goal: information density and clarity beat beauty. Self-contained (one inline
<style>, tiny inline sort JS, no external assets — CSP-safe). Plain stdlib + psycopg.

USAGE
  uv run python python/fulltext_review.py                       # all docs w/ raw rows
  uv run python python/fulltext_review.py --limit 20
  uv run python python/fulltext_review.py --symbols A/RES/76/72 S/PRST/2018/18
  uv run python python/fulltext_review.py --parsed-dir /tmp/parsed --out /tmp/review

The CLI never fails when a parsed JSON is missing (left column renders with a
"not parsed" note) or when raw rows exist but the ledger says extract_failed.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

from fulltext_common import ARCHIVE_ROOT, get_conn, sanitize_symbol

DEFAULT_PARSED_DIR = ARCHIVE_ROOT / "parsed_dev"
DEFAULT_OUT_DIR = ARCHIVE_ROOT / "review"

TRUNCATE_AT = 300  # chars before a raw-row text gets an expand-on-click <details>

# Element type -> css role class on the right column. Unknown types fall through
# to "unknown" (rendered gray) so a parser that invents a new type is still visible.
KNOWN_ROLES = {
    "frontmatter", "title", "heading", "preambular", "operative",
    "annex", "appendix", "footnote", "table", "text",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def family_of(symbol: str) -> str:
    """Leading non-numeric segments, e.g. A/RES/76/72 -> 'A/RES',
    A/HRC/PRST/28/2 -> 'A/HRC/PRST', S/PRST/2018/18 -> 'S/PRST'."""
    segs = symbol.split("/")
    out = []
    for seg in segs:
        if any(ch.isdigit() for ch in seg):
            break
        out.append(seg)
    return "/".join(out) if out else (segs[0] if segs else symbol)


def compress_positions(positions: list[int]) -> str:
    """[14,15,16,20] -> '14-16, 20' for a compact superscript."""
    if not positions:
        return ""
    ps = sorted(set(int(p) for p in positions))
    parts = []
    start = prev = ps[0]
    for p in ps[1:]:
        if p == prev + 1:
            prev = p
            continue
        parts.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = p
    parts.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(parts)


_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman_value(s: str) -> int | None:
    if not s or any(ch not in _ROMAN for ch in s):
        return None
    total, prevv = 0, 0
    for ch in reversed(s):
        v = _ROMAN[ch]
        total += -v if v < prevv else v
        prevv = v
    return total


def _alpha_value(s: str) -> int | None:
    if not re.fullmatch(r"[a-z]+", s):
        return None
    val = 0
    for ch in s:
        val = val * 26 + (ord(ch) - ord("a") + 1)
    return val


def prefix_number(prefix: str) -> tuple[str, int | None]:
    """Classify an operative prefix by its own shape. Returns (kind, ordinal).

    '1.' -> ('num', 1); '(a)' -> ('alpha', 1); '(iv)' -> ('roman', 4).

    Standalone shape is ambiguous for tokens that are both a letter and a Roman
    numeral (i, v, x, l, c, d, m) and for two-letter Romans ("ii", "vi"); the
    reset-aware detector prefers the parser's `level` via prefix_ordinal() and
    only falls back here. We resolve the shape ambiguity by preferring Roman when
    the token is a *multi-letter* well-formed Roman ("ii", "iv", "xiv") and alpha
    for a single letter (so "(c)" -> alpha 3, the common case)."""
    if not prefix:
        return ("none", None)
    s = prefix.strip().strip(".").strip("()").strip().lower()
    if s.isdigit():
        return ("num", int(s))
    if len(s) >= 2 and _roman_value(s) is not None:
        return ("roman", _roman_value(s))
    av = _alpha_value(s)
    if av is not None and len(s) <= 2:
        return ("alpha", av)
    rv = _roman_value(s)
    if rv is not None:
        return ("roman", rv)
    return ("other", None)


def prefix_ordinal(prefix: str, level: object) -> tuple[str, int | None]:
    """Ordinal of an operative prefix, trusting the parser's `level` to pick the
    numbering kind (1=numeric, 2=alpha, 3=roman). This sidesteps the standalone
    i/v/x/c letter-vs-Roman ambiguity: the parser's OpLevelTracker already decided
    that "(i)" opening a sub-sub run is Roman (level 3) while "(c)" as a third
    sub-item is alpha (level 2). Falls back to prefix_number() when level is absent."""
    if not prefix:
        return ("none", None)
    s = prefix.strip().strip(".").strip("()").strip().lower()
    if isinstance(level, int):
        if level <= 1:
            return ("num", int(s)) if s.isdigit() else prefix_number(prefix)
        if level == 2:
            av = _alpha_value(s)
            if av is not None:
                return ("alpha", av)
        if level >= 3:
            rv = _roman_value(s)
            if rv is not None:
                return ("roman", rv)
    return prefix_number(prefix)


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------

def load_symbols(conn, symbols: list[str] | None, limit: int | None) -> list[tuple[str, str]]:
    """Return [(symbol_normalized, lang), ...] to render, driven by raw rows present."""
    with conn.cursor() as cur:
        if symbols:
            cur.execute(
                """SELECT DISTINCT symbol_normalized, lang
                     FROM digitallibrary.document_paragraphs_raw
                    WHERE symbol_normalized = ANY(%s)
                    ORDER BY symbol_normalized, lang""",
                [symbols],
            )
        else:
            cur.execute(
                """SELECT DISTINCT symbol_normalized, lang
                     FROM digitallibrary.document_paragraphs_raw
                    ORDER BY symbol_normalized, lang"""
            )
        rows = cur.fetchall()
    if limit:
        rows = rows[:limit]
    return [(r[0], r[1]) for r in rows]


def load_raw(conn, symbol: str, lang: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT position, kind, text, style_id, style_name, numbering,
                      props, table_cell, hyperlinks, footnote_ref
                 FROM digitallibrary.document_paragraphs_raw
                WHERE symbol_normalized = %s AND lang = %s
                ORDER BY position""",
            [symbol, lang],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_ledger(conn, symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT symbol_normalized, format, status, error
                 FROM digitallibrary.document_files
                WHERE symbol_normalized = ANY(%s) AND lang = 'en'""",
            [symbols],
        )
        return {r[0]: {"format": r[1], "status": r[2], "error": r[3]} for r in cur.fetchall()}


def load_parsed(parsed_dir: Path, symbol: str) -> dict | None:
    p = parsed_dir / f"{sanitize_symbol(symbol)}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # corrupt / mid-write JSON — treat as "present but broken"
        return {"_load_error": str(e), "elements": [], "dropped": [], "issues": [f"JSON load error: {e}"]}


# ---------------------------------------------------------------------------
# analysis: accounting + anomalies + flags
# ---------------------------------------------------------------------------

def role_of(elem: dict) -> str:
    t = str(elem.get("type") or "").lower()
    if t in KNOWN_ROLES:
        return t
    pt = str(elem.get("paragraph_type") or "").lower()
    if pt in ("preambular", "preamble"):
        return "preambular"
    if pt == "operative":
        return "operative"
    sec = str(elem.get("section") or "").lower()
    if sec in ("annex",):
        return "annex"
    if sec in ("appendix",):
        return "appendix"
    if t in ("preamble",):
        return "preambular"
    return "unknown"


def is_operative(elem: dict) -> bool:
    return "operative" in (
        str(elem.get("type") or "").lower(),
        str(elem.get("paragraph_type") or "").lower(),
        str(elem.get("section") or "").lower(),
    )


def is_preambular(elem: dict) -> bool:
    vals = {
        str(elem.get("type") or "").lower(),
        str(elem.get("paragraph_type") or "").lower(),
        str(elem.get("section") or "").lower(),
    }
    return bool(vals & {"preambular", "preamble"})


def analyze(symbol: str, raw: list[dict], parsed: dict | None) -> dict:
    """Compute accounting, per-element anomalies, and doc-level red flags."""
    raw_positions = [int(r["position"]) for r in raw]
    total = len(raw_positions)

    elements = (parsed or {}).get("elements") or []
    dropped = (parsed or {}).get("dropped") or []
    issues = (parsed or {}).get("issues") or []

    # position -> element index (green); position -> drop reason (gray)
    pos_in_elem: dict[int, int] = {}
    for i, el in enumerate(elements):
        for p in (el.get("positions") or []):
            try:
                pos_in_elem[int(p)] = i
            except (TypeError, ValueError):
                continue
    pos_dropped: dict[int, str] = {}
    for d in dropped:
        try:
            pos_dropped[int(d.get("position"))] = str(d.get("reason") or "")
        except (TypeError, ValueError):
            continue

    raw_pos_set = set(raw_positions)
    accounted = sum(1 for p in raw_positions if p in pos_in_elem or p in pos_dropped)
    unaccounted = [p for p in raw_positions if p not in pos_in_elem and p not in pos_dropped]
    pct_accounted = round(100.0 * accounted / total, 1) if total else 100.0

    # per-element anomalies + operative/preambular structure checks.
    #
    # Operative-gap detection is HIERARCHICAL and RESET-AWARE (v2). The old
    # detector kept a single last-ordinal per prefix-kind (num/alpha/roman) for
    # the whole document, so a level-2 "(a)..(e)" run made the *next* "(a)"
    # (starting a fresh sub-list under the following top-level item) look like a
    # jump 5 -> 1, and every restart of numbering in a new section / sub-text /
    # annex was flagged. It also let a level-2 run "break" the level-1 2 -> 3
    # succession. The new detector:
    #   * tracks an expected next-ordinal per *level* (1=top, 2=(a).., 3=(i)..);
    #   * when an item at level L appears, all deeper levels (>L) are reset, so a
    #     sub-list restarting at (a)/(i) under each parent is never a "gap";
    #   * resets ALL levels at a structural boundary -- a heading, an opening
    #     formula, an annex/appendix/section change, or a new text_index (a
    #     consolidated sub-resolution or annexed instrument restarts numbering);
    #   * treats a kind switch at the same level (numeric "(1)" vs alpha "(a)")
    #     as a fresh run, not a jump.
    # Only a genuine break WITHIN one continuous same-level, same-kind run
    # (e.g. top-level 12 -> 14, or (c) -> (e)) is reported.
    elem_anoms: list[list[tuple[str, str]]] = [[] for _ in elements]
    seen_operative = False
    op_gap = False
    level_jump = False
    n_operative = n_preambular = 0
    prev_level: int | None = None
    # level -> (kind, next_expected_ordinal) for the run currently open at level
    expected: dict[int, tuple[str, int]] = {}
    cur_section: str | None = None
    cur_text_index: object = None

    for i, el in enumerate(elements):
        anoms = elem_anoms[i]
        etype = str(el.get("type") or "").lower()
        sec = str(el.get("section") or "").lower()
        ti = el.get("text_index", 1)
        text = el.get("text")
        if (text is None or str(text).strip() == "") and role_of(el) not in ("table",):
            anoms.append(("empty", "element has empty text"))

        # structural boundary -> a new numbering context; reset all open runs
        if sec != cur_section or ti != cur_text_index:
            expected.clear()
            prev_level = None
            cur_section, cur_text_index = sec, ti
        if etype in ("heading", "opening", "title", "divider"):
            expected.clear()
            prev_level = None

        if is_preambular(el):
            n_preambular += 1
            if seen_operative:
                anoms.append(("misordered", "preambular element appears after an operative paragraph"))

        if is_operative(el):
            n_operative += 1
            seen_operative = True
            kind, ordv = prefix_ordinal(str(el.get("prefix") or ""), el.get("level"))
            lv = el.get("level")
            if not isinstance(lv, int):
                lv = {"num": 1, "alpha": 2, "roman": 3}.get(kind, 1)
            if ordv is not None:
                # a shallower/equal item ends any deeper open runs
                for deeper in [L for L in expected if L > lv]:
                    del expected[deeper]
                prev = expected.get(lv)
                # ordv == 1 (or 'a'/'i') is a fresh run start, never a "skip":
                # a numbered list restarting at 1 in the same section is a new
                # list (common in omnibus resolutions), not a gap.
                if prev is not None and prev[0] == kind and ordv != prev[1] and ordv != 1:
                    op_gap = True
                    anoms.append(("seq-gap",
                                  f"operative numbering at level {lv} ({kind}): "
                                  f"expected {prev[1]}, got {ordv}"))
                expected[lv] = (kind, ordv + 1)
            if isinstance(el.get("level"), int):
                if prev_level is not None and lv > prev_level + 1:
                    level_jump = True
                    anoms.append(("level-jump", f"indentation level jumps {prev_level} -> {lv}"))
                prev_level = lv

    has_annex = any(role_of(el) in ("annex", "appendix")
                    or str(el.get("section") or "").lower() in ("annex", "appendix")
                    for el in elements)

    # ---- doc-level red flags ----
    flags: list[str] = []
    if parsed is None:
        flags.append("not-parsed")
    if parsed is not None and parsed.get("_load_error"):
        flags.append("parse-json-error")
    if unaccounted and parsed is not None:  # for not-parsed docs "not-parsed" already says it
        flags.append(f"unaccounted:{len(unaccounted)}")
    if op_gap:
        flags.append("operative-gap")
    if level_jump:
        flags.append("level-jump")
    if "/RES/" in symbol and parsed is not None and n_operative == 0:
        flags.append("no-operatives")
    if symbol.startswith("A/RES") and parsed is not None and n_preambular == 0:
        flags.append("no-preambulars")
    # dropped% flag: 'empty' and 'section_break' are layout spacer rows (blank
    # paragraphs, WP section markers) -- dropping them is correct and says nothing
    # about parse quality, so they are excluded from the flag. A doc that is 63/64
    # empty-spacer drops with one real drop is a clean parse, not "dropped>71%".
    # The raw dropped count stays visible in the index; the flag fires only on the
    # meaningful (content-bearing) drop rate.
    IGNORED_DROP_REASONS = {"empty", "section_break"}
    n_dropped_meaningful = sum(
        1 for r in pos_dropped.values() if r not in IGNORED_DROP_REASONS)
    if total and n_dropped_meaningful / total > 0.15:
        flags.append(f"dropped>{int(100 * n_dropped_meaningful / total)}%")
    if issues:
        flags.append(f"issues:{len(issues)}")

    return {
        "total": total,
        "pos_in_elem": pos_in_elem,
        "pos_dropped": pos_dropped,
        "unaccounted": set(unaccounted),
        "n_unaccounted": len(unaccounted),
        "n_dropped": len(pos_dropped),
        "n_dropped_meaningful": n_dropped_meaningful,
        "pct_accounted": pct_accounted,
        "elements": elements,
        "dropped": dropped,
        "issues": issues,
        "elem_anoms": elem_anoms,
        "n_operative": n_operative,
        "n_preambular": n_preambular,
        "has_annex": has_annex,
        "flags": flags,
        "raw_pos_set": raw_pos_set,
    }


# ---------------------------------------------------------------------------
# shared CSS + sort JS
# ---------------------------------------------------------------------------

CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  color:#1a1a1a;background:#f6f7f8}
a{color:#1a56b0;text-decoration:none}a:hover{text-decoration:underline}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header.top{padding:12px 18px;background:#fff;border-bottom:1px solid #dde1e6;position:sticky;top:0;z-index:5}
header.top h1{margin:0;font-size:16px;font-weight:650}
header.top .sub{color:#667085;font-size:12px;margin-top:2px}
.wrap{padding:14px 18px}

/* badges */
.badge{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600;
  line-height:1.5;white-space:nowrap;vertical-align:middle}
.fmt-docx{background:#dbeafe;color:#1e40af}
.fmt-doc{background:#fef3c7;color:#92400e}
.fmt-wpd{background:#ede9fe;color:#5b21b6}
.fmt-pdf{background:#fee2e2;color:#991b1b}
.fmt-unknown,.fmt-none{background:#e5e7eb;color:#374151}
.kind{background:#eef2f6;color:#334155;font-weight:600}
.kind-footnote{background:#f1e9ff;color:#5b21b6}
.kind-table_cell{background:#e0f2fe;color:#075985}
.kind-section_break{background:#f3f4f6;color:#6b7280}
.kind-empty{background:#f3f4f6;color:#9ca3af}
.chip{display:inline-block;padding:0 5px;margin:0 2px 2px 0;border-radius:3px;font-size:10.5px;
  background:#eef1f4;color:#475467;border:1px solid #e1e6eb;white-space:nowrap}
.chip.it{font-style:italic;background:#fbf6ff;border-color:#ecdcff;color:#6b21a8}
.chip.caps{letter-spacing:.5px;font-weight:600}
.anom{background:#ffedd5;color:#9a3412;border:1px solid #fdba74;cursor:help}
.flag{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;margin:0 3px 3px 0}
.flag.ok{background:#dcfce7;color:#166534;border-color:#86efac}

/* index table */
table.idx{border-collapse:collapse;width:100%;background:#fff;font-size:12.5px}
table.idx th,table.idx td{border:1px solid #e5e8ec;padding:5px 8px;text-align:left;vertical-align:top}
table.idx th{background:#f0f3f6;cursor:pointer;position:sticky;top:52px;user-select:none;white-space:nowrap}
table.idx th:hover{background:#e4e9ef}
table.idx th.sorted-asc::after{content:" \\25B2";color:#98a2b3}
table.idx th.sorted-desc::after{content:" \\25BC";color:#98a2b3}
table.idx tr:nth-child(even) td{background:#fafbfc}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.flagcell{max-width:340px}
.pctbar{display:inline-block;min-width:38px}
.warn{color:#b42318;font-weight:600}

/* doc page two-column */
.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0;align-items:start}
.col{min-width:0}
.col h2{position:sticky;top:52px;margin:0;padding:7px 12px;font-size:12px;font-weight:700;
  text-transform:uppercase;letter-spacing:.6px;color:#475467;background:#eef1f4;border-bottom:1px solid #dde1e6;z-index:3}
.colL{border-right:2px solid #dde1e6}

/* left raw rows */
.rrow{display:block;padding:4px 10px 4px 8px;border-bottom:1px solid #eef0f2;border-left:4px solid transparent}
.rrow.in-elem{border-left-color:#22c55e;background:#f6fdf8}
.rrow.dropped{border-left-color:#9ca3af;background:#f7f7f8}
.rrow.unacct{border-left-color:#ef4444;background:#fff5f5}
.rrow .pos{display:inline-block;min-width:34px;color:#98a2b3;font-size:11px;text-align:right;
  margin-right:6px;font-variant-numeric:tabular-nums}
.rrow .sid{color:#8a94a6;font-size:10.5px;margin-right:4px}
.rrow .rtext{white-space:pre-wrap;word-break:break-word}
.rrow details summary{cursor:pointer;color:#1a56b0;font-size:11px;display:inline}
.rrow .droptip{color:#6b7280;font-size:10.5px;font-style:italic}

/* right parsed elements */
.doc{padding:14px 16px 60px}
.el{position:relative;margin:0 0 3px}
.el .pos{position:absolute;left:-2px;top:-8px;font-size:9px;color:#b6bcc7;font-variant-numeric:tabular-nums}
.el .body{padding-left:4px}
.el-frontmatter .body{color:#98a2b3;font-size:11.5px}
.el-title .body{font-size:19px;font-weight:700;margin:8px 0 4px}
.el-heading .body{font-weight:700;color:#101828;margin:10px 0 3px}
.el-preambular .body{font-style:italic;margin:2px 0}
.el-operative .body{margin:2px 0}
.el-unknown .body{color:#98a2b3;background:#f4f4f5;border:1px dashed #d0d5dd;padding:2px 5px}
.el-footnote .body{font-size:11px;color:#475467}
.leadv{font-style:italic;font-weight:700;color:#6b21a8}
.opref{font-weight:600;color:#334155;margin-right:5px}
.in-annex{border-left:3px solid #c7cdd6;padding-left:8px;margin-left:2px}
.annex-sep{margin:16px 0 6px;padding-top:10px;border-top:2px solid #cbd2db}
.annex-sep .body{font-weight:700;color:#344054;text-transform:uppercase;letter-spacing:.5px;font-size:13px}
.tbl{border-collapse:collapse;font-size:11px;margin:3px 0}
.tbl td{border:1px solid #d0d5dd;padding:2px 5px;vertical-align:top}
.endblock{margin-top:22px;padding-top:8px;border-top:1px dashed #cbd2db}
.endblock h3{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#667085;margin:8px 0 4px}
.endblock li{margin:1px 0;color:#475467;font-size:11.5px}
.notparsed{padding:20px;color:#98a2b3;font-style:italic}
.metaline{font-size:11.5px;color:#667085;margin-top:3px}
"""

SORT_JS = """
function sortTable(th){
  var table=th.closest('table'),tb=table.tBodies[0],
      idx=Array.prototype.indexOf.call(th.parentNode.children,th),
      num=th.dataset.num==='1',
      cur=th.classList.contains('sorted-asc'),dir=cur?-1:1;
  Array.prototype.forEach.call(table.tHead.rows[0].cells,function(c){c.classList.remove('sorted-asc','sorted-desc');});
  th.classList.add(dir===1?'sorted-asc':'sorted-desc');
  var rows=Array.prototype.slice.call(tb.rows);
  rows.sort(function(a,b){
    var x=a.cells[idx].dataset.k!==undefined?a.cells[idx].dataset.k:a.cells[idx].textContent.trim(),
        y=b.cells[idx].dataset.k!==undefined?b.cells[idx].dataset.k:b.cells[idx].textContent.trim();
    if(num){x=parseFloat(x)||0;y=parseFloat(y)||0;return (x-y)*dir;}
    return x.localeCompare(y)*dir;
  });
  rows.forEach(function(r){tb.appendChild(r);});
}
document.addEventListener('click',function(e){if(e.target.tagName==='TH')sortTable(e.target);});
"""


# ---------------------------------------------------------------------------
# left column (raw) rendering
# ---------------------------------------------------------------------------

def render_chips(row: dict) -> str:
    chips: list[str] = []
    num = row.get("numbering")
    if num:
        fmt = num.get("num_fmt") or num.get("lvl_text") or "num"
        ilvl = num.get("ilvl")
        chips.append(f'<span class="chip">#{esc(fmt)}{"·L" + str(ilvl) if ilvl is not None else ""}</span>')
    props = row.get("props") or {}
    lit = props.get("lead_italic_text")
    if lit:
        chips.append(f'<span class="chip it">{esc(lit)}</span>')
    tabs = props.get("tabs_leading")
    if tabs:
        chips.append(f'<span class="chip">&raquo;{esc(tabs)}</span>')
    if props.get("indent_left"):
        chips.append(f'<span class="chip">&#9635;{esc(props["indent_left"])}</span>')
    if props.get("indent_hanging"):
        chips.append(f'<span class="chip">&#8676;{esc(props["indent_hanging"])}</span>')
    al = props.get("alignment")
    if al:
        chips.append(f'<span class="chip">&equiv;{esc(al)}</span>')
    if props.get("italic"):
        chips.append('<span class="chip it">i</span>')
    if props.get("bold"):
        chips.append('<span class="chip">b</span>')
    if props.get("all_caps") or props.get("caps"):
        chips.append('<span class="chip caps">CAPS</span>')
    hl = row.get("hyperlinks")
    if hl:
        chips.append(f'<span class="chip">&#128279;{len(hl)}</span>')
    fn = row.get("footnote_ref")
    if fn and row.get("kind") != "footnote":
        chips.append('<span class="chip">fn-ref</span>')
    return "".join(chips)


def render_text_cell(text: str) -> str:
    text = text or ""
    if len(text) <= TRUNCATE_AT:
        return f'<span class="rtext">{esc(text)}</span>'
    head = esc(text[:TRUNCATE_AT])
    rest = esc(text[TRUNCATE_AT:])
    return (f'<span class="rtext">{head}</span>'
            f'<details style="display:inline"><summary>… (+{len(text) - TRUNCATE_AT} chars)</summary>'
            f'<span class="rtext">{rest}</span></details>')


def render_left(raw: list[dict], an: dict) -> str:
    pos_in_elem = an["pos_in_elem"]
    pos_dropped = an["pos_dropped"]
    unaccounted = an["unaccounted"]
    out = ['<div class="col colL"><h2>RAW &nbsp;&middot;&nbsp; ' + str(an["total"]) + ' rows</h2>']
    for row in raw:
        pos = int(row["position"])
        if pos in pos_in_elem:
            cls, tip = "in-elem", f'in element #{pos_in_elem[pos]}'
        elif pos in pos_dropped:
            cls, tip = "dropped", f'dropped: {pos_dropped[pos]}'
        elif pos in unaccounted:
            cls, tip = "unacct", "UNACCOUNTED — no element claims this position and it was not dropped"
        else:
            cls, tip = "", ""
        kind = row.get("kind") or ""
        sid = row.get("style_id")
        drop_note = ""
        if cls == "dropped":
            drop_note = f' <span class="droptip">[dropped: {esc(pos_dropped[pos])}]</span>'
        out.append(
            f'<div class="rrow {cls}" title="{esc(tip)}">'
            f'<span class="pos">{pos}</span>'
            f'<span class="badge kind kind-{esc(kind)}">{esc(kind)}</span> '
            + (f'<span class="sid mono">{esc(sid)}</span> ' if sid else "")
            + render_chips(row)
            + " " + render_text_cell(row.get("text") or "")
            + drop_note
            + "</div>"
        )
    out.append("</div>")
    return "".join(out)


# ---------------------------------------------------------------------------
# right column (parsed) rendering
# ---------------------------------------------------------------------------

def render_preambular_body(el: dict) -> str:
    text = str(el.get("text") or "")
    lead = el.get("lead_verb")
    if lead and text.strip().lower().startswith(str(lead).strip().lower()):
        n = len(str(lead))
        # preserve original leading whitespace
        lead_span = f'<span class="leadv">{esc(text[:n])}</span>'
        return lead_span + esc(text[n:])
    if lead:
        return f'<span class="leadv">{esc(lead)}</span> {esc(text)}'
    return esc(text)


def render_table_body(el: dict) -> str:
    rows = el.get("rows") or el.get("cells")
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        trs = []
        for r in rows:
            tds = "".join(f"<td>{esc(c)}</td>" for c in r)
            trs.append(f"<tr>{tds}</tr>")
        return f'<span class="badge kind kind-table_cell">table</span><table class="tbl"><tbody>{"".join(trs)}</tbody></table>'
    return f'<span class="badge kind kind-table_cell">table</span> {esc(el.get("text"))}'


def render_anoms(anoms: list[tuple[str, str]]) -> str:
    return "".join(f'<span class="badge anom" title="{esc(expl)}">&#9888; {esc(label)}</span> '
                   for label, expl in anoms)


def render_right(an: dict) -> str:
    elements = an["elements"]
    anoms_all = an["elem_anoms"]
    out = [f'<div class="col"><h2>PARSED &nbsp;&middot;&nbsp; {len(elements)} elements</h2><div class="doc">']

    footnotes: list[tuple[int, dict]] = []
    for i, el in enumerate(elements):
        role = role_of(el)
        if role == "footnote":
            footnotes.append((i, el))
            continue
        sec = str(el.get("section") or "").lower()
        classes = ["el", f"el-{role}"]
        # annex/appendix heading gets a strong separator; body elements get a border
        if role in ("annex", "appendix") or sec in ("annex", "appendix"):
            if role in ("annex", "appendix") or (str(el.get("type") or "").lower() == "heading" and sec in ("annex", "appendix")):
                classes.append("annex-sep")
            else:
                classes.append("in-annex")

        pos_sup = compress_positions(el.get("positions") or [])
        # body content by role
        if role == "operative":
            pref = el.get("prefix")
            lvl = el.get("level") if isinstance(el.get("level"), int) else 0
            indent = f'style="margin-left:{min(int(lvl), 6) * 22}px"'
            pref_html = f'<span class="opref">{esc(pref)}</span>' if pref else ""
            body = f'{pref_html}{esc(el.get("text"))}'
            body_wrap = f'<div class="body" {indent}>{body}</div>'
        elif role == "preambular":
            body_wrap = f'<div class="body">{render_preambular_body(el)}</div>'
        elif role == "heading":
            hl = el.get("heading_level")
            size = {1: 17, 2: 15, 3: 13.5}.get(hl if isinstance(hl, int) else 2, 13)
            body_wrap = f'<div class="body" style="font-size:{size}px">{esc(el.get("text"))}</div>'
        elif role == "table":
            body_wrap = f'<div class="body">{render_table_body(el)}</div>'
        elif role == "title":
            body_wrap = f'<div class="body">{esc(el.get("text"))}</div>'
        elif role == "frontmatter":
            tag = ('<span class="badge kind kind-section_break">masthead</span> '
                   if el.get("subtype") == "masthead" else "")
            body_wrap = f'<div class="body">{tag}{esc(el.get("text"))}</div>'
        elif str(el.get("type") or "").lower() == "vote_record":
            vote = el.get("vote") or {}
            parts = []
            for key, label in (("in_favour", "In favour"), ("against", "Against"),
                               ("abstaining", "Abstaining"), ("non_voting", "Non-voting"),
                               ("absent", "Absent")):
                lst = vote.get(key)
                if lst:
                    parts.append(f'<div><b>{label}</b> ({len(lst)}): {esc(", ".join(lst))}</div>')
            vs = el.get("vote_summary")
            summ = (f' <span class="mono">[{vs["in_favour"]}-{vs["against"]}-{vs["abstaining"]}]</span>'
                    if isinstance(vs, dict) else "")
            body_wrap = (f'<div class="body"><span class="badge kind">vote</span>{summ} '
                         f'{esc(el.get("text"))}{"".join(parts)}</div>')
        elif role == "unknown":
            t = el.get("type") or "?"
            body_wrap = f'<div class="body"><span class="mono">[type={esc(t)}]</span> {esc(el.get("text"))}</div>'
        else:
            body_wrap = f'<div class="body">{esc(el.get("text"))}</div>'

        anom_html = render_anoms(anoms_all[i]) if i < len(anoms_all) else ""
        sup = f'<span class="pos">{esc(pos_sup)}</span>' if pos_sup else ""
        out.append(f'<div class="{" ".join(classes)}">{sup}{anom_html}{body_wrap}</div>')

    # footnotes block
    if footnotes:
        out.append('<div class="endblock"><h3>Footnotes</h3><ol>')
        for i, el in footnotes:
            sup = compress_positions(el.get("positions") or [])
            anom_html = render_anoms(anoms_all[i]) if i < len(anoms_all) else ""
            out.append(f'<li class="el-footnote">{anom_html}{esc(el.get("text"))} '
                       + (f'<span class="pos">{esc(sup)}</span>' if sup else "") + "</li>")
        out.append("</ol></div>")

    # dropped + issues
    if an["dropped"]:
        out.append(f'<div class="endblock"><h3>Dropped ({len(an["dropped"])})</h3><ul>')
        for d in an["dropped"][:200]:
            out.append(f'<li>pos {esc(d.get("position"))}: {esc(d.get("reason"))}</li>')
        out.append("</ul></div>")
    if an["issues"]:
        out.append(f'<div class="endblock"><h3 class="warn">Issues ({len(an["issues"])})</h3><ul>')
        for iss in an["issues"]:
            out.append(f'<li>{esc(iss)}</li>')
        out.append("</ul></div>")

    out.append("</div></div>")
    return "".join(out)


# ---------------------------------------------------------------------------
# page assembly
# ---------------------------------------------------------------------------

def fmt_badge(fmt: str | None) -> str:
    f = (fmt or "none").lower()
    return f'<span class="badge fmt-{esc(f)}">{esc(f)}</span>'


def flags_html(flags: list[str]) -> str:
    if not flags:
        return '<span class="badge flag ok">clean</span>'
    return "".join(f'<span class="badge flag">{esc(f)}</span>' for f in flags)


def render_doc_page(symbol: str, lang: str, raw: list[dict], parsed: dict | None,
                    ledger: dict, an: dict) -> str:
    fmt = ledger.get("format")
    status = ledger.get("status")
    parser_ver = (parsed or {}).get("parser_version", "—")
    meta = (f'{fmt_badge(fmt)} <span class="mono">{esc(status or "?")}</span> '
            f'&middot; {an["total"]} raw rows &middot; {len(an["elements"])} elements '
            f'&middot; {an["pct_accounted"]}% accounted &middot; '
            f'{an["n_operative"]} operative / {an["n_preambular"]} preambular '
            f'&middot; parser {esc(parser_ver)}')
    if ledger.get("error"):
        meta += f' &middot; <span class="warn">ledger error: {esc(ledger["error"])}</span>'

    left = render_left(raw, an)
    if parsed is None:
        right = ('<div class="col"><h2>PARSED</h2>'
                 '<div class="notparsed">No parsed JSON found for this document '
                 f'(<span class="mono">{esc(sanitize_symbol(symbol))}.json</span> missing). '
                 'Raw column shown for reference.</div></div>')
    else:
        right = render_right(an)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(symbol)} — review</title><style>{CSS}</style></head><body>
<header class="top"><h1>{esc(symbol)} <span style="font-weight:400;color:#98a2b3">({esc(family_of(symbol))})</span></h1>
<div class="metaline">{meta}</div>
<div class="metaline">{flags_html(an["flags"])} &nbsp; <a href="index.html">&larr; index</a></div></header>
<div class="cols">{left}{right}</div>
</body></html>"""


def render_index(records: list[dict]) -> str:
    head = ("<tr>"
            "<th>symbol</th><th>family</th><th>fmt</th>"
            "<th data-num=1>raw</th><th data-num=1>elems</th>"
            "<th data-num=1>% acct</th><th data-num=1>unacct</th>"
            "<th data-num=1>dropped</th><th data-num=1>drop*</th><th data-num=1>issues</th>"
            "<th>annex</th><th>red flags</th></tr>")
    body = []
    for r in records:
        an = r["an"]
        pct = an["pct_accounted"]
        pct_cls = ' class="warn"' if pct < 99.9 else ""
        flagcell = flags_html([f for f in an["flags"]])
        body.append(
            "<tr>"
            f'<td><a href="{esc(r["href"])}">{esc(r["symbol"])}</a></td>'
            f'<td>{esc(r["family"])}</td>'
            f'<td>{fmt_badge(r["format"])}</td>'
            f'<td class="num">{an["total"]}</td>'
            f'<td class="num">{len(an["elements"])}</td>'
            f'<td class="num"{pct_cls} data-k="{pct}">{pct}</td>'
            f'<td class="num" data-k="{an["n_unaccounted"]}">{an["n_unaccounted"]}</td>'
            f'<td class="num">{an["n_dropped"]}</td>'
            f'<td class="num">{an["n_dropped_meaningful"]}</td>'
            f'<td class="num">{len(an["issues"])}</td>'
            f'<td data-k="{1 if an["has_annex"] else 0}">{"yes" if an["has_annex"] else "—"}</td>'
            f'<td class="flagcell" data-k="{len(an["flags"])}">{flagcell}</td>'
            "</tr>"
        )
    total_docs = len(records)
    clean = sum(1 for r in records if not r["an"]["flags"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fulltext parser review — index</title><style>{CSS}</style></head><body>
<header class="top"><h1>Fulltext parser review</h1>
<div class="sub">{total_docs} documents &middot; {clean} clean &middot; {total_docs - clean} with red flags
 &middot; click a column header to sort</div></header>
<div class="wrap"><table class="idx"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>
<script>{SORT_JS}</script></body></html>"""


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Static HTML review harness for the fulltext parser.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="output directory")
    ap.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR,
                    help="directory of parsed JSONs (default: parsed_dev on the SSD)")
    ap.add_argument("--symbols", nargs="*", help="restrict to these symbol_normalized values")
    ap.add_argument("--limit", type=int, help="cap number of documents rendered")
    args = ap.parse_args()

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir: Path = args.parsed_dir

    conn = get_conn()
    try:
        pairs = load_symbols(conn, args.symbols, args.limit)
        if not pairs:
            print("No documents with raw rows matched. Nothing to render.", file=sys.stderr)
            # still write an empty index so downstream tooling has a file
            (out_dir / "index.html").write_text(render_index([]), encoding="utf-8")
            (out_dir / "_flags.json").write_text("{}", encoding="utf-8")
            return 0

        symbols = [s for s, _ in pairs]
        ledger = load_ledger(conn, symbols)

        records: list[dict] = []
        flags_map: dict[str, list[str]] = {}
        for symbol, lang in pairs:
            raw = load_raw(conn, symbol, lang)
            parsed = load_parsed(parsed_dir, symbol)
            an = analyze(symbol, raw, parsed)
            led = ledger.get(symbol, {})
            href = f"{sanitize_symbol(symbol)}.html"
            page = render_doc_page(symbol, lang, raw, parsed, led, an)
            (out_dir / href).write_text(page, encoding="utf-8")
            records.append({
                "symbol": symbol, "family": family_of(symbol),
                "format": led.get("format"), "href": href, "an": an,
            })
            flags_map[symbol] = an["flags"]
            print(f"  {symbol:<22} {len(raw):>5} raw  {len(an['elements']):>4} elems  "
                  f"{an['pct_accounted']:>5}% acct  flags={an['flags']}")

        (out_dir / "index.html").write_text(render_index(records), encoding="utf-8")
        (out_dir / "_flags.json").write_text(
            json.dumps(flags_map, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"\nWrote {len(records)} doc pages + index.html + _flags.json to {out_dir}")
        if parsed_dir.exists():
            n_json = len(list(parsed_dir.glob('*.json')))
            print(f"(parsed-dir {parsed_dir} holds {n_json} JSON files)")
        else:
            print(f"(parsed-dir {parsed_dir} does not exist — all docs render 'not parsed')")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
