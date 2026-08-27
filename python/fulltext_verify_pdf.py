#!/usr/bin/env python3
"""Acceptance gate for the DETERMINISTIC PDF path (Track A, pre-1994).

The twin of `fulltext_verify_text.py`, but for PDF-sourced documents. It checks
that the content words of the TARGET resolution survive from the archived PDF
into the parsed JSON — using an INDEPENDENT extractor (`pdftotext`, poppler) as
ground truth, so a bug in the pymupdf extractor cannot hide behind itself.

THE DENOMINATOR COMES FROM THE FILE, NOT FROM THE PARSE
-------------------------------------------------------
The pre-1994 PDFs are mostly EXCERPTS of "Resolutions adopted ..." supplement
pages: a file holds the END of the previous resolution, the TARGET, and the
START of the next one, plus running headers/footers, page numbers, and (in old
volumes) a FRENCH copy alongside the English. The extractor deliberately CROPS
to the target and DROPS that apparatus, so a naive "every pdftotext word must
appear in the parse" check would flood with false losses.

Until 2026-07-27 the comparison region was anchored on THE PARSE'S OWN first and
last elements. That made the denominator a function of the numerator: deleting
90% of a document shrank the region from 1,676 tokens to 135 and the score ROSE
to 100.00% (control `P-DEL90`). That is `len(x)/len(x)`.

The region is now derived from the FILE ALONE:

    region = [ the line printing the TARGET's own resolution number ,
               the next line printing a DIFFERENT resolution number )

`region_ground_truth(path, symbol)` takes the archived file and the LEDGER
symbol. It never sees the parse — the old failure mode is structurally
impossible, not merely fixed. Four region modes, all counted and reported:

  heading      the target's printed heading was found (the normal case)
  lead         no heading for the target, but a later foreign heading exists —
               the file opens mid-target, so the region is [0, that heading)
  whole        the file prints no resolution heading at all (single-doc PDF):
               the whole file is the region
  unlocatable  a FOREIGN heading opens the file and the target's own heading is
               absent — the region cannot be bounded from the file, so the
               document CANNOT be verified. This is a FAILURE, not a skip.

TWO DIRECTIONS
--------------
  loss        in-region file words missing from the parse (minus benign classes)
  fabrication parse words that appear NOWHERE in the file, and parsed elements
              whose text is largely absent from the file. Every allowance is
              enumerated below and each one is exercised by a negative control.

Usage:
    uv run python python/fulltext_verify_pdf.py
    uv run python python/fulltext_verify_pdf.py --symbols A/RES/1260(XIII)
    uv run python python/fulltext_verify_pdf.py --self-test

Exit code: 0 iff at least one document was checked and every checked document
passed. Checking zero documents is a FAILURE (a gate that ran over an empty set
must never be indistinguishable from a gate that verified the corpus).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
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
# Ground truth: pdftotext (independent of pymupdf)
# ---------------------------------------------------------------------------

def pdftotext_raw_lines(path: Path) -> list[str]:
    """EVERY non-empty line poppler emits, including the facing French column."""
    # Default mode (NOT -layout): poppler emits reading order (column-by-column,
    # matching the parser) and rejoins soft hyphens — so the token stream aligns
    # with the parse and cross-line word fragments do not create false losses.
    out = subprocess.run([PDFTOTEXT, "-enc", "UTF-8", str(path), "-"],
                         capture_output=True, text=True, timeout=180)
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def pdftotext_lines(path: Path) -> list[str]:
    """Ground truth for the LOSS side: English only."""
    return [ln for ln in pdftotext_raw_lines(path) if not _french_line(ln)]


def _flat_tokens(lines: list[str]) -> list[str]:
    toks: list[str] = []
    for ln in lines:
        toks.extend(_wordset(ln))
    return toks


# ---------------------------------------------------------------------------
# The printed-heading boundary: a property of the FILE and the SYMBOL only
# ---------------------------------------------------------------------------
#
# Two printed forms occur across the corpus:
#   modern  '42/33.'        '42/187 A.'      'S-8/1.'
#   old     '1260 (XIII).'  'Resolution 1001 (ES-I)'   '589 (VI). Headquarters'
# The roman numeral is NOT part of the key: scanned volumes OCR it as 'XW',
# 'Xlll', 'XVl' etc., so keying on it would silently lose the boundary. The
# resolution NUMBER is digits and survives OCR.

_PRINTED_NEW = re.compile(r"^\s*(S-\d{1,2}|\d{1,4})\s*/\s*(\d{1,4})\s*[A-Z]?\s*[.．]")
# The parenthesised session form must be FOLLOWED by a title (a period then a
# capital) or end the line ('Resolution 997 (ES-I)'). Without that condition a
# mid-text cross-reference — '1002 (ES-I) of 7 November 1956' — reads as a
# heading and truncates the region, which showed up as 13 'out-of-region'
# elements on a document whose in-region preservation was 100%.
_PRINTED_OLD = re.compile(
    r"^\s*(?:Resolutions?\s+)?(\d{1,4})\s*[A-Z]?\s*[\(\[][^)\]]{1,14}[\)\]]"
    r"(?:\s*[.．,]\s*[\"“'(]?[A-Z]|\s*$)")

# Tail-anchored so every family works with one rule: A/RES/42/33, A/DEC/78/401,
# A/HRC/RES/6/28, E/DEC/2015/220, S/RES/508(1982), A/RES/1000(ES-I), A/RES/S-8/1.
_SYM_NEW = re.compile(r"(?:^|/)(S-\d{1,2}|\d{1,4})/(\d{1,4})\s*[A-Z]?$")
_SYM_OLD = re.compile(r"(?:^|/)(\d{1,4})\s*[A-Z]?\s*\([^()]+\)$")


def symbol_print_key(symbol: str) -> tuple[str, str] | None:
    """The printed-heading key a document symbol should appear under, or None."""
    s = (symbol or "").upper().replace(" ", "")
    m = _SYM_NEW.search(s)
    if m:
        return ("new", f"{m.group(1)}/{m.group(2)}")
    m = _SYM_OLD.search(s)
    if m:
        return ("old", m.group(1))
    return None


def line_print_key(line: str) -> tuple[str, str] | None:
    """The printed-heading key a ground-truth line declares, or None."""
    m = _PRINTED_NEW.match(line)
    if m:
        return ("new", f"{m.group(1)}/{m.group(2)}")
    m = _PRINTED_OLD.match(line)
    if m:
        return ("old", m.group(1))
    return None


def file_region(lines: list[str], symbol: str) -> tuple[int, int, str]:
    """(start_line, end_line, mode) of the target's printed range in `lines`.

    Depends ONLY on the archived file and the ledger symbol. See the module
    docstring for the four modes.
    """
    heads = [(i, k) for i, k in ((j, line_print_key(ln)) for j, ln in enumerate(lines)) if k]
    key = symbol_print_key(symbol)
    if key is not None:
        own = [i for i, k in heads if k == key]
        if own:
            start = own[0]
            nxt = [i for i, k in heads if i > start and k != key]
            return start, (nxt[0] if nxt else len(lines)), "heading"
    if not heads:
        return 0, len(lines), "whole"
    if heads[0][0] > 0:
        # The file opens mid-document and the first printed heading belongs to
        # the NEXT resolution: everything before it is the target's tail.
        return 0, heads[0][0], "lead"
    return 0, 0, "unlocatable"


def region_ground_truth(path: Path, symbol: str) -> tuple[Counter, Counter, int, str]:
    """(in_region_words, out_region_words, n_region_tokens, mode).

    NOTE the signature: the parse is NOT an argument. The comparison region can
    therefore never be a function of the artefact being graded.
    """
    lines = pdftotext_lines(path)
    start, end, mode = file_region(lines, symbol)
    in_region = Counter(_flat_tokens(lines[start:end]))
    out_region = Counter(_flat_tokens(lines[:start])) + Counter(_flat_tokens(lines[end:]))
    return in_region, out_region, sum(in_region.values()), mode


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


def parsed_token_seq(doc: dict) -> list[str]:
    """The parse's tokens IN ORDER (needed for the dehyphenation-split allowance)."""
    seq: list[str] = []
    for e in doc.get("elements", []):
        if e.get("prefix"):
            seq.extend(_wordset(e["prefix"]))
        seq.extend(_wordset(e.get("text") or ""))
        for lst in (e.get("vote") or {}).values():
            for name in lst:
                seq.extend(_wordset(name))
    return seq


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
# The OTHER direction: text in the parse that is not in the file
# ---------------------------------------------------------------------------
#
# ENUMERATED ALLOWANCES. Each is a claim about a real, mechanical transformation
# between poppler's token stream and the parser's, and each is exercised by a
# negative control (see fulltext_negative_controls.py, P-INVENT-*):
#
#   1. bare numbers and <=2-char fragments — the same class the loss side ignores
#      (page numbers, enumerator letters, OCR crumbs).
#   2. the document's own symbol tokens — the parser stamps the symbol into the
#      title element even when the printed page abbreviates it.
#   3. DEHYPHENATION, both directions. Poppler and the parser disagree about
#      where a hyphenated or line-broken word is one token or two, in BOTH
#      directions, so both directions are allowed — and both are anchored on the
#      file, never on a vocabulary:
#        a) JOIN: the parse token equals the concatenation of a CONTIGUOUS run of
#           2..4 FILE tokens ('inter national' -> 'international').
#        b) SPLIT: the parse token is one piece of a CONTIGUOUS run of 2..4 PARSE
#           tokens whose concatenation IS a file token ('com' 'mittee' where the
#           file has 'committee'; 'secretary' 'general' where the file, after
#           hyphen stripping, has 'secretarygeneral').
#      Neither can launder an injected sentence: in (a) the pieces must be
#      adjacent in the file, in (b) the RESULT must exist in the file. The
#      self-test proves a word assembled from non-adjacent file tokens is still
#      reported.
#
# Anything else is fabrication. The aggregate `--max-invented` exists only to
# absorb poppler/pymupdf disagreement on GARBLED SCANS (a dropped drop-cap turns
# 'Considering' into 'onsidering'), which is why it is small for 'text'-class
# documents and only opens up for 'poor'-class ones; the per-element check below
# is what localises fabrication, and it has no band.

