#!/usr/bin/env python3
"""Semantic parser for UN resolution / decision / PRST full texts (Track A, v1).

Reads the low-interpretation extraction layer (digitallibrary.document_paragraphs_raw)
and emits ONE semantic JSON per document to
    <ARCHIVE_ROOT>/parsed_dev/<sanitized_symbol>.json

It classifies each raw paragraph into semantic elements (frontmatter, title,
heading, preambular/operative paragraph, footnote, vote record, signature,
table, divider) and enforces an accounting invariant: every raw position is
consumed by exactly one element's `positions[]` or appears in the top-level
`dropped[]` list with a reason.

Three format families are handled (see fulltext_census.py / the corpus census):
  - native `docx`  : rich styles, italic preambular lead runs (props.lead_italic_text),
                     literal operative numbers ("1.\\t..."), real kind='footnote' rows,
                     masthead often in table cells.
  - legacy `doc`   : usually styled like native; masthead frequently in table cells.
  - `wpd` (WP)     : ONE 'Normal' style, no italics, no numbering. Hierarchy is carried
                     by literal tabs / indent_firstline and text patterns; preambular
                     clauses are plain "Verb ... ," lines; footnotes are inline "N/ text"
                     after a "____" divider; sentences are sometimes hard-broken across
                     consecutive paragraph rows (we merge them).

This is deliberately standalone (mirrors the other python/ scripts): DATABASE_URL
from .env, short-lived psycopg (v3) connections. Targets are documents whose
document_files.status is 'extracted' or 'parsed' (so a re-parse still finds docs
already loaded to the semantic DB).

DB MODE (--to-db). The frozen semantic layer lands in two tables added by
migration 003:
  - digitallibrary.document_paragraphs — one row per parsed element (the JSON
    `elements[]`), document order. `id` is uuid5(NAMESPACE_URL,
    '<symbol_normalized>:<lang>:<position>') where position is the 0-based element
    index, computed by this loader.
  - digitallibrary.document_parses     — one row per (symbol,lang): parser_version,
    format, element_count, and the JSON root `dropped[]`/`issues[]` verbatim, so
    the accounting invariant stays queryable in SQL.
Loading is DELETE-then-INSERT per (symbol,lang) in BOTH tables (idempotent /
re-parsable), batched over short-lived connections of ~20 docs each, mirroring
fulltext_extract_raw.py's discipline. On success the document_files status is
advanced 'extracted' -> 'parsed'; on a hard parse/insert failure it is set to
'parse_failed' with the error recorded (never crashes the batch). An accounting
failure does NOT fail the load — the doc is still written and the failure is
recorded in document_parses.issues (and document_paragraphs stays queryable).
JSON output is written alongside the DB rows unless --db-only is given.

Usage:
    uv run python python/fulltext_parse.py                 # JSON only (all extracted/parsed docs)
    uv run python python/fulltext_parse.py --limit 5
    uv run python python/fulltext_parse.py --symbol A/RES/48/70
    uv run python python/fulltext_parse.py --to-db         # JSON + semantic DB
    uv run python python/fulltext_parse.py --db-only        # semantic DB only, no JSON
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from pathlib import Path

import fulltext_verbs as fv
from fulltext_common import ARCHIVE_ROOT, get_conn, sanitize_symbol, upsert_document_file

# sem-v2 adds the action-verb annotation pass (migration 004): a nested `action`
# object on each operative/preambular element, flattened to the action_*/assignee_*
# columns by the loader. Element construction / accounting are unchanged.
PARSER_VERSION = "sem-v2"
OUT_DIR = ARCHIVE_ROOT / "parsed_dev"

# Opt-in source-defect rescue: when a resolution drops an operative NUMBER at
# source (e.g. A/HRC/RES/19/1 omits '5.'/'6.'; S/RES/1528(2004) omits '(e)'-'(h)'),
# an unlabeled clause that (a) sits inside a running operative sequence with a
# CONFIRMED numbering gap ahead and (b) reads like an operative (finite lead verb
# or a 'To <verb>' sub-item) is labeled operative with props.inferred_operative and
# NO invented prefix. Default ON; recorded per element so it is auditable/reversible.
RESCUE_INFERRED_OPERATIVE = True

# 'To <lowercase-verb>' opens an infinitive operative sub-item (…(a) To observe,
# (b) To liaise…); used by the rescue to recognise an unlabeled sibling sub-item.
INFINITIVE_SUBITEM_RE = re.compile(r"^To\s+[a-z]")

# ---------------------------------------------------------------------------
# Vocabularies & patterns
# ---------------------------------------------------------------------------

# Preambular lead verbs (first word, case-insensitive). Used for the WP fallback
# where there is no props.lead_italic_text to read the verb from. Deliberately
# broad; the state machine (opening-formula .. first-operative) is the primary
# signal, this only labels lead_verb and rescues stray clauses.
PREAMBULAR_FIRST_WORDS = {
    "recalling", "reaffirming", "noting", "recognizing", "recognising",
    "welcoming", "considering", "convinced", "concerned", "emphasizing",
    "emphasising", "expressing", "guided", "having", "bearing", "taking",
    "mindful", "alarmed", "acknowledging", "determined", "determining",
    "stressing", "underlining", "underscoring", "desiring", "desirous",
    "aware", "regretting", "deploring", "affirming", "observing", "believing",
    "conscious", "deeply", "gravely", "firmly", "fully", "further",
    "reiterating", "recalling", "seeking", "encouraged", "endorsing",
    "commending", "appreciating", "anxious", "cognizant", "cognisant",
    "remaining", "keeping", "realizing", "realising", "supporting",
    "welcoming", "invoking", "conscious", "noting", "having", "aware",
    "highlighting", "acknowledging", "reaffirming", "expressing", "guided",
    "concerned", "convinced", "confident", "hopeful", "resolved", "eager",
    "grateful", "pleased", "sharing", "aiming",
}

# Second words that extend a lead-verb phrase (e.g. "Recalling also",
# "Deeply concerned", "Taking note", "Bearing in mind", "Having considered").
PREAMBULAR_SECOND_WORDS = {
    "also", "further", "note", "in", "with", "that", "the", "concerned",
    "convinced", "aware", "mindful", "considered", "examined", "recalled",
    "regard", "account", "into", "of", "deeply", "again",
}

# Finite operative lead verbs (3rd-person present). Distinct from the participial
# preambular leads -- used to catch UNNUMBERED operative clauses in short
# resolutions ("Decides to hold ...") that carry no "1." prefix.
OPERATIVE_LEAD_VERBS = {
    "decides", "requests", "calls", "urges", "invites", "recommends",
    "reaffirms", "notes", "welcomes", "encourages", "endorses", "expresses",
    "takes", "considers", "demands", "stresses", "emphasizes", "emphasises",
    "reiterates", "approves", "adopts", "condemns", "deplores", "declares",
    "affirms", "acknowledges", "appeals", "authorizes", "authorises",
    "designates", "proclaims", "resolves", "supports", "commends", "confirms",
    "underlines", "underscores", "recognizes", "recognises", "determines",
    "establishes", "requires", "instructs", "directs",
    "implores", "pledges", "undertakes",
}

# Opening formula. Tolerates a leading quote (A/HRC/PRST statements quote the
# Council resolution verbatim: '"The Human Rights Council,') so those PRSTs are
# recognised as resolution-structured (preambular/operative labeling applies).
OPENING_RE = re.compile(
    r"^[\"“”‘’']?\s*The\s+(General Assembly|Security Council|Economic and Social Council|"
    r"Human Rights Council|Trusteeship Council)\s*,?\s*$"
)

# Title patterns.
TITLE_GA_NUM_RE = re.compile(r"^(\d+/\d+[A-Za-z]*)\.\s+(.+)$")          # "77/52. Subject"
TITLE_HRC_NUM_RE = re.compile(r"^(\d+/\d+[A-Za-z]*)\s+([A-Z].+)$")       # "15/7 Subject"
TITLE_SC_RE = re.compile(r"^Resolution\s+\d+\s*\(\d{4}\)\s*$", re.I)     # "Resolution 1881 (2009)"
TITLE_PRST_RE = re.compile(r"^Statement by the President of the ", re.I)
TITLE_DEC_RE = re.compile(r"^Decision\s+\d+", re.I)

# Operative / subparagraph prefixes (on cleaned, tab-collapsed text).
OP_NUM_RE = re.compile(r"^(\d+)\.\s+(\S.*)$")            # "1. Requests ..."
# Tolerant operative number for source/OCR defects where the period after the
# number is missing or misplaced ("4\tCalls", "232\t.Recognizes", "13\tUrges").
# Only applied mid-operative and only when the clause opens with a known
# operative lead verb, so plain numbers / years never match (see step 11).
OP_NUM_LOOSE_RE = re.compile(r"^(\d{1,3})[.\s]*(\S.*)$")
# Letter cap is 7 to admit long Roman subparagraph markers ("(xxviii)"=6,
# "(xxxviii)"=7). classify_paren() still disambiguates alpha vs Roman, so the
# wider cap only rescues genuine long numerals that {1,5} silently dropped.
OP_PAREN_RE = re.compile(r"^\(([A-Za-z]{1,7}|\d{1,3})\)\s+(\S.*)$")  # "(a) ...", "(i) ...", "(1) ..."

# Frontmatter / structural line patterns.
DIVIDER_RE = re.compile(r"^_{3,}$")
WP_FOOTNOTE_RE = re.compile(r"^(\d{1,3})/\s+(\S.*)$")    # "1/ United Nations, Treaty Series ..."
# Annex/appendix heading. Matches the bare form ("Annex", "Annex II", "Annex A")
# AND a titled form where a label is followed by a running title on the SAME line
# ("Annex A - Items subject to a no-objection process", "ANNEX 1 - <plan>"). A
# title is only accepted AFTER a label (letter / Roman / number) so body prose
# beginning with the word "Annex"/"Appendix" (e.g. "Annex to the present
# resolution ...") is NOT swallowed; the (?=\s|$) also rejects "Annexation ...".
ANNEX_RE = re.compile(
    r"^(Annex|Appendix)(?=\s|$)"
    r"(?:\s+(?:[IVXLCDM]+|[A-Z]|\d{1,3})"
    r"(?:\s*[-–—:.]\s*\S.*|\s+\S.*)?)?\s*$", re.I)
VOTE_RE = re.compile(r"^\[?\s*Adopted\b.*(vote|without a vote)", re.I)
VOTE_TALLY_RE = re.compile(r"^(In favour|Against|Abstaining|Non-Voting|Absent)\s*:\s*(.*)$", re.I)
VOTE_SUMMARY_RE = re.compile(
    r"recorded vote of\s+(\d+)\s+to\s+(\d+)(?:,\s*with\s+(\d+)\s+abstention)?", re.I)
VOTE_KEY = {
    "in favour": "in_favour", "against": "against", "abstaining": "abstaining",
    "non-voting": "non_voting", "absent": "absent",
}
MEETING_RE = re.compile(r"^\d+\s*(st|nd|rd|th)\s+(plenary\s+)?meeting\b", re.I)
DATE_LINE_RE = re.compile(
    r"^\d{1,2}\s+(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{4}\s*$"
)
SESSION_RE = re.compile(r"session\s*$", re.I)
AGENDA_RE = re.compile(r"^Agenda item\b", re.I)
RUNNING_HEADER_RE = re.compile(r"^[A-Z]+(/[A-Z0-9()./-]+)+$")   # bare doc symbol like "A/RES/48/70"
PAGE_NUM_RE = re.compile(r"^-?\s*\d{1,4}\s*-?$")
# Masthead lines (document letterhead): organisation name, distribution class,
# language, running symbol/date. Only tagged in the front region -- these are
# accounted as frontmatter with subtype='masthead' for a cleaner elements stream.
MASTHEAD_RE = re.compile(
    r"^(United Nations|Nations Unies|UNITED|NATIONS|Distr\.?|GENERAL|LIMITED|"
    r"Original\s*:|ORIGINAL\s*:|Security Council|General Assembly|"
    r"Economic and Social Council|Human Rights Council|Trusteeship Council)\b", re.I)
ADOPTED_BY_RE = re.compile(r"^Adopted by the (Security Council|General Assembly)", re.I)
RES_ADOPTED_RE = re.compile(r"^Resolution adopted by ", re.I)
REPORT_NOTE_RE = re.compile(r"^\[on the (report|recommendation) ", re.I)

# Heading styles seen in the corpus (native docx / doc). TitleH1 / TitleHCH are
# handled as titles; the rest mark section headings inside the body.
BODY_HEADING_STYLES = {"H1", "H2", "H3", "H4", "H23", "H1G", "H2G", "H3G", "H4G",
                       "HCh", "HChG", "HChM"}

ROMAN_CHARS = set("ivxlcdm")

# Annex that is really an annexed governance INSTRUMENT (terms of reference, rules
# of procedure, statute, charter, ...) with a numbered-article structure gets
# SCOPED operative labeling (its numbered paragraphs are the instrument's
# operatives, tracked independently of the parent resolution). Plain annexes
# (programmes of action, agendas, declarations, lists, schedules, tables) keep
# paragraph_type=None -- their "operativeness" is not resolution-mandate operative
# and is deferred (see run report). "Amendments to ..." annexes are diffs, not the
# instrument, so they are excluded.
ANNEX_INSTRUMENT_RE = re.compile(
    r"\b(terms of reference|rules of procedure|statute|constitution|"
    r"charter of|regulations of|mandate of the)\b", re.I)
ANNEX_AMENDMENT_RE = re.compile(r"^\s*Amendments?\b", re.I)


def _clean(text: str) -> str:
    """Normalize a raw paragraph text for classification & output.

    Collapses tabs to single spaces, turns NBSP into regular spaces, squeezes
    runs of whitespace, and strips. The raw leading-tab indentation is dropped
    (it is captured structurally by props/level), but internal structure is
    preserved as spaces.
    """
    if not text:
        return ""
    t = text.replace("\xa0", " ").replace("\t", " ").replace("\n", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _is_terminal(text: str) -> bool:
    """True if `text` ends with sentence/clause-final punctuation."""
    return bool(text) and text.rstrip()[-1:] in ".,;:?!”’)"


# ---------------------------------------------------------------------------
# Logical-row model (raw rows, after WP hard-break merging)
# ---------------------------------------------------------------------------


class LRow:
    """One logical row: a raw row, or several WP rows merged into one clause."""

    __slots__ = ("positions", "kind", "text", "clean", "style", "num", "props",
                 "table_cell", "hyperlinks", "note_ids", "_fr")

    def __init__(self, raw: dict):
        self.positions = [raw["position"]]
        self.kind = raw["kind"]
        self.text = raw["text"] or ""
        self.clean = _clean(self.text)
        self.style = raw["style_id"] or ""
        self.num = raw["numbering"]
        self.props = raw["props"] or {}
        self.table_cell = raw["table_cell"]
        self.hyperlinks = list(raw["hyperlinks"] or [])
        fr = raw["footnote_ref"]
        self.note_ids: list[int] = []
        if isinstance(fr, dict) and "note_ids" in fr:
            self.note_ids = list(fr["note_ids"])
        self._fr = fr

    def merge(self, other: "LRow") -> None:
        self.positions.extend(other.positions)
        joiner = "" if self.text.endswith("-") else " "
        self.text = (self.text.rstrip() + joiner + other.text.lstrip()).strip()
        self.clean = (self.clean + " " + other.clean).strip()
        self.hyperlinks.extend(other.hyperlinks)
        self.note_ids.extend(other.note_ids)


def _structural_start(lr: LRow) -> bool:
    """True if a WP paragraph clearly begins a new structural unit (never merge into prev)."""
    c = lr.clean
    if not c:
        return True
    if OPENING_RE.match(c) or OP_NUM_RE.match(c) or OP_PAREN_RE.match(c):
        return True
    if WP_FOOTNOTE_RE.match(c) or DIVIDER_RE.match(c) or ANNEX_RE.match(c):
        return True
    if VOTE_RE.match(c) or VOTE_TALLY_RE.match(c) or MEETING_RE.match(c):
        return True
    if TITLE_GA_NUM_RE.match(c) or TITLE_SC_RE.match(c) or TITLE_PRST_RE.match(c):
        return True
    if lr.props.get("all_caps") or lr.props.get("alignment") == "center":
        return True
    first = c.split(" ", 1)[0].lower().rstrip(",")
    if first in PREAMBULAR_FIRST_WORDS:
        return True
    return False


def build_logical_rows(raw_rows: list[dict], fmt: str) -> list[LRow]:
    """Merge WP hard-broken continuation lines into single logical rows.

    Conservative: only for wpd, only merges a *body* paragraph into the previous
    body paragraph when the previous line did not end with terminal punctuation
    and the current line is not itself a structural start and looks like a wrap
    (starts lowercase, or lost the firstline indent that the previous line had).
    """
    lrows = [LRow(r) for r in raw_rows]
    if fmt != "wpd":
        return lrows

    merged: list[LRow] = []
    for lr in lrows:
        if (
            merged
            and lr.kind == "paragraph"
            and lr.clean
            and merged[-1].kind == "paragraph"
            and merged[-1].clean
            and not _is_terminal(merged[-1].clean)
            and not _structural_start(lr)
        ):
            prev = merged[-1]
            starts_lower = lr.clean[:1].islower()
            prev_had_indent = "indent_firstline" in prev.props
            cur_no_indent = "indent_firstline" not in lr.props
            if starts_lower or (prev_had_indent and cur_no_indent):
                prev.merge(lr)
                continue
        merged.append(lr)
    return merged


SUBRES_LETTER_RE = re.compile(r"^([A-Z])\.?$")   # bare "A" / "B." sub-resolution heading


def _annex_subtype(lrows: list[LRow], i: int, heading: str) -> str | None:
    """Classify the annex whose heading is lrows[i]: 'amendment', 'instrument', or None.

    Scans from the heading to the next annex/appendix boundary (or end) to read the
    annex's title line and count its numbered paragraphs. Priority:
      * 'amendment' -- the heading OR its title line opens with 'Amendment(s)'
        (e.g. 'Amendments to the terms of reference ...'); the body is a diff
        ('Amend paragraph N to read: ...'), NOT the instrument itself, so it is
        NEVER scoped -- pure labeling, paragraph_type stays null.
      * 'instrument' -- the annex carries its OWN opening formula (an annexed
        resolution/agreement with a preamble), OR its title matches an instrument
        keyword (terms of reference / rules of procedure / statute / ...) and it
        has >=2 numbered paragraphs. Only 'instrument' annexes are scoped (their
        numbered paragraphs are labeled operative, tracked independently).
      * None -- plain annex (programme of action, declaration, agenda, list, ...).
    """
    n = len(lrows)
    title_line = None
    n_numbered = 0
    has_opening = False
    j = i + 1
    while j < n:
        cj = lrows[j].clean
        if lrows[j].kind in ("empty", "section_break") or not cj:
            j += 1
            continue
        if ANNEX_RE.match(cj):           # next annex/appendix -> end of this scope
            break
        if OPENING_RE.match(cj):
            has_opening = True
        if title_line is None:
            title_line = cj
        if OP_NUM_RE.match(cj):
            n_numbered += 1
        j += 1
    if ANNEX_AMENDMENT_RE.match(heading) or (title_line and ANNEX_AMENDMENT_RE.match(title_line)):
        return "amendment"
    if has_opening:
        return "instrument"
    if title_line and ANNEX_INSTRUMENT_RE.search(title_line) and n_numbered >= 2:
        return "instrument"
    return None


def detect_subres_blocks(lrows: list[LRow]) -> dict[int, dict]:
    """Locate consolidated/omnibus sub-resolution boundaries (multi-text).

    A consolidated resolution (e.g. A/RES/48/75 with sub-resolutions A..L) prints,
    for each sub-resolution, a bare capital-letter heading ('A'), then optional
    title line(s), then its OWN opening formula ('The General Assembly,'). We
    segment ONLY when the letter heading is CONFIRMED by an opening formula that
    follows within a short window -- so section headings inside a single resolution
    ('I', 'A. Utilization ...' followed by '1. ...', never an opening) are never
    mistaken for a new text.

    Returns {lrow_index_of_letter: {'letter': str, 'title_idx': [lrow_index, ...]}}.
    The title lines are the non-empty rows between the letter and the opening; they
    are merged into ONE title element per block by the caller and skipped in the
    normal stream (their positions are consumed by that title element).
    """
    n = len(lrows)
    blocks: dict[int, dict] = {}
    for i, lr in enumerate(lrows):
        m = SUBRES_LETTER_RE.match(lr.clean)
        if not m:
            continue
        title_idx: list[int] = []
        seen_open = False
        j = i + 1
        steps = 0
        while j < n and steps < 6:
            cj = lrows[j].clean
            if lrows[j].kind in ("empty", "section_break") or not cj:
                j += 1
                continue
            if OPENING_RE.match(cj):
                seen_open = True
                break
            # anything that is clearly not a sub-title stops the look-ahead
            if (OP_NUM_RE.match(cj) or OP_PAREN_RE.match(cj) or ANNEX_RE.match(cj)
                    or MEETING_RE.match(cj) or VOTE_RE.match(cj) or DIVIDER_RE.match(cj)
                    or WP_FOOTNOTE_RE.match(cj) or SUBRES_LETTER_RE.match(cj)
                    or TITLE_GA_NUM_RE.match(cj)):
                break
            title_idx.append(j)
            j += 1
            steps += 1
        if seen_open:
            blocks[i] = {"letter": m.group(1), "title_idx": title_idx}
    return blocks


# ---------------------------------------------------------------------------
# Sub-paragraph level / (i) disambiguation
# ---------------------------------------------------------------------------


class OpLevelTracker:
    """Assigns level/prefix to operative markers, disambiguating (i) alpha vs roman."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.last_alpha: str | None = None   # last single-letter alpha subpara
        self.roman_active = False            # a roman sub-sub sequence is running

    def top(self) -> None:
        # New top-level operative resets sub-sequences.
        self.last_alpha = None
        self.roman_active = False

    def classify_paren(self, token: str, next_tok: str | None = None) -> tuple[int, str]:
        """Return (level, prefix) for a parenthetical marker token like 'a','i','iv','1'.

        `next_tok` is the following paren marker (if any); it disambiguates a single
        '(i)' -- Roman nesting when '(ii)' follows, alpha continuation otherwise.
        """
        tok = token.lower()
        prefix = f"({token})"
        if tok.isdigit():
            return 2, prefix  # numeric subpara -- treat as level 2 variant
        # DOUBLED same-letter marker ('aa','bb',..,'zz'): the UN alpha convention
        # for subparagraphs continuing past (z). These are ALWAYS level-2 alpha --
        # NOT Roman -- even when the letter happens to be a Roman glyph ('cc','dd',
        # 'ii','ll','mm' etc.). The only exception is a genuine Roman sub-sub run
        # already open (e.g. '(i)(ii)') where '(ii)' is Roman: gated on roman_active.
        if len(tok) == 2 and tok[0] == tok[1]:
            if self.roman_active and all(ch in ROMAN_CHARS for ch in tok):
                return 3, prefix
            self.last_alpha = tok
            self.roman_active = False
            return 2, prefix
        # multi-char: roman if all roman chars, else treat as deeper alpha
        if len(tok) > 1:
            if all(ch in ROMAN_CHARS for ch in tok):
                self.roman_active = True
                return 3, prefix
            return 2, prefix
        # single char
        if tok == "i":
            # '(i)' is ambiguous: the start of a Roman sub-sub run (i)(ii)(iii)…
            # nested under an alpha item, OR alpha continuation of a flat (a)(b)…(i)
            # list (incl. …(d),(i) when source dropped (e)-(h)). Disambiguate by the
            # NEXT marker: '(ii)' => Roman nesting; otherwise, if an alpha run is
            # open, alpha continuation.
            if next_tok is not None and next_tok.lower() == "ii":
                self.roman_active = True
                return 3, prefix
            if self.last_alpha is not None and not self.roman_active:
                self.last_alpha = "i"
                return 2, prefix
            self.roman_active = True         # start of (i)(ii)... roman run
            return 3, prefix
        if tok in ("v", "x") and self.roman_active:
            return 3, prefix
        if tok in ROMAN_CHARS and self.roman_active and self.last_alpha is None:
            return 3, prefix
        # default: alpha subparagraph
        self.last_alpha = tok
        self.roman_active = False
        return 2, prefix


