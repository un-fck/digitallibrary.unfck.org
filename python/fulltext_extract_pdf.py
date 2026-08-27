#!/usr/bin/env python3
"""Raw paragraph extractor for the DETERMINISTIC PDF path (Track A, pre-1994).

The Word path (`fulltext_extract_raw.py`) turns archived `.docx` into
`document_paragraphs_raw`. This is its PDF twin: it turns archived `.pdf` files
(pre-1994 documents that have no Word source on ODS) into the SAME raw-row
contract, so the FROZEN semantic parser (`fulltext_parse.py`, sem-v2) can consume
them through its style-less lexical path with no changes.

NO LLM anywhere. Everything here is deterministic geometry + lexical patterns.

The pre-1994 PDFs are of three kinds:
  * born-digital modern PDFs (~1990-1993): a clean embedded text layer, one
    resolution per file, a UN masthead front;
  * scanned compilation-volume EXCERPTS (older): an OCR text layer of variable
    quality, laid out as pages of a "Resolutions adopted ..." supplement — so a
    file's page typically shows the END of the previous resolution, the target
    resolution, and the START of the next one, under a running page header;
  * pure image scans: NO text layer at all — unrecoverable without OCR, excluded.

Pipeline per document (all deterministic):
  1. TRIAGE the text layer -> class 'text' | 'poor' | 'none'. 'none' (pure scan)
     is recorded status='no_text_layer' and skipped. The triage score rides along
     on every emitted row's props (textlayer_score) and in the ledger error field.
  2. EXTRACT spans -> lines (pymupdf), DROP running headers/footers/page numbers
     (position + repetition + pattern), reconstruct PARAGRAPHS by column left-edge
     indent + vertical gaps + terminal punctuation, repairing end-of-line
     hyphenation conservatively.
  3. CROP to the target resolution inside the excerpt (its own number heading ..
     its adoption record / the next resolution's heading). Never silently
     truncate: if the crop anchor is uncertain, keep everything and flag it.
  4. EMIT `document_paragraphs_raw` rows matching the docx contract
     (kind='paragraph'/'empty'; props carry size/bold/italic/indent/all_caps/
     alignment/lead_italic_text plus pdf=true and textlayer_score;
     extractor_version='pdf-v1'), advancing the ledger 'fetched' -> 'extracted'.

Then run the existing parser over these docs (uv run python python/fulltext_parse.py)
exactly as for Word docs — it is format-agnostic in its lexical patterns.

Usage:
    uv run python python/fulltext_extract_pdf.py
    uv run python python/fulltext_extract_pdf.py --limit 20
    uv run python python/fulltext_extract_pdf.py --symbols A/RES/1260(XIII),S/RES/338(1973)
    uv run python python/fulltext_extract_pdf.py --force   # re-extract 'extracted'/'no_text_layer'
    uv run python python/fulltext_extract_pdf.py --symbols ... --debug   # dump crop + rows, no DB write
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # pymupdf
from psycopg.types.json import Jsonb

from fulltext_common import ARCHIVE_ROOT, get_conn, upsert_document_file

EXTRACTOR_VERSION = "pdf-v1"
BATCH_DOCS = 20

# pymupdf span flag bits.
FLAG_ITALIC = 1 << 1   # 2
FLAG_BOLD = 1 << 4     # 16

# ---------------------------------------------------------------------------
# Triage vocabulary — a small built-in common-word list. Deliberately tiny and
# stdlib-only: enough to tell real English OCR from garbage, not a spell checker.
# ---------------------------------------------------------------------------
COMMON_WORDS = frozenset("""
the of to and a in that is was he for it with as his on be at by i this had not
are but from or have an they which one you were her all she there would their we
him been has when who will more no if out so up said what its about into than them
can only other new some could time these two may then do first any my now such like
our over man me even most made after also did many before must through back years
where much your way well down should because each just those people mr how too little
state good very make world still see men work long get here between both life being
under general assembly security council economic social nations united resolution
resolutions decides requests recalling reaffirming noting recognizing welcoming
considering having report committee member states international peace secretary
adopted meeting plenary session document decision decisions organization
government development rights human question situation present agenda organizations
information programme co-operation cooperation
""".split())


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]{2,}", text)


# ---------------------------------------------------------------------------
# Facing-language (French) line detection — for the old bilingual GA/ECOSOC
# supplement volumes, where an English column faces a French one on the same
# page. We drop French-only lines from BOTH the body and the verify ground truth
# so the interleaved French column does not truncate the English crop or flood the
# gate with expected-loss French words. Deliberately a tiny, high-precision set of
# French FUNCTION words that essentially never occur in English UN prose; a line is
# 'French' only when it carries >=3 of them (so an English line quoting one French
# name is never dropped).
# ---------------------------------------------------------------------------
FRENCH_STOPWORDS = frozenset("""
le la les des du et aux une dans par qui que pour avec sur ses leur leurs cette
ces entre ainsi dont sont elle ils nous vous tous comme sans sous deux cet celle
ceux votre notre seance pleniere economique institutions specialisees
renseignements secretaire egalement competentes territoires autonomes assemblee
generale conseil comite novembre decembre janvier fevrier avril juin juillet
septembre octobre adoptee mondiale examine informer presenter maintenir
""".split())

_FR_TOKEN = re.compile(r"[a-zà-ÿ']+")


def french_line(text: str) -> bool:
    """True for a line that is clearly French facing-language content."""
    toks = _FR_TOKEN.findall(text.lower())
    if len(toks) < 4:
        return False
    hits = sum(1 for t in toks if t in FRENCH_STOPWORDS)
    return hits >= 3


@dataclass
class Triage:
    chars_per_page: float
    alnum_ratio: float
    dict_hit_rate: float
    garbage_ratio: float
    klass: str  # 'text' | 'poor' | 'none'

    def as_props(self) -> dict:
        return {
            "class": self.klass,
            "chars_per_page": round(self.chars_per_page, 1),
            "alnum_ratio": round(self.alnum_ratio, 3),
            "dict_hit_rate": round(self.dict_hit_rate, 3),
            "garbage_ratio": round(self.garbage_ratio, 3),
        }

    def summary(self) -> str:
        return (f"class={self.klass} cpp={self.chars_per_page:.0f} "
                f"alnum={self.alnum_ratio:.2f} dict={self.dict_hit_rate:.2f} "
                f"garbage={self.garbage_ratio:.2f}")


def triage_text(pages_text: list[str]) -> Triage:
    """Score the embedded text layer and classify it.

    'none' — essentially no text (pure image scan): skip.
    'text' — clean enough to trust (born-digital or good OCR).
    'poor' — marginal OCR: extract anyway, but flag so the acceptance gate can
             hold it to a lower bar.
    """
    n_pages = max(len(pages_text), 1)
    full = "\n".join(pages_text)
    n_chars = len(full)
    chars_per_page = n_chars / n_pages

    letters = sum(c.isalpha() for c in full)
    alnum = sum(c.isalnum() or c.isspace() for c in full)
    alnum_ratio = alnum / n_chars if n_chars else 0.0

    toks = _tokens(full)
    n_tok = len(toks)
    if n_tok:
        hits = sum(1 for t in toks if t.lower() in COMMON_WORDS)
        dict_hit_rate = hits / n_tok
        # "garbage" tokens: contain no vowel, or mix letters with stray marks, or
        # have improbable long consonant/again runs — OCR debris like 'HE~(l"Tl'.
        garbage = 0
        for t in toks:
            tl = t.lower()
            if not re.search(r"[aeiouy]", tl):
                garbage += 1
            elif re.search(r"[^a-z]", tl):
                garbage += 1
            elif re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", tl):
                garbage += 1
        garbage_ratio = garbage / n_tok
    else:
        dict_hit_rate = 0.0
        garbage_ratio = 1.0

    # Classification thresholds (calibrated on the stratified sample).
    if chars_per_page < 80 or n_tok < 20:
        klass = "none"
    elif dict_hit_rate >= 0.30 and alnum_ratio >= 0.80 and garbage_ratio <= 0.30:
        klass = "text"
    else:
        klass = "poor"
    return Triage(chars_per_page, alnum_ratio, dict_hit_rate, garbage_ratio, klass)


# ---------------------------------------------------------------------------
# Line model
# ---------------------------------------------------------------------------

@dataclass
class Line:
    text: str
    x0: float
    x1: float
    y0: float
    y1: float
    size: float
    bold: bool
    italic: bool
    lead_italic_text: str | None
    page: int
    cleft: float = 0.0   # left edge of this line's column (set after column split)
    cright: float = 0.0  # right edge of this line's column


def _span_style(span: dict) -> tuple[bool, bool]:
    font = (span.get("font") or "").lower()
    flags = span.get("flags", 0)
    bold = bool(flags & FLAG_BOLD) or "bold" in font or "black" in font
    italic = bool(flags & FLAG_ITALIC) or "italic" in font or "oblique" in font
    return bold, italic


def extract_lines(page: fitz.Page, page_no: int) -> list[Line]:
    """One Line per pymupdf text line, with geometry + majority font style."""
    d = page.get_text("dict")
    out: list[Line] = []
    for block in d.get("blocks", []):
        if "lines" not in block:
            continue  # image block
        for ln in block["lines"]:
            spans = [s for s in ln["spans"] if (s.get("text") or "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in ln["spans"])
            if not text.strip():
                continue
            xs0 = [s["bbox"][0] for s in spans]
            xs1 = [s["bbox"][2] for s in spans]
            ys0 = [s["bbox"][1] for s in spans]
            ys1 = [s["bbox"][3] for s in spans]
            # weighted-majority size / style by span text length
            size_weight: dict[float, int] = {}
            bold_w = italic_w = total_w = 0
            for s in spans:
                w = len(s["text"].strip()) or 1
                sz = round(s["size"] * 2) / 2
                size_weight[sz] = size_weight.get(sz, 0) + w
                b, it = _span_style(s)
                bold_w += w if b else 0
                italic_w += w if it else 0
                total_w += w
            size = max(size_weight, key=size_weight.get)
            bold = total_w and bold_w >= 0.6 * total_w
            italic = total_w and italic_w >= 0.6 * total_w
            # leading italic run text (preambular-verb signal; rare on old scans)
            lead_it = None
            if not italic:
                buf: list[str] = []
                for s in spans:
                    _, it = _span_style(s)
                    if it:
                        buf.append(s["text"])
                    elif s["text"].strip():
                        break
                lead = "".join(buf).strip()
                if lead:
                    lead_it = lead
            out.append(Line(
                text=text.strip(), x0=min(xs0), x1=max(xs1),
                y0=min(ys0), y1=max(ys1), size=size, bold=bool(bold),
                italic=bool(italic), lead_italic_text=lead_it, page=page_no))
    return out


# ---------------------------------------------------------------------------
# Header / footer / page-artifact detection
# ---------------------------------------------------------------------------

_PAGE_NUM = re.compile(r"^[\[\(]?\s*-?\s*\d{1,4}\s*-?\s*[\]\)]?\.?$")
_RULE = re.compile(r"^[\-_—–.\s·]{4,}$|^/\s*\.{2,}$")   # rules + "/..." continued marker
_DOC_SYMBOL = re.compile(r"^[\[\(]?[A-Z]{1,4}/[A-Z0-9/().,\-\[\]]+$")
_PAGE_LABEL = re.compile(r"^Page\s+\d+$", re.I)
# Running header of a compilation volume ("General Assembly—Thirteenth Session",
# "Resolutions adopted on the reports of the First Committee").
_RUN_HEADER = re.compile(
    r"(General Assembly|Security Council|Economic and Social Council|"
    r"Trusteeship Council)\b.*\bsession\b"
    r"|^\s*(?:[IVXLC]+\.?\s+)?Resolutions?\s+(adopted|and Decisions)\b"
    r"|^\s*Resolution[s]?\W+adopted\b", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", text)).strip().lower()


def _is_static_artifact(text: str) -> bool:
    t = text.strip()
    return bool(_PAGE_NUM.match(t) or _RULE.match(t) or _DOC_SYMBOL.match(t)
                or _PAGE_LABEL.match(t) or _RUN_HEADER.search(t))


def drop_headers_footers(lines: list[Line], page_heights: dict[int, float]) -> tuple[list[Line], list[str]]:
    """Remove running headers/footers, page numbers and separator rules.

    Two signals combined: BAND (a line near the very top/bottom of its page) and
    either (a) a static artifact pattern (page number / doc symbol / rule /
    compilation running-header), or (b) REPETITION — the same de-digited text in
    the same band on >=2 pages. Body lines are never touched here (the parser owns
    body classification)."""
    top_band: dict[str, int] = {}
    bot_band: dict[str, int] = {}
    band: dict[int, str] = {}
    for idx, ln in enumerate(lines):
        H = page_heights.get(ln.page, 800.0)
        if ln.y0 < H * 0.11:
            band[idx] = "top"
            top_band[_norm(ln.text)] = top_band.get(_norm(ln.text), 0) + 1
        elif ln.y1 > H * 0.93:
            band[idx] = "bot"
            bot_band[_norm(ln.text)] = bot_band.get(_norm(ln.text), 0) + 1

    kept: list[Line] = []
    dropped: list[str] = []
    for idx, ln in enumerate(lines):
        b = band.get(idx)
        if b is not None:
            n = _norm(ln.text)
            repeated = (top_band if b == "top" else bot_band).get(n, 0) >= 2 and len(n) > 3
            if _is_static_artifact(ln.text) or repeated:
                dropped.append(f"[{b}] {ln.text[:60]}")
                continue
        kept.append(ln)
    return kept, dropped


# ---------------------------------------------------------------------------
# Column detection (old supplements are single-column; guard for 2-column)
# ---------------------------------------------------------------------------

def _row_sort(lines: list[Line]) -> list[Line]:
    """Order lines top-to-bottom, but group spans of the same visual row (within a
    ~4pt band) left-to-right — so a hanging marker '1.' precedes its text line."""
    return sorted(lines, key=lambda l: (round(l.y0 / 4.0), l.x0))


def _set_edges(group: list[Line]) -> None:
    """Stamp each line with its column's left/right edges (robust percentiles)."""
    if not group:
        return
    cleft = _percentile([l.x0 for l in group], 0.12)
    cright = _percentile([l.x1 for l in group], 0.88)
    for l in group:
        l.cleft, l.cright = cleft, cright