_MAX_JOIN_RUN = 4


def contiguous_joins(file_tokens: list[str]) -> set[str]:
    """Every concatenation of 2..4 CONSECUTIVE file tokens."""
    out: set[str] = set()
    n = len(file_tokens)
    for i in range(n):
        acc = file_tokens[i]
        for j in range(i + 1, min(i + _MAX_JOIN_RUN, n)):
            acc += file_tokens[j]
            out.add(acc)
    return out


def fragment_of_file(tok: str, prefixes: set[str], suffixes: set[str]) -> bool:
    """The parse token is a PREFIX or SUFFIX of a longer file token.

    Line-broken words survive extraction differently in the two engines: poppler
    rejoins 'Com-\\nmittee' into 'committee' while the pymupdf path can keep the
    pieces ('com', 'mittee'), and OCR noise in the other piece stops the
    contiguous-run rules above from matching. A fragment is anchored on a real
    file word, so it cannot admit an invented one: 'zarnovian' is a prefix or
    suffix of nothing the file contains (proved in the self-test).
    """
    return len(tok) >= 3 and (tok in prefixes or tok in suffixes)


def file_fragments(file_bag: Counter) -> tuple[set[str], set[str]]:
    """All proper prefixes and suffixes (len>=3) of the file's tokens."""
    pre: set[str] = set()
    suf: set[str] = set()
    for t in file_bag:
        if len(t) < 5:
            continue
        for k in range(3, len(t) - 1):
            pre.add(t[:k])
            suf.add(t[-k:])
    return pre, suf