# ---------------------------------------------------------------------------
# Element construction
# ---------------------------------------------------------------------------


def _new_element(lr: LRow, **kw) -> dict:
    el = {
        "positions": list(lr.positions),
        "type": kw.get("type", "paragraph"),
        "section": kw.get("section", "main"),
        "paragraph_type": kw.get("paragraph_type"),
        "level": kw.get("level"),
        "prefix": kw.get("prefix"),
        "heading_level": kw.get("heading_level"),
        "text": kw.get("text", lr.clean),
        "lead_verb": kw.get("lead_verb"),
        "hyperlinks": lr.hyperlinks or [],
        "note_ids": sorted(set(lr.note_ids)) if lr.note_ids else [],
    }
    if kw.get("subtype"):
        el["subtype"] = kw["subtype"]
    if kw.get("annex_index"):
        el["annex_index"] = kw["annex_index"]
    if kw.get("text_index", 1) != 1:
        el["text_index"] = kw["text_index"]
    return el


def _lead_verb_from_text(clean: str) -> str | None:
    """Lexical lead-verb extraction for WP (no italics). Returns the verb phrase.

    Keeps the phrase tight: the leading participle plus a meaningful modifier
    only (e.g. "Recalling also", "Deeply concerned", "Bearing in mind", "Taking
    note"), not trailing objects -- "Recalling that ..." yields just "Recalling".
    """
    words = [w.strip(",") for w in clean.split()]
    if not words:
        return None
    w0 = words[0].lower()
    if w0 not in PREAMBULAR_FIRST_WORDS:
        return None
    if w0 in ("deeply", "gravely", "fully", "firmly", "strongly", "keenly") and len(words) > 1:
        return f"{words[0]} {words[1]}"          # "Deeply concerned"
    if w0 == "bearing" and len(words) > 2:
        return "Bearing in mind"
    if w0 == "taking" and len(words) > 1:
        return f"Taking {words[1]}"              # "Taking note"
    if w0 == "having" and len(words) > 1:
        return f"Having {words[1]}"              # "Having considered"
    if w0 == "guided":
        return "Guided by"
    if len(words) > 1 and words[1].lower() in ("also", "further", "again"):
        return f"{words[0]} {words[1]}"          # "Recalling also"
    return words[0]