def split_columns(lines: list[Line], page_width: float) -> list[list[Line]]:
    """Return reading-ordered line groups and stamp per-column edges.

    Two columns are detected by a GUTTER: a vertical strip in the central region
    that almost no line box crosses. A horizontal coverage histogram over x finds
    the emptiest column in [0.35·W, 0.62·W]; if its coverage is near zero and both
    sides hold substantial text, that x is the divider. This distinguishes a real
    two-column supplement page (empty gutter) from a hanging-number layout
    (number at x≈108, text at x≈144, body at x≈72 — no gutter, lines span the
    centre). Reading order is left column fully, then right column."""
    n = len(lines)
    if n < 12:
        _set_edges(lines)
        return [_row_sort(lines)]
    bin_w = 4.0
    nbins = int(page_width / bin_w) + 2
    cov = [0] * nbins
    for l in lines:
        a = int(l.x0 / bin_w)
        b = int(min(l.x1, page_width) / bin_w)
        for k in range(max(a, 0), min(b, nbins - 1) + 1):
            cov[k] += 1
    maxc = max(cov) or 1
    lo, hi = int(0.35 * page_width / bin_w), int(0.62 * page_width / bin_w)
    gutter = min(range(lo, hi), key=lambda k: cov[k]) if hi > lo else lo
    divider = gutter * bin_w
    col_l = [l for l in lines if (l.x0 + l.x1) / 2 < divider]
    col_r = [l for l in lines if (l.x0 + l.x1) / 2 >= divider]
    if cov[gutter] <= 0.06 * maxc and len(col_l) >= 0.2 * n and len(col_r) >= 0.2 * n:
        _set_edges(col_l)
        _set_edges(col_r)
        return [_row_sort(col_l), _row_sort(col_r)]
    _set_edges(lines)
    return [_row_sort(lines)]