def split_covered(parse_tokens: list[str], file_bag: Counter) -> set[str]:
    """Parse tokens that are a piece of a contiguous parse run whose concatenation
    is a token OF THE FILE (the parser split what poppler kept whole)."""
    out: set[str] = set()
    n = len(parse_tokens)
    for i in range(n):
        acc = parse_tokens[i]
        for j in range(i + 1, min(i + _MAX_JOIN_RUN, n)):
            acc += parse_tokens[j]
            if file_bag.get(acc, 0) > 0:
                out.update(parse_tokens[i:j + 1])
    return out


def invented_words(pw: Counter, file_bag: Counter, joins: set[str],
                   symbol_tokens: frozenset[str],
                   split_ok: set[str] | None = None,
                   fragments: tuple[set[str], set[str]] | None = None) -> Counter:
    """Parse tokens that appear nowhere in the file, minus the allowances."""
    split_ok = split_ok or set()
    pre, suf = fragments or (set(), set())
    out: Counter = Counter()
    for tok, cnt in pw.items():
        if file_bag.get(tok, 0) > 0:
            continue
        if tok.isdigit() or len(tok) <= 2:
            continue
        if tok in symbol_tokens:
            continue
        if tok in joins or tok in split_ok:
            continue
        if fragment_of_file(tok, pre, suf):
            continue
        out[tok] = cnt
    return out