def _split_countries(s: str) -> list[str]:
    """Split a comma-separated country list, dropping trailing bracket/period."""
    s = s.strip().rstrip("]").rstrip(".").strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _consume_vote_block(lrows: list["LRow"], i: int) -> tuple[int, list[int], str, dict, dict | None]:
    """Greedily consume a vote block starting at lrows[i].

    Returns (positions, summary_text, vote, vote_summary). The block is the
    optional "[Adopted by a recorded vote ...]" line plus the
    "In favour:/Against:/Abstaining:" labels and their country lists (whether
    inline or split across following rows). Consumption stops at the first
    blank/footnote/divider/meeting/opening/annex/operative/date row, or at a
    non-label row while no tally label is currently open (so we never swallow
    unrelated tail prose)."""
    n = len(lrows)
    positions: list[int] = []
    summary_parts: list[str] = []
    vote: dict[str, list[str]] = {"in_favour": [], "against": [], "abstaining": []}
    vote_summary: dict | None = None
    cur: str | None = None
    j = i
    started = False
    while j < n:
        r = lrows[j]
        rc = r.clean
        if r.kind in ("empty", "section_break", "footnote") or not rc:
            break
        if (DIVIDER_RE.match(rc) or MEETING_RE.match(rc) or OPENING_RE.match(rc)
                or ANNEX_RE.match(rc) or OP_NUM_RE.match(rc) or DATE_LINE_RE.match(rc)):
            break
        m_label = VOTE_TALLY_RE.match(rc)
        is_adopted = bool(VOTE_RE.match(rc))
        if started and not (m_label or is_adopted) and cur is None:
            break
        if is_adopted:
            summary_parts.append(rc)
            ms = VOTE_SUMMARY_RE.search(rc)
            if ms:
                vote_summary = {"in_favour": int(ms.group(1)), "against": int(ms.group(2)),
                                "abstaining": int(ms.group(3)) if ms.group(3) else 0}
        elif m_label:
            cur = VOTE_KEY.get(m_label.group(1).lower())
            if cur is not None:
                vote.setdefault(cur, [])
                rest = m_label.group(2).strip()
                if rest:
                    vote[cur].extend(_split_countries(rest))
        elif cur is not None:
            vote[cur].extend(_split_countries(rc))
        positions.extend(r.positions)
        j += 1
        started = True
    summary = " ".join(summary_parts) if summary_parts else "Vote record"
    return j, positions, summary, vote, vote_summary