# ---------------------------------------------------------------------------
# Structural-start patterns (shared vocabulary with the parser)
# ---------------------------------------------------------------------------

OPENING_RE = re.compile(
    r"^[\"“”‘’']?\s*The\s+(General Assembly|Security Council|"
    r"Economic and Social Council|Human Rights Council|Trusteeship Council)\s*,?\s*$")
OP_NUM_RE = re.compile(r"^\(?\s*(\d{1,3})\s*\.\s+\S")
OP_PAREN_RE = re.compile(r"^\(\s*([A-Za-z]{1,7}|\d{1,3})\s*\)\s+\S")
MEETING_RE = re.compile(r"^\[?\s*\d+\s*(st|nd|rd|th|II\b|d\b)?\s*(plenary\s+)?meeting\b", re.I)
ADOPTED_RE = re.compile(r"^\[?\s*Adopted\b", re.I)
DATE_RE = re.compile(r"^\d{1,2}\s+[A-Za-z]+\s+\d{4}\.?\s*$")
PREAMBULAR_FIRST = frozenset("""
recalling reaffirming noting recognizing recognising welcoming considering
convinced concerned emphasizing emphasising expressing guided having bearing
taking mindful alarmed acknowledging determined determining stressing underlining
underscoring desiring desirous aware regretting deploring affirming observing
believing conscious deeply gravely firmly fully further reiterating seeking
encouraged endorsing commending appreciating anxious cognizant remaining keeping
realizing supporting invoking highlighting confident hopeful resolved eager
grateful pleased sharing aiming inspired""".split())
# resolution number heading (old GA/ECOSOC "1260 (XIII)."; SC "338 (1973).";
# modern "48/23.") — used to find neighbour boundaries.
HEADING_ROMAN_RE = re.compile(r"^\(?\s*(\d{1,4})\s*\(\s*([A-Za-z0-9]{1,8})\s*\)\s*\.")
HEADING_SLASH_RE = re.compile(r"^\s*([A-Z]?-?\d{1,4})/(\d{1,4}[A-Za-z]*)\s*\.")
HEADING_SC_RE = re.compile(r"^\s*Resolution\s+(\d{1,4})\s*\(\s*(\d{4})\s*\)", re.I)
DECISION_HEAD_RE = re.compile(r"^Decisions?\s*$", re.I)
# A compilation 'Decisions' NARRATIVE block header: the SC "Resolutions and
# Decisions" volumes run 'Decision(s) At its Nth meeting, ... the Council decided'
# blocks straight after a resolution's operative tail (the adoption record itself
# often lives in the small-font footnote apparatus). Ending the crop here stops the
# following Decisions narrative from bleeding into the target region.
DECISION_BLOCK_RE = re.compile(r"^Decisions?\s+(?:At|On|The|Following)\b", re.I)

# Operative lead verbs (complement PREAMBULAR_FIRST) — used only by the
# first-word lead-verb OCR repair vocabulary, never for classification.
OPERATIVE_LEAD = frozenset("""
decides requests calls demands urges reaffirms recalls invites notes expresses
condemns declares endorses authorizes approves welcomes recommends encourages
emphasizes stresses appeals appoints adopts affirms agrees commends confirms
considers deplores designates determines directs draws elects establishes
instructs proclaims regrets reiterates renews resolves supports transmits
underlines warns decides taking demanding requesting calling noting
""".split())


