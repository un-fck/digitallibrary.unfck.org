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
from .env, short-lived psycopg connection, no new DB tables. The schema is a
working hypothesis and is expected to be iterated -- deviations from the brief
are documented in module docstrings and the run report.

Usage:
    uv run python python/fulltext_parse.py            # all extracted docs
    uv run python python/fulltext_parse.py --limit 5
    uv run python python/fulltext_parse.py --symbol A/RES/48/70
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from fulltext_common import ARCHIVE_ROOT, get_conn, sanitize_symbol

PARSER_VERSION = "sem-v1"
OUT_DIR = ARCHIVE_ROOT / "parsed_dev"

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
}

OPENING_RE = re.compile(
    r"^The\s+(General Assembly|Security Council|Economic and Social Council|"
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
OP_PAREN_RE = re.compile(r"^\(([A-Za-z]{1,5}|\d{1,3})\)\s+(\S.*)$")  # "(a) ...", "(i) ...", "(1) ..."

# Frontmatter / structural line patterns.
DIVIDER_RE = re.compile(r"^_{3,}$")
WP_FOOTNOTE_RE = re.compile(r"^(\d{1,3})/\s+(\S.*)$")    # "1/ United Nations, Treaty Series ..."
ANNEX_RE = re.compile(r"^(Annex|Appendix)(\s+[IVXLCDM]+|\s+[A-Z])?\s*$", re.I)
VOTE_RE = re.compile(r"^\[?\s*Adopted\b.*(vote|without a vote)", re.I)
VOTE_TALLY_RE = re.compile(r"^(In favour|Against|Abstaining|Non-Voting|Absent)\s*:", re.I)
MEETING_RE = re.compile(r"^\d+\s*(st|nd|rd|th)\s+(plenary\s+)?meeting\b", re.I)
DATE_LINE_RE = re.compile(
    r"^\d{1,2}\s+(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{4}\s*$"
)
SESSION_RE = re.compile(r"session\s*$", re.I)
AGENDA_RE = re.compile(r"^Agenda item\b", re.I)
RUNNING_HEADER_RE = re.compile(r"^[A-Z]+(/[A-Z0-9()./-]+)+$")   # bare doc symbol like "A/RES/48/70"
PAGE_NUM_RE = re.compile(r"^-?\s*\d{1,4}\s*-?$")
ADOPTED_BY_RE = re.compile(r"^Adopted by the (Security Council|General Assembly)", re.I)
RES_ADOPTED_RE = re.compile(r"^Resolution adopted by ", re.I)
REPORT_NOTE_RE = re.compile(r"^\[on the (report|recommendation) ", re.I)

# Heading styles seen in the corpus (native docx / doc). TitleH1 / TitleHCH are
# handled as titles; the rest mark section headings inside the body.
BODY_HEADING_STYLES = {"H1", "H2", "H3", "H4", "H23", "H1G", "H2G", "H3G", "H4G",
                       "HCh", "HChG", "HChM"}

ROMAN_CHARS = set("ivxlcdm")


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

    def classify_paren(self, token: str) -> tuple[int, str]:
        """Return (level, prefix) for a parenthetical marker token like 'a','i','iv','1'."""
        tok = token.lower()
        prefix = f"({token})"
        if tok.isdigit():
            return 2, prefix  # numeric subpara -- treat as level 2 variant
        # multi-char: roman if all roman chars, else treat as deeper alpha
        if len(tok) > 1:
            if all(ch in ROMAN_CHARS for ch in tok):
                self.roman_active = True
                return 3, prefix
            return 2, prefix
        # single char
        if tok == "i":
            if self.last_alpha == "h":       # ... (g)(h)(i) alpha run
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


def _footnote_text(raw_text: str) -> str:
    """Clean a footnote body: drop leading tabs and marker glyphs (*, ?, N/, N.)."""
    t = _clean(raw_text)
    t = re.sub(r"^[\*\?†‡\s]+", "", t)         # symbol markers
    t = re.sub(r"^\d{1,3}[/.]\s*", "", t)                 # "1/ " or "1. "
    t = re.sub(r"^[\*\?†‡]+\s*", "", t)
    return t.strip()


# ---------------------------------------------------------------------------
# Main per-document parse
# ---------------------------------------------------------------------------


def parse_document(symbol: str, fmt: str, raw_rows: list[dict]) -> dict:
    lrows = build_logical_rows(raw_rows, fmt)

    elements: list[dict] = []
    dropped: list[dict] = []
    issues: list[dict] = []

    state = "front"          # front -> preamble -> operative ; plus tail signals
    section = "main"
    annex_index = 0
    text_index = 1
    seen_opening = False
    seen_title = False
    op_tracker = OpLevelTracker()

    i = 0
    n = len(lrows)
    while i < n:
        lr = lrows[i]
        c = lr.clean
        kind = lr.kind

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
            if seen_opening:
                text_index += 1
                op_tracker.reset()
                section = "main"
                annex_index = 0
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
            el = _new_element(lr, type="heading", section=section, heading_level=1,
                              text=c, text_index=text_index)
            if section == "annex":
                el["annex_index"] = annex_index
            elements.append(el)
            state = "preamble"  # annex may restart with its own preamble/operatives
            i += 1
            continue

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

        # 9. vote record ------------------------------------------------------
        if VOTE_RE.match(c) or VOTE_TALLY_RE.match(c):
            elements.append(_new_element(lr, type="vote_record", section=section,
                                         text=c, text_index=text_index))
            state = "tail"
            i += 1
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
        in_main = section == "main"
        m_num = OP_NUM_RE.match(c)
        if m_num and not TITLE_GA_NUM_RE.match(c):
            op_tracker.top()
            elements.append(_new_element(
                lr, type="paragraph", section=section,
                paragraph_type="operative" if in_main else None,
                level=1, prefix=f"{m_num.group(1)}.", text=m_num.group(2).strip(),
                lead_verb=_op_lead_verb(m_num.group(2)), text_index=text_index))
            state = "operative"
            i += 1
            continue

        m_par = OP_PAREN_RE.match(c)
        if m_par and state in ("operative", "preamble"):
            level, prefix = op_tracker.classify_paren(m_par.group(1))
            # A parenthetical in the PREAMBLE is a sub-item of the preceding
            # preambular clause (often introduced by a clause ending in ':'),
            # NOT the first operative -- operatives are introduced by "1." So we
            # keep it preambular and do NOT switch state. Only in the operative
            # part is a parenthetical an operative subparagraph.
            base = "operative" if state == "operative" else "preambular"
            elements.append(_new_element(
                lr, type="paragraph", section=section,
                paragraph_type=base if in_main else None,
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
        if state == "front":
            # drop obvious page artifacts, keep informative masthead as frontmatter
            if RUNNING_HEADER_RE.match(c) or PAGE_NUM_RE.match(c):
                for p in lr.positions:
                    dropped.append({"position": p, "reason": "page_artifact"})
            else:
                elements.append(_new_element(lr, type="frontmatter", section=section,
                                             text=c, text_index=text_index))
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
                    paragraph_type="operative" if section == "main" else None,
                    level=1, prefix=None, text=c, lead_verb=_op_lead_verb(c),
                    text_index=text_index))
                state = "operative"
                i += 1
                continue
            lead = lead_it or _lead_verb_from_text(c)
            elements.append(_new_element(
                lr, type="paragraph", section=section,
                paragraph_type="preambular" if section == "main" else None,
                level=1, text=c, lead_verb=lead, text_index=text_index))
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

    result = {
        "symbol": symbol,
        "format": fmt,
        "parser_version": PARSER_VERSION,
        "elements": elements,
        "dropped": dropped,
        "issues": issues,
    }
    return result


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
    # "A. Title" / "I. Title" style section heading
    if re.match(r"^([IVXLC]{1,4}|[A-Z])\.\s+[A-Z]", c) and lr.props.get("bold"):
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


def fetch_targets(limit: int | None, symbol: str | None) -> list[tuple[str, str]]:
    sql = (
        "SELECT df.symbol_normalized, df.format FROM digitallibrary.document_files df "
        "WHERE df.status = 'extracted' "
    )
    params: list[object] = []
    if symbol:
        sql += "AND df.symbol_normalized = %s "
        params.append(symbol)
    sql += "ORDER BY df.symbol_normalized "
    if limit:
        sql += "LIMIT %s"
        params.append(limit)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], r[1]) for r in cur.fetchall()]