def _footnote_text(raw_text: str) -> str:
    """Clean a footnote body: drop leading tabs and marker glyphs (*, ?, N/, N.)."""
    t = _clean(raw_text)
    t = re.sub(r"^[\*\?†‡\s]+", "", t)         # symbol markers
    t = re.sub(r"^\d{1,3}[/.]\s*", "", t)                 # "1/ " or "1. "
    t = re.sub(r"^[\*\?†‡]+\s*", "", t)
    return t.strip()


# ---------------------------------------------------------------------------
# Action-verb annotation pass (migration 004 / sem-v2)
# ---------------------------------------------------------------------------


def annotate_actions(elements: list[dict]) -> None:
    """Attach a nested ``action`` dict to each operative/preambular element in place.

    Runs the deterministic action-verb parser (``fulltext_verbs.extract_action``)
    over the element sequence in document order, resolving each clause's *chapeau*
    context EXACTLY as ``fulltext_verbs_eval.run_parser_over_doc`` does (this is the
    single source of truth for that resolution — keep the two in lockstep):

      * only ``paragraph_type IN ('operative','preambular')`` elements are annotated;
        every other element (PRST/statement bodies, annexes, headings, votes, and
        every ``paragraph_type IS NULL`` element) is left untouched — no ``action`` key;
      * a top-level (level<=1) clause that does NOT end in ':' resets the inherited
        chapeau context;
      * the row a sub-item inherits from is the most recent operative element ending
        in ':' at a shallower level — with ``governing_verb_for_children`` overriding
        the line's own leading verb for declaration ('We decide to:') and passive
        personified ('... are encouraged ... :') chapeaux.

    Elements whose ``extract_action`` returns ``None`` (noun-phrase budget sub-items,
    chapeau-less continuations, non-action headings that slipped through) get no
    ``action`` key and so load as all-NULL action columns. The Chapter-VII marker
    ('Acting under Chapter VII …') IS attached (it has a context_marker) even though
    its normalized verb is None. The pass never alters ``text``/``positions`` — it is
    purely additive, so the accounting invariant and text-preservation gate are
    unaffected.
    """
    chapeau: dict | None = None
    for el in elements:
        ptype = el.get("paragraph_type")
        if ptype not in ("operative", "preambular"):
            continue
        text = el.get("text") or ""
        level = el.get("level")
        prefix = el.get("prefix")
        is_colon = text.rstrip().endswith(":")
        lvl = level if level is not None else (1 if ptype == "operative" else 0)
        # a new top-level, non-chapeau clause resets inherited context
        if lvl <= 1 and not is_colon:
            chapeau = None
        action = fv.extract_action(
            text, paragraph_type=ptype, level=level, prefix=prefix,
            chapeau_action=chapeau,
        )
        # this row becomes the chapeau for following sub-items if it opens a list;
        # a governing verb overrides the line's own leading verb for what children
        # inherit ('We decide to:' -> decide; '... are encouraged to ... :' -> encourage).
        gov = fv.governing_verb_for_children(text)
        if gov is not None:
            chapeau = gov
        elif (action and not action.get("inherited") and is_colon
                and action.get("normalized")):
            chapeau = action
        if action is not None:
            el["action"] = action