_ORGANS = {
    "thegeneralassembly": "The General Assembly,",
    "thesecuritycouncil": "The Security Council,",
    "theeconomicandsocialcouncil": "The Economic and Social Council,",
    "thehumanrightscouncil": "The Human Rights Council,",
    "thetrusteeshipcouncil": "The Trusteeship Council,",
}


def repair_opening(text: str) -> str:
    """Repair an OCR-garbled opening formula to its canonical form.

    The parser anchors the preamble/operative state machine on an EXACT
    'The General Assembly,' line; OCR noise ('The General Assemb/y,') breaks it
    and demotes the whole preamble to frontmatter. Only a short standalone line
    that fuzzily matches one organ formula is rewritten — body prose that merely
    starts 'The General Assembly requests ...' is far too long to match."""
    t = text.strip()
    if len(t) > 46 or not t.lower().startswith("the "):
        return text
    from difflib import SequenceMatcher
    key = re.sub(r"[^a-z]", "", t.lower())
    for canon_key, canon in _ORGANS.items():
        if SequenceMatcher(None, key, canon_key).ratio() >= 0.86:
            return canon
    return text


# ---------------------------------------------------------------------------
# Sequence-confirmed OCR marker repair + first-word lead-verb repair
# ---------------------------------------------------------------------------
# Glyphs an OCR engine commonly emits for a leading DIGIT marker. Repair fires
# ONLY when arithmetic sequence confirmation holds (the neighbouring real numeric
# markers at the same indent bracket the candidate), so a genuine roman 'I.'/'II.'
# heading — which is followed by 'II.'/'III.', not '2.' — is never rewritten.
_OCR_DIGIT = {"I": "1", "l": "1", "|": "1", "i": "1", "J": "1",
              "S": "5", "O": "0", "o": "0", "Z": "2", "B": "8"}
_CONFUSABLE_MARKER = re.compile(r"^([IlJ|iSOoZB])\.(?:\s*$|\s+\S)")
_NUM_MARKER = re.compile(r"^(\d{1,3})[.)](?:\s|$)")

# Lead-verb repair vocabulary: preambular + operative first words. First-word
# only, edit distance 1, prefix-anchored, unique — improves the parser's
# preambular/operative classification of an OCR-garbled lead verb without touching
# body text (the acceptance gate holds body text verbatim against pdftotext).
LEAD_VERB_VOCAB = PREAMBULAR_FIRST | OPERATIVE_LEAD


def _num_marker_val(text: str) -> int | None:
    m = _NUM_MARKER.match(text.strip())
    return int(m.group(1)) if m else None


# OCR-junk letters: rare in the target lead verbs, so a single substitution that
# replaces a common target letter WITH one of these is almost certainly OCR damage
# ('Recallin-g' -> 'Recallin-x', 'Gravel-y' -> 'Gravel-v'). Requiring the damaged
# letter to be junk blocks false positives on genuine inflections that differ by a
# common letter ('authorized' -> 'authorizes' d/s, 'transmis' -> 'transmits').
_JUNK_LETTERS = frozenset("xvzjq")


def repair_lead_verb(text: str) -> str | None:
    """If the FIRST word is a single-substitution OCR corruption of a UNIQUE lead
    verb — the damaged letter being an OCR-junk letter — return the text with only
    that word repaired; else None. Equal-length only, prefix-anchored (first two
    letters match), and never touches anything past the first word (body stays
    verbatim for the acceptance gate)."""
    m = re.match(r"^([A-Za-z]{5,14})(?=$|[\s,.:;])", text)
    if not m:
        return None
    w = m.group(1)
    wl = w.lower()
    if wl in LEAD_VERB_VOCAB:
        return None  # already a valid lead verb
    cands: list[str] = []
    for v in LEAD_VERB_VOCAB:
        if v[:2] != wl[:2] or len(v) != len(wl):
            continue  # equal-length single substitution only
        diffs = [(o, t) for o, t in zip(wl, v) if o != t]
        if len(diffs) != 1:
            continue
        orig, targ = diffs[0]
        if orig in _JUNK_LETTERS and targ not in _JUNK_LETTERS:
            cands.append(v)
    if len(cands) != 1:
        return None  # no unique repair -> leave verbatim
    best = cands[0]
    repl = best.capitalize() if w[0].isupper() else best
    return repl + text[len(w):]


def _repair_ocr_markers(paras: list[Para], log: list[tuple[str, str]]) -> list[Para]:
    """Rewrite a mis-OCR'd leading digit marker ('I.'->'1.', 'S.'->'5.') when the
    surrounding real numeric markers at the SAME indent arithmetically confirm it
    (previous+1 == candidate == next-1). Runs before hanging-marker merging so a
    repaired standalone '1.' merges into its clause like a native marker."""
    for i, p in enumerate(paras):
        t = p.text.strip()
        m = _CONFUSABLE_MARKER.match(t)
        if not m:
            continue
        cand = _OCR_DIGIT.get(m.group(1))
        if cand is None:
            continue
        cand_i = int(cand)
        x0 = p.x0
        nxt = prv = None
        for j in range(i + 1, min(i + 5, len(paras))):
            v = _num_marker_val(paras[j].text)
            if v is not None and abs(paras[j].x0 - x0) < 45:
                nxt = v
                break
        for j in range(i - 1, max(i - 5, -1), -1):
            v = _num_marker_val(paras[j].text)
            if v is not None and abs(paras[j].x0 - x0) < 45:
                prv = v
                break
        # Arithmetic sequence confirmation — REQUIRE a confirming next marker.
        if nxt is None or cand_i != nxt - 1:
            continue
        if prv is not None and cand_i != prv + 1:
            continue
        standalone = bool(re.fullmatch(r"[IlJ|iSOoZB]\.", t))
        after = f"{cand}." if standalone else cand + t[1:]
        p.override = after
        log.append((t, after))
    return paras


def _terminal(text: str) -> bool:
    return bool(text) and text.rstrip()[-1:] in ".,;:?!\"”’)"