def fetch_rows(conn, symbol: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT position, kind, text, style_id, style_name, numbering, props, "
            "table_cell, hyperlinks, footnote_ref "
            "FROM digitallibrary.document_paragraphs_raw "
            "WHERE symbol_normalized = %s ORDER BY position",
            [symbol],
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


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


def main() -> int:
    ap = argparse.ArgumentParser(description="Semantic full-text parser (v1)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--symbol")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = fetch_targets(args.limit, args.symbol)
    print(f"Parsing {len(targets)} documents -> {out_dir}")

    n_ok = 0
    n_acct_fail = 0
    with get_conn() as conn:
        for k, (symbol, fmt) in enumerate(targets, 1):
            raw_rows = fetch_rows(conn, symbol)
            try:
                result = parse_document(symbol, fmt, raw_rows)
            except Exception as exc:  # never crash the batch on one doc
                print(f"  ! {symbol}: {type(exc).__name__}: {exc}")
                continue
            err = _check_accounting(result, raw_rows)
            if err:
                n_acct_fail += 1
                result.setdefault("issues", []).append(
                    {"position": -1, "problem": "accounting", "text_head": err})
                print(f"  ! {symbol}: ACCOUNTING {err}")
            out_path = out_dir / f"{sanitize_symbol(symbol)}.json"
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                encoding="utf-8")
            n_ok += 1
            if k % 25 == 0:
                print(f"  parsed {k}/{len(targets)}")

    print(f"\nDone: {n_ok} written, {n_acct_fail} accounting failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