# ---------------------------------------------------------------------------
# Main per-document parse
# ---------------------------------------------------------------------------


def parse_document(symbol: str, fmt: str, raw_rows: list[dict]) -> dict:
    lrows = build_logical_rows(raw_rows, fmt)
    subres = detect_subres_blocks(lrows)
    # lrow indices whose positions are consumed by a block's merged title element
    title_skip: set[int] = set()
    for blk in subres.values():
        title_skip.update(blk["title_idx"])

    elements: list[dict] = []
    dropped: list[dict] = []
    issues: list[dict] = []

    state = "front"          # front -> preamble -> operative ; plus tail signals
    section = "main"
    annex_index = 0
    text_index = 1
    seen_opening = False
    seen_title = False
    pending_block_opening = False   # a sub-res boundary already bumped text_index;
                                    # suppress the imminent opening's own bump
    annex_scoped = False            # current annex/appendix is a scoped instrument
                                    # (its numbered paras are labeled operative)
    last_op_number = 0              # last top-level operative number seen (for rescue)
    op_tracker = OpLevelTracker()

    i = 0
    n = len(lrows)
    while i < n:
        lr = lrows[i]
        c = lr.clean
        kind = lr.kind

        # 0. sub-res block title lines are emitted with their block's letter
        # heading (below); skip them here so positions are consumed exactly once.
        if i in title_skip:
            i += 1
            continue

        # 0b. sub-resolution block boundary (letter heading + title + opening ahead)
        if i in subres:
            blk = subres[i]
            if seen_opening:
                text_index += 1
                pending_block_opening = True
            op_tracker.reset()
            op_tracker.top()
            section = "main"
            annex_index = 0
            state = "blockhead"
            elements.append(_new_element(lr, type="heading", section="main",
                                         heading_level=1, text=c,
                                         text_index=text_index, subtype="subres"))
            tidx = blk["title_idx"]
            if tidx:
                t_positions: list[int] = []
                t_texts: list[str] = []
                t_hyper: list = []
                for k in tidx:
                    t_positions.extend(lrows[k].positions)
                    if lrows[k].clean:
                        t_texts.append(lrows[k].clean)
                    t_hyper.extend(lrows[k].hyperlinks)
                title_el = {
                    "positions": t_positions, "type": "title", "section": "main",
                    "paragraph_type": None, "level": None, "prefix": None,
                    "heading_level": None, "text": " ".join(t_texts),
                    "lead_verb": None, "hyperlinks": t_hyper, "note_ids": [],
                }
                if text_index != 1:
                    title_el["text_index"] = text_index
                elements.append(title_el)
                seen_title = True
            i += 1
            continue

        # 1. empties & section breaks -----------------------------------------
        if kind == "empty" or (kind != "footnote" and not c):
            reason = "empty" if kind in ("empty", "paragraph") else kind
            for p in lr.positions:
                dropped.append({"position": p, "reason": reason})
            i += 1
            continue
        if kind == "section_break":
            for p in lr.positions:
                dropped.append({"position": p, "reason": "section_break"})
            i += 1
            continue

        # 2. native/doc footnote rows -----------------------------------------
        if kind == "footnote":
            note_id = None
            if isinstance(lr._fr, dict):
                note_id = lr._fr.get("note_id")
            el = _new_element(lr, type="footnote", section=section, text=_footnote_text(lr.text),
                              text_index=text_index)
            if note_id is not None:
                el["note_ids"] = [note_id]
            elements.append(el)
            i += 1
            continue

        # 3. table cells: group consecutive cells -----------------------------
        if kind == "table_cell":
            j = i
            group: list[LRow] = []
            while j < n and lrows[j].kind == "table_cell":
                group.append(lrows[j])
                j += 1
            positions = [p for g in group for p in g.positions]
            cells = [g.clean for g in group if g.clean]
            # masthead (before we've reached the body) vs genuine data table
            is_masthead = (state == "front" and not seen_title
                           and len(cells) <= 12)
            if not cells:
                for p in positions:
                    dropped.append({"position": p, "reason": "layout_cell"})
            else:
                el = {
                    "positions": positions,
                    "type": "frontmatter" if is_masthead else "table",
                    "section": section,
                    "paragraph_type": None,
                    "level": None,
                    "prefix": None,
                    "heading_level": None,
                    "text": " | ".join(cells),
                    "lead_verb": None,
                    "hyperlinks": [h for g in group for h in g.hyperlinks],
                    "note_ids": [],
                }
                if is_masthead:
                    el["subtype"] = "masthead"
                if text_index != 1:
                    el["text_index"] = text_index
                elements.append(el)
            i = j
            continue

        # 4. divider (footnote separator) -------------------------------------
        if DIVIDER_RE.match(c):
            elements.append(_new_element(lr, type="divider", section=section, text=c,
                                         text_index=text_index))
            i += 1
            continue

        # 5. WP inline footnote "N/ text" -------------------------------------
        if fmt == "wpd" and WP_FOOTNOTE_RE.match(c):
            m = WP_FOOTNOTE_RE.match(c)
            el = _new_element(lr, type="footnote", section=section,
                              text=m.group(2).strip(), text_index=text_index)
            el["note_ids"] = [int(m.group(1))]
            elements.append(el)
            i += 1
            continue

        # 6. opening formula --------------------------------------------------
        if OPENING_RE.match(c):
            if seen_opening and not pending_block_opening:
                # repeated opening with NO preceding letter heading (e.g. an ECOSOC
                # resolution that recommends a GA text): still a distinct text block.
                text_index += 1
                op_tracker.reset()
                section = "main"
                annex_index = 0
            pending_block_opening = False
            seen_opening = True
            state = "preamble"
            elements.append(_new_element(lr, type="opening", section=section,
                                         paragraph_type="preambular", level=0,
                                         text=c, text_index=text_index))
            i += 1
            continue

        # 7. annex / appendix heading -> new section --------------------------
        if ANNEX_RE.match(c) and state != "front":
            m = ANNEX_RE.match(c)
            if m.group(1).lower() == "annex":
                annex_index += 1
                section = "annex"
            else:
                section = "appendix"
            op_tracker.top()
            op_tracker.reset()
            annex_sub = _annex_subtype(lrows, i, c)
            annex_scoped = annex_sub == "instrument"
            el = _new_element(lr, type="heading", section=section, heading_level=1,
                              text=c, text_index=text_index)
            if section == "annex":
                el["annex_index"] = annex_index
            if annex_sub:
                el["subtype"] = annex_sub
            elements.append(el)
            # 'annextitle' captures the instrument/annex title line as a title
            # element; then the (scoped) preamble/operative machine runs.
            state = "annextitle"
            i += 1
            continue

        # 7b. annex title line (first content line after an annex heading) ------
        if state == "annextitle":
            # only a plain title line; structural rows fall through to be parsed
            if not (OPENING_RE.match(c) or OP_NUM_RE.match(c) or OP_PAREN_RE.match(c)
                    or ANNEX_RE.match(c) or _looks_like_heading(c, lr, "operative")
                    or MEETING_RE.match(c) or DIVIDER_RE.match(c)):
                elements.append(_new_element(lr, type="title", section=section,
                                             text=c, text_index=text_index))
                state = "preamble"
                i += 1
                continue
            state = "preamble"  # no distinct title; reparse this row below

        # 8. titles (front region) --------------------------------------------
        if state == "front" or not seen_opening:
            title = _match_title(c, lr)
            if title is not None:
                ttype, prefix, ttext = title
                elements.append(_new_element(lr, type="title", section=section,
                                             prefix=prefix, text=ttext,
                                             text_index=text_index))
                seen_title = True
                # PRSTs have no opening formula: their quoted body is a statement,
                # so switch out of the frontmatter phase here.
                if TITLE_PRST_RE.match(c):
                    state = "statement"
                i += 1
                continue

        # 9. vote record: consume the whole block into one element -----------
        if VOTE_RE.match(c) or VOTE_TALLY_RE.match(c):
            j, positions, summary, vote, vsum = _consume_vote_block(lrows, i)
            el = {
                "positions": positions,
                "type": "vote_record",
                "section": section,
                "paragraph_type": None,
                "level": None,
                "prefix": None,
                "heading_level": None,
                "text": summary,
                "lead_verb": None,
                "hyperlinks": [],
                "note_ids": [],
                "vote": vote,
            }
            if vsum:
                el["vote_summary"] = vsum
            if text_index != 1:
                el["text_index"] = text_index
            elements.append(el)
            state = "tail"
            i = j if j > i else i + 1
            continue

        # 10. signature / meeting line ----------------------------------------
        if MEETING_RE.match(c) or (state == "tail" and DATE_LINE_RE.match(c)):
            elements.append(_new_element(lr, type="signature", section=section,
                                         text=c, text_index=text_index))
            state = "tail"
            i += 1
            continue

        # 11. operative paragraph ---------------------------------------------
        # NB: paragraph_type='operative'/'preambular' is a *main-section* concept.
        # Annex/appendix bodies are frequently numbered too (programmes of action,
        # agendas), but those are backmatter, not resolution operatives -- so we
        # keep their prefix/level yet set paragraph_type=None there.
        # label operatives in the main section AND inside a scoped instrument annex
        in_main = section == "main"
        label_ops = in_main or annex_scoped
        m_num = OP_NUM_RE.match(c)
        if m_num and not TITLE_GA_NUM_RE.match(c):
            op_tracker.top()
            last_op_number = int(m_num.group(1))
            elements.append(_new_element(
                lr, type="paragraph", section=section,
                paragraph_type="operative" if label_ops else None,
                level=1, prefix=f"{m_num.group(1)}.", text=m_num.group(2).strip(),
                lead_verb=_op_lead_verb(m_num.group(2)), text_index=text_index))
            state = "operative"
            i += 1
            continue

        # tolerant operative number (missing/misplaced period), gated hard:
        # only continue an already-running operative sequence, and only when the
        # clause opens with a finite operative verb -- so "232 .Recognizes",
        # "4 Calls", "13 Urges" are rescued without misreading years/quantities.
        if state == "operative" and not m_num and not TITLE_GA_NUM_RE.match(c):
            m_loose = OP_NUM_LOOSE_RE.match(c)
            if m_loose:
                rest = m_loose.group(2).strip()
                w0 = rest.split(" ", 1)[0].strip(",.").lower()
                # source may drop the period AND glue the number to an adverb-led
                # verb ("6Also reaffirms ..."): look past a leading adverb for the
                # finite operative verb before deciding.
                verb = w0
                if w0 in ("also", "further", "again", "finally", "moreover") and " " in rest:
                    verb = rest.split(" ", 2)[1].strip(",.").lower()
                if verb in OPERATIVE_LEAD_VERBS:
                    op_tracker.top()
                    last_op_number = int(m_loose.group(1))
                    elements.append(_new_element(
                        lr, type="paragraph", section=section,
                        paragraph_type="operative" if label_ops else None,
                        level=1, prefix=f"{m_loose.group(1)}.",
                        text=rest,
                        lead_verb=_op_lead_verb(rest), text_index=text_index))
                    state = "operative"
                    i += 1
                    continue

        m_par = OP_PAREN_RE.match(c)
        if m_par and state in ("operative", "preamble"):
            level, prefix = op_tracker.classify_paren(
                m_par.group(1), next_tok=_next_paren_token(lrows, i + 1))
            # A parenthetical in the PREAMBLE is a sub-item of the preceding
            # preambular clause (often introduced by a clause ending in ':'),
            # NOT the first operative -- operatives are introduced by "1." So we
            # keep it preambular and do NOT switch state. Only in the operative
            # part is a parenthetical an operative subparagraph.
            base = "operative" if state == "operative" else "preambular"
            elements.append(_new_element(
                lr, type="paragraph", section=section,
                paragraph_type=base if label_ops else None,
                level=level, prefix=prefix, text=m_par.group(2).strip(),
                text_index=text_index))
            i += 1
            continue

        # 12. body heading (roman/letter/short heading-styled line) -----------
        if _looks_like_heading(c, lr, state):
            elements.append(_new_element(lr, type="heading", section=section,
                                         heading_level=_heading_level(lr),
                                         text=c, text_index=text_index))
            i += 1
            continue

        # 13. frontmatter residue (session/agenda/masthead lines) -------------
        if state in ("front", "blockhead"):
            # drop obvious page artifacts, keep informative masthead as frontmatter
            if RUNNING_HEADER_RE.match(c) or PAGE_NUM_RE.match(c):
                for p in lr.positions:
                    dropped.append({"position": p, "reason": "page_artifact"})
            else:
                st = "masthead" if (MASTHEAD_RE.match(c) or DATE_LINE_RE.match(c)) else None
                elements.append(_new_element(lr, type="frontmatter", section=section,
                                             text=c, text_index=text_index, subtype=st))
            i += 1
            continue

        # 14. preambular ------------------------------------------------------
        if state == "preamble":
            lead_it = lr.props.get("lead_italic_text")
            first = c.split(" ", 1)[0].strip(",").lower()
            # Unnumbered operative: some short resolutions have a single operative
            # clause with no "1." -- it is a *finite* verb (Decides/Requests...),
            # whereas preambular leads are participles (-ing) or adjectives.
            lead_word = (lead_it or c).split(" ", 1)[0].strip(",").lower()
            if lead_word in OPERATIVE_LEAD_VERBS and first not in PREAMBULAR_FIRST_WORDS:
                op_tracker.top()
                elements.append(_new_element(
                    lr, type="paragraph", section=section,
                    paragraph_type="operative" if label_ops else None,
                    level=1, prefix=None, text=c, lead_verb=_op_lead_verb(c),
                    text_index=text_index))
                state = "operative"
                i += 1
                continue
            lead = lead_it or _lead_verb_from_text(c)
            elements.append(_new_element(
                lr, type="paragraph", section=section,
                paragraph_type="preambular" if label_ops else None,
                level=1, text=c, lead_verb=lead, text_index=text_index))
            i += 1
            continue

        # 14b. OPT-IN source-defect rescue: an unlabeled operative whose NUMBER was
        # dropped at source. Fires only inside a running operative sequence with a
        # CONFIRMED numbering gap ahead, and only for clauses that read operative.
        if (RESCUE_INFERRED_OPERATIVE and state == "operative" and label_ops
                and not OP_NUM_RE.match(c) and not OP_PAREN_RE.match(c)
                and not _looks_like_heading(c, lr, state)):
            first = c.split(" ", 1)[0].strip(",.").lower()
            in_alpha_run = op_tracker.last_alpha is not None and not op_tracker.roman_active
            looks_op = first in OPERATIVE_LEAD_VERBS or INFINITIVE_SUBITEM_RE.match(c)
            inferred = False
            level = 1
            if looks_op:
                if in_alpha_run:
                    nxt = _next_number_ahead(lrows, i + 1, paren=True)
                    cur_ord = _alpha_ord(op_tracker.last_alpha) or 0
                    if nxt is not None and nxt > cur_ord + 1:
                        inferred, level = True, 2
                else:
                    nxt = _next_number_ahead(lrows, i + 1, paren=False)
                    if nxt is not None and nxt > last_op_number + 1:
                        inferred, level = True, 1
            if inferred:
                el = _new_element(
                    lr, type="paragraph", section=section,
                    paragraph_type="operative", level=level, prefix=None, text=c,
                    lead_verb=_op_lead_verb(c) if level == 1 else None,
                    text_index=text_index)
                el["inferred_operative"] = True
                elements.append(el)
                i += 1
                continue

        # 15. operative continuation / tail body / annex prose / PRST body ----
        if state in ("operative", "tail"):
            # An UNPREFIXED line inside the operative part is a continuation /
            # chapeau / stray heading, not a distinct numbered operative -- a
            # real operative clause always carries a prefix (or was caught as an
            # unnumbered operative in step 14). So paragraph_type=None here, to
            # avoid inflating operative counts.
            elements.append(_new_element(
                lr, type="paragraph", section=section, paragraph_type=None,
                level=None, text=c, text_index=text_index))
            i += 1
            continue

        # 16. PRST / statement body (no opening formula seen) -----------------
        if not seen_opening:
            elements.append(_new_element(lr, type="paragraph", section=section,
                                         paragraph_type=None, level=1, text=c,
                                         text_index=text_index))
            i += 1
            continue

        # 17. fallback: unclassified -----------------------------------------
        issues.append({"position": lr.positions[0], "problem": "unclassified paragraph",
                       "text_head": c[:80]})
        elements.append(_new_element(lr, type="paragraph", section=section,
                                     paragraph_type=None, text=c, text_index=text_index))
        i += 1

    # sem-v2: annotate operative/preambular elements with their action verb in a
    # cleanly separable pass (additive; never touches text/positions/accounting).
    annotate_actions(elements)

    result = {
        "symbol": symbol,
        "format": fmt,
        "parser_version": PARSER_VERSION,
        "elements": elements,
        "dropped": dropped,
        "issues": issues,
    }
    return result