def foreign_elements(doc: dict, file_bag: Counter, in_region: Counter,
                     min_tokens: int, min_present: float) -> list[tuple[str, str, float]]:
    """Parsed elements that do not belong to this document's printed range.

    Two classes, both failures, distinguished so the report says which:

      INVENTED      the element's words are largely absent from the FILE. This is
                    fabrication. It is concentrated (a whole clause), whereas
                    extractor/OCR disagreement is distributed (a token here and
                    there), so scoring per element separates the two without a
                    corpus-wide tolerance band.
      OUT-OF-REGION the words ARE in the file but not inside the target's printed
                    range: text lifted from the NEIGHBOURING resolution printed on
                    the same supplement page. A file-membership test alone cannot
                    see this (control P-FABRICATE-XDOC) because the neighbour is
                    in the same file.
    """
    out: list[tuple[str, str, float]] = []
    for e in doc.get("elements", []):
        toks = set(_wordset(e.get("text") or ""))
        toks = {t for t in toks if len(t) > 2 and not t.isdigit()}
        if len(toks) < min_tokens:
            continue
        in_file = sum(1 for t in toks if file_bag.get(t, 0) > 0) / len(toks)
        in_reg = sum(1 for t in toks if in_region.get(t, 0) > 0) / len(toks)
        if in_file < min_present:
            out.append(((e.get("text") or "")[:70], "INVENTED", in_file))
        elif in_reg < min_present:
            out.append(((e.get("text") or "")[:70], "OUT-OF-REGION", in_reg))
    return out


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def fetch_targets(limit: int | None, symbols: list[str] | None):
    """(targets, handed_off).

    Volume-split children (source_symbol IS NOT NULL) have no archive file of
    their own — their ground truth is the parent volume's printed range, which
    `fulltext_verify_volumes.py --children` gates. They are HANDED OFF, and the
    count is printed, so the three gates' coverage adds up to the ledger instead
    of disappearing into a silent SKIP.
    """
    sql = ("SELECT symbol_normalized, archive_path, error, source_symbol "
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
        rows = cur.fetchall()
    targets = [(s, a, e) for s, a, e, src in rows if src is None]
    handed = [s for s, a, e, src in rows if src is not None]
    return targets, handed


def _klass(error: str | None) -> str:
    if error and "class=" in error:
        m = re.search(r"class=(\w+)", error)
        if m:
            return m.group(1)
    return "text"


# ---------------------------------------------------------------------------
# Self-test: the instruments must be shown to move
# ---------------------------------------------------------------------------

def _self_test() -> int:
    fails: list[str] = []
    lines = [
        "Resolutions adopted without reference to a Committee",
        "1000 (ES-I). United Nations Command",
        "The General Assembly,",
        "Decides to establish a Command.",
        "1001 (ES-I). Something else entirely",
        "The General Assembly, having considered nothing.",
    ]
    s, e, mode = file_region(lines, "A/RES/1000(ES-I)")
    if (s, e, mode) != (1, 4, "heading"):
        fails.append(f"region for a printed heading: got {(s, e, mode)}, want (1, 4, 'heading')")
    s, e, mode = file_region(lines, "A/RES/1001(ES-I)")
    if (s, e, mode) != (4, 6, "heading"):
        fails.append(f"region for the last heading: got {(s, e, mode)}")
    # NEGATIVE CONTROL for the region itself: the region must NOT depend on the
    # parse. region_ground_truth takes no parse; assert the boundary is stable
    # when the notional parse shrinks (here: same call, same answer).
    s2, e2, _ = file_region(lines, "A/RES/1000(ES-I)")
    if (s2, e2) != (1, 4):
        fails.append("region is not a pure function of (file, symbol)")
    # A file that opens with a foreign heading and never prints the target's own
    # must be unlocatable, not silently whole-file.
    s, e, mode = file_region(lines[1:], "A/RES/9999(XX)")
    if mode != "unlocatable":
        fails.append(f"missing target heading must be unlocatable, got {mode}")
    # No printed headings at all -> the whole file.
    s, e, mode = file_region(["The General Assembly,", "Decides."], "A/RES/70/1")
    if (s, e, mode) != (0, 2, "whole"):
        fails.append(f"heading-less file must be whole-file, got {(s, e, mode)}")
    # Modern lettered headings terminate a region.
    s, e, mode = file_region(["42/186. Environment", "text", "42/187 A. Other"], "A/RES/42/186")
    if (s, e) != (0, 2):
        fails.append(f"lettered next heading must end the region, got {(s, e)}")
    # Every symbol family the corpus contains must yield a key.
    for sym, want in (("A/RES/42/33", ("new", "42/33")),
                      ("A/DEC/78/401", ("new", "78/401")),
                      ("A/HRC/RES/6/28", ("new", "6/28")),
                      ("E/DEC/2015/220", ("new", "2015/220")),
                      ("A/RES/S-8/1", ("new", "S-8/1")),
                      ("S/RES/508(1982)", ("old", "508")),
                      ("A/RES/1000(ES-I)", ("old", "1000"))):
        if symbol_print_key(sym) != want:
            fails.append(f"symbol_print_key({sym}) = {symbol_print_key(sym)}, want {want}")

    # Fabrication side.
    ftoks = _wordset("the general assembly decides to establish an inter national command")
    joins = contiguous_joins(ftoks)
    bag = Counter(ftoks)
    pw = words("the assembly decides to establish an international command in Zarnovia")
    inv = invented_words(pw, bag, joins, frozenset())
    if "international" in inv:
        fails.append("contiguous-run join ('inter national') was not allowed")
    if "zarnovia" not in inv:
        fails.append("an invented proper noun was not reported")
    # The join allowance must NOT excuse a word assembled from NON-adjacent tokens.
    pw2 = words("commandassembly")
    if "commandassembly" not in invented_words(pw2, bag, joins, frozenset()):
        fails.append("non-contiguous join was wrongly excused")
    # SPLIT direction: the file keeps 'international' whole, the parse breaks it.
    fb = Counter(_wordset("the assembly notes international co operation"))
    seq = _wordset("the assembly notes inter national co operation and Zarnovia")
    sc = split_covered(seq, fb)
    inv2 = invented_words(Counter(seq), fb, contiguous_joins(list(fb.elements())),
                          frozenset(), sc)
    if "inter" in inv2 or "national" in inv2:
        fails.append("dehyphenation-split pieces were not allowed")
    if "zarnovia" not in inv2:
        fails.append("an invented word survived the split allowance undetected")
    # FRAGMENT allowance, and its negative control: an invented proper noun must
    # not be excusable as a fragment of anything the file contains.
    fb2 = Counter(_wordset("the committee of the general assembly on peacekeeping"))
    frags = file_fragments(fb2)
    inv3 = invented_words(words("com mittee sembly zarnovian brovania"), fb2,
                          set(), frozenset(), set(), frags)
    if "com" in inv3 or "mittee" in inv3 or "sembly" in inv3:
        fails.append("line-break fragments of real file words were not allowed")
    if "zarnovian" not in inv3 or "brovania" not in inv3:
        fails.append("the fragment allowance excused an invented word")

    for m in fails:
        print("  FAIL:", m)
    print("self-test:", "FAILED" if fails else "passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Acceptance gate: PDF->parsed text preservation.")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--parsed-dir", type=Path, default=PARSED_DIR)
    ap.add_argument("--max-loss", type=int, default=5,
                    help="small absolute in-region genuine-loss tolerance (default 5) "
                         "— covers a handful of OCR letter-substitutions in a small doc. "
                         "A document must satisfy BOTH this and --min-preserved.")
    ap.add_argument("--min-preserved", type=float, default=95.0,
                    help="in-region preservation %% a 'text' doc must reach to pass (default 95)")
    ap.add_argument("--poor-min-preserved", type=float, default=85.0,
                    help="looser preservation %% bar for 'poor'-class OCR docs (default 85)")
    ap.add_argument("--max-invented", type=int, default=0,
                    help="in-parse token TYPES absent from the file that a 'text'-class doc "
                         "may carry (default 0)")
    ap.add_argument("--poor-max-invented", type=int, default=25,
                    help="same, for 'poor'-class scans where poppler and pymupdf disagree "
                         "on garbled glyphs (default 25)")
    ap.add_argument("--foreign-min-tokens", type=int, default=8,
                    help="element size (distinct content tokens) at which the per-element "
                         "fabrication check applies (default 8)")
    ap.add_argument("--foreign-min-present", type=float, default=0.60,
                    help="fraction of an element's tokens that must exist in the source file "
                         "(default 0.60)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    targets, handed = fetch_targets(args.limit, args.symbols)
    print(f"PDF acceptance gate: {len(targets)} own-file docs "
          f"(+{len(handed)} volume-split children handed to "
          f"fulltext_verify_volumes.py --children); parsed_dir={args.parsed_dir}")

    n_pass = n_fail = 0
    tot_region = tot_genuine = tot_crop = tot_invented = 0
    modes: Counter = Counter()
    failures: list[tuple[str, str, str]] = []

    for symbol, rel, error in targets:
        klass = _klass(error)
        pj = args.parsed_dir / f"{sanitize_symbol(symbol)}.json"
        pdf = (ARCHIVE_ROOT / rel) if rel else None

        # A ledger row in status extracted/parsed whose artefacts are missing is a
        # broken invariant, not something to skip past. (The docx twin already
        # FAILs on the same condition; the two must not disagree.)
        if not pdf or not pdf.exists():
            print(f"  FAIL  {symbol:<22} [{klass}] archived PDF missing ({rel})")
            n_fail += 1
            failures.append((symbol, klass, "archived PDF missing"))
            continue
        if not pj.exists():
            print(f"  FAIL  {symbol:<22} [{klass}] parsed JSON missing ({pj.name})")
            n_fail += 1
            failures.append((symbol, klass, "parsed JSON missing"))
            continue

        try:
            doc = json.loads(pj.read_text(encoding="utf-8"))
            raw_lines = pdftotext_raw_lines(pdf)
            lines = [ln for ln in raw_lines if not _french_line(ln)]
            start, end, mode = file_region(lines, symbol)
            in_region = Counter(_flat_tokens(lines[start:end]))
            out_region = (Counter(_flat_tokens(lines[:start]))
                          + Counter(_flat_tokens(lines[end:])))
            n_region = sum(in_region.values())
            # The FABRICATION side asks "does this text exist in the source file
            # at all", so it uses the UNFILTERED stream: French text leaking into
            # an English parse is a misattribution defect, not an invention, and
            # must not be reported as one.
            file_tokens = _flat_tokens(raw_lines)
            file_bag = Counter(file_tokens)
            pw = parsed_words(doc)
        except Exception as exc:
            print(f"  FAIL  {symbol:<22} [{klass}] {type(exc).__name__}: {exc}")
            n_fail += 1
            failures.append((symbol, klass, f"{type(exc).__name__}: {exc}"))
            continue

        modes[mode] += 1
        if mode == "unlocatable":
            print(f"  FAIL  {symbol:<22} [{klass}] region not determinable from the file "
                  f"(no printed heading for this symbol) — cannot verify")
            n_fail += 1
            failures.append((symbol, klass, "region unlocatable"))
            continue

        symtok = frozenset(_TOKEN.findall(symbol.lower().translate(_STRIP)))
        lost, crop_loss = genuine_loss(in_region, pw, out_region, symtok)
        n_lost = sum(lost.values())
        joins = contiguous_joins(file_tokens)
        inv = invented_words(pw, file_bag, joins, symtok,
                             split_covered(parsed_token_seq(doc), file_bag),
                             file_fragments(file_bag))
        n_inv = len(inv)
        foreign = foreign_elements(doc, file_bag, in_region, args.foreign_min_tokens,
                                   args.foreign_min_present)

        tot_region += n_region
        tot_genuine += n_lost
        tot_crop += crop_loss
        tot_invented += n_inv

        # An empty region is not a perfect score; it is an unverifiable document.
        if n_region == 0:
            print(f"  FAIL  {symbol:<22} [{klass}] region is empty ({mode}) — nothing to verify")
            n_fail += 1
            failures.append((symbol, klass, "empty region"))
            continue

        preserved = 100.0 * (n_region - n_lost) / n_region
        bar = args.poor_min_preserved if klass == "poor" else args.min_preserved
        inv_bar = args.poor_max_invented if klass == "poor" else args.max_invented
        # AND, not OR: the old `n_lost <= max_loss or preserved >= bar` let any
        # document losing <=5 tokens pass regardless of size (control P-BAND-5TOKENS
        # deleted 5 words, lost 24 tokens, and passed).
        reasons: list[str] = []
        if not (n_lost <= args.max_loss and preserved >= bar):
            reasons.append(f"loss {n_lost} tok, preserved {preserved:.2f}% (bar {bar})")
        if n_inv > inv_bar:
            reasons.append(f"{n_inv} invented token types (bar {inv_bar}): "
                           f"{list(sorted(inv, key=lambda t: -inv[t]))[:6]}")
        if foreign:
            reasons.append(f"{len(foreign)} element(s) outside this document: "
                           + "; ".join(f"{k} {t!r}" for t, k, _ in foreign[:2]))

        if reasons:
            n_fail += 1
            failures.append((symbol, klass, "; ".join(reasons)))
            print(f"  FAIL  {symbol:<22} [{klass}] region={n_region}({mode}) "
                  f"preserved={preserved:.2f}% lost={n_lost} crop={crop_loss} "
                  f"invented={n_inv} | " + "; ".join(reasons)[:140])
        else:
            n_pass += 1
            if args.verbose:
                print(f"  pass  {symbol:<22} [{klass}] region={n_region}({mode}) "
                      f"preserved={preserved:.2f}% lost={n_lost} crop={crop_loss} "
                      f"invented={n_inv}")

    n_checked = n_pass + n_fail
    print("\n" + "=" * 64)
    print(f"checked={n_checked}  pass={n_pass}  FAIL={n_fail}   "
          f"region modes: {dict(modes)}")
    if tot_region:
        print(f"in-region words={tot_region}  genuine lost={tot_genuine}  "
              f"crop-loss (expected, outside target)={tot_crop}  "
              f"invented token types={tot_invented}  "
              f"aggregate in-region preserved="
              f"{100.0*(tot_region-tot_genuine)/tot_region:.3f}%")
    else:
        print("in-region words=0 — no percentage is reportable over an empty denominator")
    if failures:
        print(f"\n{len(failures)} doc(s) failed:")
        for sym, kl, why in failures[:60]:
            print(f"  {sym:<22} [{kl}] {why[:150]}")
        if len(failures) > 60:
            print(f"  ... +{len(failures) - 60} more")

    if n_checked == 0:
        print("FAIL: the gate checked 0 documents. A run that verified nothing must "
              "never be indistinguishable from a run that verified the corpus.")
        return 1
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