def _structural_start(text: str) -> bool:
    t = text.strip()
    if OPENING_RE.match(t) or OP_NUM_RE.match(t) or OP_PAREN_RE.match(t):
        return True
    if MEETING_RE.match(t) or ADOPTED_RE.match(t) or DATE_RE.match(t):
        return True
    if HEADING_ROMAN_RE.match(t) or HEADING_SLASH_RE.match(t):
        return True
    first = re.split(r"[\s,]", t, maxsplit=1)[0].lower()
    return first in PREAMBULAR_FIRST


# ---------------------------------------------------------------------------
# Paragraph reconstruction
# ---------------------------------------------------------------------------

@dataclass
class Para:
    lines: list[Line] = field(default_factory=list)
    override: str | None = None   # forced text (hanging-marker merge)

    @property
    def text(self) -> str:
        return self.override if self.override is not None else _join_lines(self.lines)

    @property
    def x0(self) -> float:
        return self.lines[0].x0


def _join_lines(lines: list[Line]) -> str:
    """Join a paragraph's lines, repairing conservative end-of-line hyphenation."""
    out = ""
    for i, ln in enumerate(lines):
        t = ln.text
        if i == 0:
            out = t
            continue
        if out.endswith("-") and len(out) >= 2 and out[-2].isalpha() and t[:1].islower():
            # soft wrap hyphen: "avoid-" + "ing" -> "avoiding" (next starts lower)
            out = out[:-1] + t
        else:
            out = out.rstrip() + " " + t.lstrip()
    return out


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


_BARE_MARKER_RE = re.compile(r"^\(?\s*(\d{1,3})\s*[.)]?\s*$|^\(\s*[A-Za-z]{1,4}\s*\)\.?$")
# a lone resolution-number heading with NO title on the same line (born-digital
# docs split '48/23.' from its title into separate blocks)
_LONE_HEADING_RE = re.compile(
    r"^([A-Z]?-?\d{1,4}/\d{1,4}[A-Za-z]*|\(?\d{1,4}\s*\([A-Za-z0-9]{1,8}\))\s*\.\s*$")


def reconstruct_paragraphs(col_lines: list[list[Line]],
                           marker_repair_log: list[tuple[str, str]] | None = None
                           ) -> tuple[list[Para], float, float]:
    """Group ordered lines into paragraphs. Returns (paras, col_left, col_right).

    A new paragraph begins on: a structural-start line; a first-line indent past
    the line's OWN column left edge; a large vertical gap; a page change; or a
    font-size jump (heading). A paragraph may flow across a column boundary
    (newspaper order) via the natural continuation rule. Finally, a bare hanging
    marker line ('1.', '(a)') is merged into the following clause, so born-digital
    UN docs that place the number in its own block still yield '1. Requests ...'."""
    all_lines = [l for col in col_lines for l in col]
    if not all_lines:
        return [], 0.0, 0.0
    col_left = _percentile([l.x0 for l in all_lines], 0.15)
    col_right = _percentile([l.x1 for l in all_lines], 0.85)
    gaps: list[float] = []
    for col in col_lines:
        for a, b in zip(col, col[1:]):
            if b.page == a.page and b.y0 >= a.y0:
                gaps.append(b.y0 - a.y1)
    med_gap = _median([g for g in gaps if g > 0]) or 3.0
    body_size = _median([l.size for l in all_lines]) or 10.0

    paras: list[Para] = []
    prev: Line | None = None
    for col in col_lines:
        for ln in col:
            start = False
            if prev is None:
                start = True
            elif _structural_start(ln.text):
                start = True
            elif ln.page != prev.page:
                start = True
            elif ln.y0 - prev.y1 > 1.6 * med_gap and ln.page == prev.page:
                start = True
            elif ln.x0 > ln.cleft + 7 and _terminal(prev.text):
                start = True  # first-line indent after a completed sentence
            elif ln.size >= body_size + 1.5 and prev.size < body_size + 1.5:
                start = True  # font jump into a heading
            if start:
                paras.append(Para([ln]))
            else:
                paras[-1].lines.append(ln)
            prev = ln

    if marker_repair_log is not None:
        paras = _repair_ocr_markers(paras, marker_repair_log)
    return _merge_hanging_markers(paras), col_left, col_right


def _merge_hanging_markers(paras: list[Para]) -> list[Para]:
    """Merge a paragraph that is just a hanging marker ('1.', '(a)') into the
    next paragraph, reconstructing 'N. <verb> ...' for the parser's lexical path."""
    out: list[Para] = []
    i = 0
    while i < len(paras):
        p = paras[i]
        t = p.text.strip()
        if _BARE_MARKER_RE.match(t) and i + 1 < len(paras):
            nxt = paras[i + 1]
            # normalise "1" / "1)" -> "1."; keep "(a)" as-is
            marker = t if t.endswith((".", ")")) else t + "."
            out.append(Para(p.lines + nxt.lines,
                            override=f"{marker} {nxt.text.lstrip()}"))
            i += 2
            continue
        # lone number-heading ('48/23.') + its title line on the next block
        if (_LONE_HEADING_RE.match(t) and i + 1 < len(paras)
                and not _structural_start(paras[i + 1].text)):
            nxt = paras[i + 1]
            out.append(Para(p.lines + nxt.lines,
                            override=f"{t} {nxt.text.lstrip()}"))
            i += 2
            continue
        out.append(p)
        i += 1
    return out


# ---------------------------------------------------------------------------
# Target cropping
# ---------------------------------------------------------------------------

@dataclass
class CropResult:
    start: int
    end: int          # exclusive
    anchor_found: bool
    flags: list[str]


def _target_matchers(symbol_normalized: str):
    """Build (target_regex, target_number) from a symbol. target_number lets the
    neighbour-boundary detector recognise a DIFFERENT resolution heading."""
    m = re.match(r"^[AES]/RES/(.+)$", symbol_normalized)
    if not m:
        return None, None
    rest = m.group(1)
    mo = re.match(r"^(\d{1,4})\((\d{4})\)$", rest)          # S/RES/338(1973)
    if mo:
        num = mo.group(1)
        return re.compile(rf"^\(?\s*(Resolution\s+)?{num}\s*\(\s*{mo.group(2)}"), num
    mo = re.match(r"^(\d{1,4})\(([A-Za-z0-9\-]+)\)$", rest)  # A/RES/1260(XIII)
    if mo:
        num = mo.group(1)
        return re.compile(rf"^\(?\s*{num}\s*\("), num
    mo = re.match(r"^([A-Z]?-?\d{1,4})/(\d{1,4}[A-Za-z]*)$", rest)  # 48/23, 1978/6, S-15/1
    if mo:
        sess, num = mo.group(1), mo.group(2)
        return re.compile(rf"^\s*{re.escape(sess)}/{re.escape(num)}\s*\."), f"{sess}/{num}"
    return None, None