def _alpha_ord(s: str) -> int | None:
    """Ordinal of a subparagraph letter, honoring the UN doubling convention past z
    ('aa'=27,'bb'=28,..). Mirrors fulltext_review._alpha_value so gap arithmetic
    agrees on both sides."""
    s = s.lower()
    if not s.isalpha():
        return None
    if len(s) >= 2 and len(set(s)) == 1:
        return (len(s) - 1) * 26 + (ord(s[0]) - ord("a") + 1)
    if len(s) == 1:
        return ord(s) - ord("a") + 1
    return None


def _next_number_ahead(lrows: list[LRow], i: int, paren: bool) -> int | None:
    """Ordinal of the next labeled operative item ahead of lrows[i], or None.

    paren=False -> next top-level 'N.' number; paren=True -> next '(letter)' alpha
    ordinal. Scans a bounded window and stops at a hard structural boundary so the
    look-ahead never crosses into another text/annex/preamble.
    """
    n = len(lrows)
    j = i
    steps = 0
    while j < n and steps < 30:
        cj = lrows[j].clean
        if not cj or lrows[j].kind in ("empty", "section_break", "footnote"):
            j += 1
            continue
        if (OPENING_RE.match(cj) or ANNEX_RE.match(cj) or MEETING_RE.match(cj)
                or VOTE_RE.match(cj) or VOTE_TALLY_RE.match(cj) or DIVIDER_RE.match(cj)):
            return None
        if not paren:
            m = OP_NUM_RE.match(cj)
            if m and not TITLE_GA_NUM_RE.match(cj):
                return int(m.group(1))
        else:
            m = OP_PAREN_RE.match(cj)
            if m:
                return _alpha_ord(m.group(1))
        j += 1
        steps += 1
    return None