def _heading_number(text: str) -> str | None:
    m = HEADING_SC_RE.match(text)     # SC "Resolution 650 (1990)"
    if m:
        return m.group(1)
    m = HEADING_ROMAN_RE.match(text)
    if m:
        return m.group(1)
    m = HEADING_SLASH_RE.match(text)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def crop_to_target(paras: list[Para], symbol_normalized: str) -> CropResult:
    """Locate the target resolution inside a compilation excerpt.

    Start at the target's own number heading; end at its adoption record (a
    'Nth (plenary) meeting' / 'Adopted ...' line, plus a trailing date line) or at
    the next resolution heading. If the anchor cannot be found, keep everything and
    flag it — NEVER silently truncate."""
    flags: list[str] = []
    target_re, target_num = _target_matchers(symbol_normalized)
    texts = [p.text for p in paras]
    n = len(paras)

    start = 0
    anchor = False
    if target_re is not None:
        matches = [i for i, t in enumerate(texts) if target_re.match(t)]
        # Old SC/GA compilation pages are bilingual (French + English). Prefer a
        # heading occurrence followed by an ENGLISH opening formula within a short
        # window over one trailed by 'Le Conseil'/'L'Assemblée' (French).
        english = [i for i in matches
                   if any(OPENING_RE.match(texts[j]) for j in range(i, min(i + 6, n)))]
        if english:
            start, anchor = english[0], True
        elif matches:
            start, anchor = matches[0], True
    if not anchor:
        # Fallback: a single opening formula and no confident neighbour → keep all.
        openings = [i for i, t in enumerate(texts) if OPENING_RE.match(t)]
        if len(openings) <= 1:
            flags.append("crop_anchor_not_found_single_text")
            return CropResult(0, n, False, flags)
        # Multiple openings but no target heading match: start at the first opening's
        # preceding heading if any, else keep all and flag (ambiguous).
        flags.append("crop_anchor_not_found_multi_text")
        return CropResult(0, n, False, flags)

    # Find crop end after the start.
    end = n
    seen_opening = False
    for j in range(start + 1, n):
        t = texts[j]
        if OPENING_RE.match(t):
            seen_opening = True
            continue
        hn = _heading_number(t)
        if hn is not None and hn != target_num:
            end = j  # next (different) resolution heading
            break
        if seen_opening and (DECISION_HEAD_RE.match(t) or DECISION_BLOCK_RE.match(t)):
            end = j  # an SC 'Decision(s)' block (bare or narrative) after the body
            break
        if seen_opening and (MEETING_RE.match(t) or ADOPTED_RE.match(t)):
            # include the adoption record, plus a trailing date line if present
            end = j + 1
            if end < n and DATE_RE.match(texts[end]):
                end += 1
            break
    if end == n and start == 0:
        pass
    if not seen_opening and end == n:
        flags.append("crop_no_opening_after_anchor")
    return CropResult(start, end, True, flags)


# ---------------------------------------------------------------------------
# Raw-row emission (matches fulltext_extract_raw's contract)
# ---------------------------------------------------------------------------

def _line_props(p: Para, tri: Triage) -> dict:
    lines = p.lines
    text = p.text
    cleft = lines[0].cleft
    cright = lines[0].cright
    size = _median([l.size for l in lines])
    bold = all(l.bold for l in lines) and bool(lines)
    italic = all(l.italic for l in lines) and bool(lines)
    props: dict = {"pdf": True, "textlayer_score": tri.klass, "size": round(size, 1)}
    if bold:
        props["bold"] = True
    if italic:
        props["italic"] = True
    if lines[0].lead_italic_text and not italic:
        props["lead_italic_text"] = lines[0].lead_italic_text
    # first-line indent relative to the line's own column left edge (presence is
    # the signal the parser reads; the exact value is informational).
    indent = lines[0].x0 - cleft
    if indent > 7:
        props["indent_firstline"] = int(round(indent))
    # all-caps
    alpha = [c for c in text if c.isalpha()]
    if len(alpha) >= 2 and all(c.isupper() for c in alpha):
        props["all_caps"] = True
    # centered short line (title/heading signal)
    if len(text) <= 70 and not p.override:
        left_gap = lines[0].x0 - cleft
        right_gap = cright - lines[0].x1
        span = max(cright - cleft, 1.0)
        if left_gap > 0.12 * span and abs(left_gap - right_gap) < 0.20 * span:
            props["alignment"] = "center"
    return props


def _new_row(position: int, kind: str, text: str, props: dict | None) -> dict:
    return {
        "position": position, "kind": kind, "text": text,
        "style_id": None, "style_name": None, "numbering": None,
        "props": props, "table_cell": None, "hyperlinks": None,
        "footnote_ref": None,
    }


def build_rows(paras: list[Para], crop: CropResult, tri: Triage) -> list[dict]:
    rows: list[dict] = []
    pos = 0
    prev_line: Line | None = None
    for p in paras[crop.start:crop.end]:
        # emit an 'empty' structural marker on a large vertical gap / page break
        if prev_line is not None:
            first = p.lines[0]
            if first.page != prev_line.page or first.y0 - prev_line.y1 > 14:
                rows.append(_new_row(pos, "empty", "", None))
                pos += 1
        props = _line_props(p, tri)
        rows.append(_new_row(pos, "paragraph", p.text, props))
        pos += 1
        prev_line = p.lines[-1]
    return rows


# ---------------------------------------------------------------------------
# Whole-document extraction
# ---------------------------------------------------------------------------

@dataclass
class ExtractResult:
    triage: Triage
    rows: list[dict]
    dropped_headers: list[str]
    crop: CropResult | None
    n_columns: int
    marker_repairs: list[tuple[str, str]] = field(default_factory=list)
    leadverb_repairs: list[tuple[str, str]] = field(default_factory=list)
    french_dropped: int = 0


def extract_pdf(path: Path, symbol_normalized: str) -> ExtractResult:
    doc = fitz.open(path)
    pages_text = [doc[i].get_text("text") for i in range(doc.page_count)]
    tri = triage_text(pages_text)
    if tri.klass == "none":
        doc.close()
        return ExtractResult(tri, [], [], None, 1)

    page_heights = {i: doc[i].rect.height for i in range(doc.page_count)}
    page_widths = {i: doc[i].rect.width for i in range(doc.page_count)}
    all_lines: list[Line] = []
    for i in range(doc.page_count):
        all_lines.extend(extract_lines(doc[i], i))
    doc.close()

    kept, dropped = drop_headers_footers(all_lines, page_heights)

    # Separate small-font footnote lines (bottom-of-column apparatus in the old
    # two-column supplements: body ~9pt, footnotes ~5pt) from the body flow, so
    # they do not glue onto body text across a column boundary. They are appended
    # after the body as kind='footnote' rows, mirroring the docx extractor.
    body_size = _median([l.size for l in kept]) or 10.0
    body_lines = [l for l in kept if l.size >= body_size - 1.5]
    foot_lines = [l for l in kept if l.size < body_size - 1.5]

    # Drop facing-language (French) lines from the old bilingual supplement
    # volumes: kept only when they carry >=3 French function words, so a real
    # two-column English/French page yields a contiguous English body (the French
    # column no longer interleaves in reading order and truncates the crop).
    n_before = len(body_lines) + len(foot_lines)
    body_lines = [l for l in body_lines if not french_line(l.text)]
    foot_lines = [l for l in foot_lines if not french_line(l.text)]
    french_dropped = n_before - len(body_lines) - len(foot_lines)

    # per-page column split, then concatenate pages in order
    col_groups: list[list[Line]] = []
    n_columns = 1
    for i in range(len(page_heights)):
        pls = [l for l in body_lines if l.page == i]
        cols = split_columns(pls, page_widths.get(i, 622.0))
        n_columns = max(n_columns, len(cols))
        col_groups.extend(cols)

    marker_repairs: list[tuple[str, str]] = []
    paras, _col_left, _col_right = reconstruct_paragraphs(col_groups, marker_repairs)
    # Repair OCR-garbled opening formulas so the parser's state machine anchors,
    # and OCR-garbled first-word lead verbs (first word only, verbatim body).
    leadverb_repairs: list[tuple[str, str]] = []
    for p in paras:
        fixed = repair_opening(p.text)
        if fixed != p.text:
            p.override = fixed
            continue
        before_text = p.text
        lv = repair_lead_verb(before_text)
        if lv is not None and lv != before_text:
            p.override = lv
            leadverb_repairs.append((before_text.split(None, 1)[0],
                                     lv.split(None, 1)[0]))
    crop = crop_to_target(paras, symbol_normalized)
    rows = build_rows(paras, crop, tri)
    rows = _append_footnote_rows(rows, foot_lines, page_widths, tri)
    return ExtractResult(tri, rows, dropped, crop, n_columns,
                         marker_repairs=marker_repairs,
                         leadverb_repairs=leadverb_repairs,
                         french_dropped=french_dropped)


def _append_footnote_rows(rows: list[dict], foot_lines: list[Line],
                          page_widths: dict[int, float], tri: Triage) -> list[dict]:
    """Reconstruct footnote paragraphs from the small-font lines and append them
    as kind='footnote' rows after the cropped body (document order preserved)."""
    if not foot_lines:
        return rows
    groups: list[list[Line]] = []
    for i in sorted(page_widths):
        pls = [l for l in foot_lines if l.page == i]
        groups.extend(split_columns(pls, page_widths.get(i, 622.0)))
    fparas, _, _ = reconstruct_paragraphs(groups)
    pos = rows[-1]["position"] + 1 if rows else 0
    for p in fparas:
        if not p.text.strip():
            continue
        rows.append(_new_row(pos, "footnote", p.text,
                             {"pdf": True, "textlayer_score": tri.klass,
                              "size": round(_median([l.size for l in p.lines]), 1)}))
        pos += 1
    return rows


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
            [symbol, lang])
        params = [
            (symbol, lang, r["position"], r["kind"], r["text"], None, None,
             None, Jsonb(r["props"]) if r["props"] is not None else None,
             None, None, None, EXTRACTOR_VERSION)
            for r in rows
        ]
        cur.executemany(_INSERT, params)


# ---------------------------------------------------------------------------
# Targets + main loop
# ---------------------------------------------------------------------------

def fetch_targets(symbols: list[str] | None, force: bool, limit: int | None):
    statuses = ["fetched", "extracted", "no_text_layer"] if force else ["fetched"]
    sql = (
        "SELECT symbol_normalized, lang, archive_path "
        "FROM digitallibrary.document_files "
        "WHERE format = 'pdf' AND status = ANY(%s) AND archive_path IS NOT NULL")
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
    ap = argparse.ArgumentParser(description="Raw paragraph extractor — PDF path (pre-1994)")
    ap.add_argument("--limit", type=int, help="extract at most N documents")
    ap.add_argument("--symbols", help="comma-separated symbol_normalized list")
    ap.add_argument("--force", action="store_true",
                    help="also re-extract 'extracted'/'no_text_layer' rows")
    ap.add_argument("--debug", action="store_true",
                    help="print triage + crop + rows for each doc; do NOT write to the DB")
    ap.add_argument("--self-test", action="store_true",
                    help="run the repair-conservatism unit self-test and exit")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    targets = fetch_targets(symbols, args.force or args.debug, args.limit)
    print(f"PDF extraction targets: {len(targets)} documents")

    ok = failed = no_text = total_rows = 0
    by_class = {"text": 0, "poor": 0, "none": 0}
    flagged: list[tuple[str, list[str]]] = []
    all_marker_repairs: list[tuple[str, str, str]] = []
    all_leadverb_repairs: list[tuple[str, str, str]] = []
    all_french: list[tuple[str, int]] = []

    for start in range(0, len(targets), BATCH_DOCS):
        chunk = targets[start:start + BATCH_DOCS]
        conn = None if args.debug else get_conn()
        try:
            for symbol, lang, archive_path in chunk:
                path = ARCHIVE_ROOT / archive_path
                try:
                    if not path.exists():
                        raise FileNotFoundError(f"archive file missing: {archive_path}")
                    res = extract_pdf(path, symbol)
                    by_class[res.triage.klass] += 1
                    if args.debug:
                        _debug_dump(symbol, res)
                        continue
                    if res.triage.klass == "none":
                        upsert_document_file(
                            conn, symbol, lang, status="no_text_layer",
                            error=f"no usable text layer ({res.triage.summary()})")
                        conn.commit()
                        no_text += 1
                        continue
                    for b, a in res.marker_repairs:
                        all_marker_repairs.append((symbol, b, a))
                    for b, a in res.leadverb_repairs:
                        all_leadverb_repairs.append((symbol, b, a))
                    if res.french_dropped:
                        all_french.append((symbol, res.french_dropped))
                    write_document(conn, symbol, lang, res.rows)
                    err = None
                    if res.crop and res.crop.flags:
                        err = "; ".join(res.crop.flags)[:400]
                        flagged.append((symbol, res.crop.flags))
                    note = f"{res.triage.summary()}"
                    upsert_document_file(conn, symbol, lang, status="extracted",
                                         error=(f"{note} | {err}" if err else note)[:500])
                    conn.commit()
                    ok += 1
                    total_rows += len(res.rows)
                except Exception as exc:
                    if conn is not None:
                        conn.rollback()
                        upsert_document_file(
                            conn, symbol, lang, status="extract_failed",
                            error=f"{type(exc).__name__}: {exc}"[:500])
                        conn.commit()
                    failed += 1
                    print(f"  ! {symbol}: {type(exc).__name__}: {exc}")
        finally:
            if conn is not None:
                conn.close()
        if not args.debug:
            done = start + len(chunk)
            print(f"  extracted {done}/{len(targets)} ok={ok} no_text={no_text} "
                  f"failed={failed} rows={total_rows}")

    print(f"\nTriage: text={by_class['text']} poor={by_class['poor']} none={by_class['none']}")
    if flagged:
        print(f"Crop-flagged {len(flagged)} doc(s):")
        for sym, fl in flagged:
            print(f"  {sym}: {fl}")
    print(f"\nOCR marker repairs fired: {len(all_marker_repairs)} (audit)")
    for sym, b, a in all_marker_repairs:
        print(f"  {sym:<22} {b!r} -> {a!r}")
    print(f"Lead-verb repairs fired: {len(all_leadverb_repairs)} (audit)")
    for sym, b, a in all_leadverb_repairs:
        print(f"  {sym:<22} {b!r} -> {a!r}")
    print(f"French facing-language lines dropped in {len(all_french)} doc(s):")
    for sym, n in all_french:
        print(f"  {sym:<22} dropped {n} line(s)")
    print(f"Done. ok={ok} no_text={no_text} failed={failed} rows_written={total_rows}")
    return 0 if failed == 0 else 1