def _next_paren_token(lrows: list[LRow], i: int) -> str | None:
    """The next '(marker)' token at/after lrows[i] within a bounded window, or None.
    Stops at a hard structural boundary so it never crosses into another run."""
    n = len(lrows)
    j, steps = i, 0
    while j < n and steps < 20:
        cj = lrows[j].clean
        if not cj or lrows[j].kind in ("empty", "section_break", "footnote"):
            j += 1
            continue
        if (OPENING_RE.match(cj) or ANNEX_RE.match(cj) or MEETING_RE.match(cj)
                or VOTE_RE.match(cj) or DIVIDER_RE.match(cj) or OP_NUM_RE.match(cj)):
            return None
        m = OP_PAREN_RE.match(cj)
        if m:
            return m.group(1)
        j += 1
        steps += 1
    return None


def _op_lead_verb(rest: str) -> str | None:
    """Leading verb of an operative clause (first word, if title-case-ish)."""
    words = rest.strip().split()
    if not words:
        return None
    w = words[0].strip(",")
    if w[:1].isupper() and w.isalpha():
        # "Also welcomes" / "Further requests" -> include the adverb
        if w.lower() in ("also", "further", "again", "finally", "moreover") and len(words) > 1:
            return f"{w} {words[1].strip(',')}"
        return w
    return None


def _match_title(c: str, lr: LRow) -> tuple[str, str | None, str] | None:
    """Return (type, prefix, text) if `c` is a resolution/PRST title, else None."""
    if TITLE_PRST_RE.match(c):
        return ("title", None, c)
    if TITLE_SC_RE.match(c):
        return ("title", None, c)
    m = TITLE_GA_NUM_RE.match(c)
    if m:
        return ("title", f"{m.group(1)}.", m.group(2).strip())
    # HRC "15/7 Subject" -- require a heading style to avoid matching cross refs
    if lr.style in ("HChG", "HCh", "TitleH1", "TitleHCH") and TITLE_HRC_NUM_RE.match(c):
        m2 = TITLE_HRC_NUM_RE.match(c)
        return ("title", m2.group(1), m2.group(2).strip())
    return None


def _looks_like_heading(c: str, lr: LRow, state: str) -> bool:
    """Heuristic for a section heading inside the body (not title/opening)."""
    if state == "front":
        return False
    if len(c) > 80:
        return False
    # lone roman numeral or single capital letter (consolidated sub-res letter,
    # or numbered section heading within the operative part)
    if re.match(r"^[IVXLC]{1,4}\.?$", c) or re.match(r"^[A-Z]\.?$", c):
        return True
    # "A. Title" / "I. Title" style section heading -- accept when bold OR when the
    # paragraph carries an explicit heading style (H23 etc.), so instrument-annex
    # section headers ('A. Mandate', 'B. Objectives') that are not bold-flagged are
    # still recognised and do not leak into preambular/operative labeling.
    if re.match(r"^([IVXLC]{1,4}|[A-Z])\.\s+[A-Z]", c) and (
            lr.props.get("bold") or lr.style in BODY_HEADING_STYLES):
        return True
    # explicit heading style, short, and centered/bold
    if lr.style in BODY_HEADING_STYLES and (lr.props.get("bold") or lr.props.get("alignment") == "center"):
        return True
    return False


def _heading_level(lr: LRow) -> int:
    m = re.match(r"H(\d)", lr.style or "")
    if m:
        return int(m.group(1))
    return 1


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def fetch_targets(limit: int | None, symbol: str | None, offset: int = 0) -> list[tuple[str, str, str]]:
    """Return (symbol_normalized, lang, format) for parseable documents.

    Targets any doc whose raw extraction is available: status IN
    ('extracted', 'parsed'). Including 'parsed' keeps re-parses working after the
    loader has advanced status (a plain JSON re-run, or a re-load with --to-db,
    still finds every already-loaded doc).
    """
    sql = (
        "SELECT df.symbol_normalized, df.lang, df.format FROM digitallibrary.document_files df "
        "WHERE df.status IN ('extracted', 'parsed') "
    )
    params: list[object] = []
    if symbol:
        sql += "AND df.symbol_normalized = %s "
        params.append(symbol)
    sql += "ORDER BY (df.status = 'extracted') DESC, df.symbol_normalized "
    if limit:
        sql += "LIMIT %s "
        params.append(limit)
    if offset:
        sql += "OFFSET %s"
        params.append(offset)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], r[1] or "en", r[2]) for r in cur.fetchall()]


def fetch_rows(conn, symbol: str, lang: str = "en") -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT position, kind, text, style_id, style_name, numbering, props, "
            "table_cell, hyperlinks, footnote_ref "
            "FROM digitallibrary.document_paragraphs_raw "
            "WHERE symbol_normalized = %s AND lang = %s ORDER BY position",
            [symbol, lang],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Semantic DB loader (migration 003 tables)
# ---------------------------------------------------------------------------

# Insert column order for digitallibrary.document_paragraphs. `id` and `position`
# are computed by the loader; the rest are read off each parsed element.
_PARA_COLUMNS = (
    "symbol_normalized", "lang", "position", "id", "type", "subtype", "section",
    "annex_index", "text_index", "paragraph_type", "level", "heading_level",
    "prefix", "lead_verb", "text", "raw_positions", "inferred_operative",
    "vote", "vote_summary", "hyperlinks", "note_ids", "parser_version",
    # migration 004: flattened action-verb annotation (NULL unless the element
    # carries a nested `action` object, i.e. an annotated operative/preambular clause)
    "action_verb", "action_verb_normalized", "action_category", "action_force",
    "action_sentiment", "action_bindingness", "action_budget_relevant",
    "action_modifiers", "assignee", "assignee_head_noun", "assignee_class",
    "action_inherited", "action_context_marker",
)
_PARA_INSERT = (
    f"INSERT INTO digitallibrary.document_paragraphs ({', '.join(_PARA_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(_PARA_COLUMNS))})"
)


def element_uuid(symbol: str, lang: str, position: int) -> uuid.UUID:
    """Deterministic element id: uuid5(NAMESPACE_URL, '<symbol>:<lang>:<position>').

    `position` is the 0-based element index in parsed order. Stable across
    re-parses as long as element ordering is stable.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{symbol}:{lang}:{position}")


def load_document(conn, symbol: str, lang: str, fmt: str, result: dict) -> int:
    """Delete-then-insert one parsed document into the semantic tables.

    Writes document_paragraphs (one row per element, 0-based position) and one
    document_parses ledger row (element_count + JSON root dropped[]/issues[]).
    Caller owns the transaction (commit/rollback per doc). Returns element count.
    """
    elements = result["elements"]
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM digitallibrary.document_paragraphs "
            "WHERE symbol_normalized = %s AND lang = %s",
            [symbol, lang],
        )
        cur.execute(
            "DELETE FROM digitallibrary.document_parses "
            "WHERE symbol_normalized = %s AND lang = %s",
            [symbol, lang],
        )
        rows = []
        for pos, el in enumerate(elements):
            vote = el.get("vote")
            vote_summary = el.get("vote_summary")
            action = el.get("action")
            assignee = action.get("assignee") if action else None
            rows.append((
                symbol, lang, pos, str(element_uuid(symbol, lang, pos)),
                el["type"], el.get("subtype"), el.get("section", "main"),
                el.get("annex_index"), el.get("text_index", 1),
                el.get("paragraph_type"), el.get("level"), el.get("heading_level"),
                el.get("prefix"), el.get("lead_verb"), el["text"],
                el["positions"], bool(el.get("inferred_operative", False)),
                json.dumps(vote) if vote is not None else None,
                json.dumps(vote_summary) if vote_summary is not None else None,
                json.dumps(el.get("hyperlinks") or []),
                json.dumps(el.get("note_ids") or []),
                result["parser_version"],
                # migration 004: flattened action annotation (all NULL when no action)
                action.get("verb") if action else None,
                action.get("normalized") if action else None,
                action.get("category") if action else None,
                action.get("force") if action else None,
                action.get("sentiment") if action else None,
                action.get("bindingness") if action else None,
                action.get("budget_relevant") if action else None,
                json.dumps(action["modifiers"]) if action and action.get("modifiers") else None,
                assignee.get("verbatim") if assignee else None,
                assignee.get("head_noun") if assignee else None,
                assignee.get("addressee_class") if assignee else None,
                action.get("inherited") if action else None,
                action.get("context_marker") if action else None,
            ))
        if rows:
            cur.executemany(_PARA_INSERT, rows)
        cur.execute(
            "INSERT INTO digitallibrary.document_parses "
            "(symbol_normalized, lang, parser_version, format, element_count, dropped, issues) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
            [symbol, lang, result["parser_version"], fmt, len(elements),
             json.dumps(result.get("dropped", [])), json.dumps(result.get("issues", []))],
        )
    return len(elements)


def _check_accounting(result: dict, raw_rows: list[dict]) -> str | None:
    """Return an error string if the accounting invariant is violated, else None."""
    all_positions = {r["position"] for r in raw_rows}
    consumed: list[int] = []
    for el in result["elements"]:
        consumed.extend(el["positions"])
    for d in result["dropped"]:
        consumed.append(d["position"])
    consumed_set = set(consumed)
    if len(consumed) != len(consumed_set):
        dupes = [p for p in consumed_set if consumed.count(p) > 1]
        return f"duplicate positions: {sorted(dupes)[:10]}"
    missing = all_positions - consumed_set
    extra = consumed_set - all_positions
    if missing:
        return f"unaccounted positions: {sorted(missing)[:10]}"
    if extra:
        return f"phantom positions: {sorted(extra)[:10]}"
    return None


BATCH_DOCS = 20  # docs per short-lived DB connection (mirrors fulltext_extract_raw.py)


def main() -> int:
    ap = argparse.ArgumentParser(description="Semantic full-text parser (v1)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--symbol")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--to-db", action="store_true",
                    help="load the semantic DB tables (migration 003) alongside JSON")
    ap.add_argument("--db-only", action="store_true",
                    help="load the semantic DB only; skip writing JSON files")
    args = ap.parse_args()

    to_db = args.to_db or args.db_only
    write_json = not args.db_only

    out_dir = Path(args.out)
    if write_json:
        out_dir.mkdir(parents=True, exist_ok=True)

    targets = fetch_targets(args.limit, args.symbol, args.offset)
    dest = []
    if write_json:
        dest.append(str(out_dir))
    if to_db:
        dest.append("db(document_paragraphs, document_parses)")
    print(f"Parsing {len(targets)} documents -> {', '.join(dest) or '(nothing)'}")

    n_ok = 0
    n_acct_fail = 0
    n_loaded = 0
    n_failed = 0
    total_elems = 0
    for start in range(0, len(targets), BATCH_DOCS):
        chunk = targets[start:start + BATCH_DOCS]
        with get_conn() as conn:
            for symbol, lang, fmt in chunk:
                try:
                    raw_rows = fetch_rows(conn, symbol, lang)
                    result = parse_document(symbol, fmt, raw_rows)
                    err = _check_accounting(result, raw_rows)
                    if err:
                        n_acct_fail += 1
                        result.setdefault("issues", []).append(
                            {"position": -1, "problem": "accounting", "text_head": err})
                        print(f"  ! {symbol}: ACCOUNTING {err}")
                    if write_json:
                        out_path = out_dir / f"{sanitize_symbol(symbol)}.json"
                        out_path.write_text(
                            json.dumps(result, ensure_ascii=False, indent=1),
                            encoding="utf-8")
                    if to_db:
                        total_elems += load_document(conn, symbol, lang, fmt, result)
                        upsert_document_file(conn, symbol, lang, status="parsed", error=None)
                        conn.commit()
                        n_loaded += 1
                    n_ok += 1
                except Exception as exc:  # never crash the batch on one doc
                    if to_db:
                        conn.rollback()
                        try:
                            upsert_document_file(
                                conn, symbol, lang, status="parse_failed",
                                error=f"{type(exc).__name__}: {exc}"[:500])
                            conn.commit()
                        except Exception:
                            conn.rollback()
                    n_failed += 1
                    print(f"  ! {symbol}: {type(exc).__name__}: {exc}")
        done = start + len(chunk)
        if done % 100 == 0 or done == len(targets):
            print(f"  parsed {done}/{len(targets)} ok={n_ok} loaded={n_loaded} failed={n_failed}")

    print(f"\nDone: {n_ok} parsed, {n_acct_fail} accounting failures, "
          f"{n_failed} failed.")
    if to_db:
        print(f"Loaded {n_loaded} docs, {total_elems} element rows into the semantic DB.")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