def _debug_dump(symbol: str, res: ExtractResult) -> None:
    print(f"\n===== {symbol}  [{res.triage.summary()}]  cols={res.n_columns} =====")
    if res.dropped_headers:
        print(f"  dropped headers/footers ({len(res.dropped_headers)}):")
        for d in res.dropped_headers[:10]:
            print(f"     - {d}")
    if res.crop:
        print(f"  crop: start={res.crop.start} end={res.crop.end} "
              f"anchor_found={res.crop.anchor_found} flags={res.crop.flags}")
    if res.french_dropped:
        print(f"  french lines dropped: {res.french_dropped}")
    for b, a in res.marker_repairs:
        print(f"  marker repair: {b!r} -> {a!r}")
    for b, a in res.leadverb_repairs:
        print(f"  lead-verb repair: {b!r} -> {a!r}")
    for r in res.rows:
        if r["kind"] == "empty":
            print("     ·")
            continue
        p = r["props"] or {}
        tag = "".join(c for c, k in (("B", "bold"), ("I", "italic"),
                     ("C", "alignment"), ("U", "all_caps")) if p.get(k))
        print(f"   [{r['position']:>3}] {tag:<4} {r['text'][:96]}")


def _mk_para(text: str, x0: float = 80.0) -> Para:
    ln = Line(text=text, x0=x0, x1=x0 + 200, y0=0, y1=10, size=9.5, bold=False,
              italic=False, lead_italic_text=None, page=0)
    return Para([ln])


def _self_test() -> int:
    """Quantify repair conservatism with adversarial cases. Exit 0 iff all pass."""
    fails: list[str] = []

    # Case 1 — a standalone 'I.' WITHOUT sequence confirmation must NOT be
    # rewritten (no following '2.' numeric marker at the same indent).
    paras = [_mk_para("The Security Council,"),
             _mk_para("I."),
             _mk_para("Decides to do the thing.")]
    log: list[tuple[str, str]] = []
    _repair_ocr_markers(paras, log)
    if log or paras[1].text != "I.":
        fails.append(f"case1: unconfirmed 'I.' was rewritten -> {paras[1].text!r} log={log}")

    # Case 1b — a standalone 'I.' WITH sequence confirmation (next markers 2., 3.)
    # MUST be rewritten to '1.'.
    paras = [_mk_para("The Security Council,"),
             _mk_para("I."),
             _mk_para("Demands withdrawal.", x0=100.0),
             _mk_para("2. Demands observance."),
             _mk_para("3. Calls upon all parties.")]
    log = []
    _repair_ocr_markers(paras, log)
    if paras[1].text != "1." or not log:
        fails.append(f"case1b: confirmed 'I.' was NOT repaired -> {paras[1].text!r}")

    # Case 2 — 'Recallinx' in BODY text position (not first word) must NOT be
    # touched by the lead-verb repair.
    body = "the Council Recallinx its earlier decisions on the matter"
    if repair_lead_verb(body) is not None:
        fails.append(f"case2: body-position 'Recallinx' was repaired -> {repair_lead_verb(body)!r}")
    # ... but as a FIRST word it should repair (edit distance 1).
    if repair_lead_verb("Recallinx its resolutions 425 (1978)") != "Recalling its resolutions 425 (1978)":
        fails.append("case2b: first-word 'Recallinx' was NOT repaired")

    # Case 3 — a genuine roman heading 'II.' between operatives must SURVIVE (it is
    # two characters; only single-glyph confusables are candidates, and its
    # neighbour is 'III.', not a confirming numeric marker).
    paras = [_mk_para("1. Decides the first thing."),
             _mk_para("II."),
             _mk_para("2. Decides the second thing."),
             _mk_para("III.")]
    log = []
    _repair_ocr_markers(paras, log)
    if paras[1].text != "II." or paras[3].text != "III.":
        fails.append(f"case3: roman heading mutated -> {paras[1].text!r},{paras[3].text!r}")

    for name, msg in ([("FAIL", m) for m in fails]):
        print(f"  {name}: {msg}")
    if fails:
        print(f"self-test: {len(fails)} FAILED")
        return 1
    print("self-test: all 4 adversarial cases passed "
          "(unconfirmed 'I.' preserved, confirmed 'I.'->'1.', body 'Recallinx' "
          "untouched, roman 'II.'/'III.' survived)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
