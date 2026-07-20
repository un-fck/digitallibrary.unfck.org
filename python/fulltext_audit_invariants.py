"""Cheap structural tripwires over the semantic full-text layer.

Runs a handful of deterministic, corpus-scale checks over
``digitallibrary.document_paragraphs`` (plus the legacy ``mandates.paragraphs``
for a cross-corpus check) and emits one row per finding on a shared TSV schema:

    symbol <tab> check <tab> severity <tab> detail

so a future table-of-contents comparison tool can append its own
``toc-mismatch-count`` rows to the same file (check (e), the merge hook).

The checks are motivated by real defects found by hand in the most-cited
documents that every earlier gate (text-preservation, accounting) passed —
because those gates prove *words survive*, not that the *structure the website
renders on* is intact.

Checks:

  a. bare-section-heading — a ``type='heading'`` element whose text is only a
     roman/letter/number marker ('I', 'II.', 'A.') with no title, especially
     when the next element is a short plain paragraph that reads like the title
     that should have been part of the heading. (A/RES/79/226: heading 'I' then
     a plain 'General guidelines' paragraph.)

  b. null-heavy-doc — >50% of the non-boilerplate body tokens carry
     ``paragraph_type IS NULL``. A resolution whose substance is an annexed
     instrument labels almost nothing operative/preambular, so the website (which
     shows operative by default, preambular by toggle, headings always, and hides
     everything else) renders next to nothing. (A/RES/69/313, A/RES/73/195.)

  c. annex-invisible — an annex with >=10 enumerated (numbered/lettered) elements
     but zero operative/preambular labels: a whole programme of action the UI
     cannot surface. (A/RES/69/313's Addis Ababa Action Agenda.)

  d. old-heading-missing — for documents that also exist in the legacy
     ``mandates.paragraphs`` corpus, legacy headings whose normalized text has no
     fuzzy match (containment or >0.8 similarity) among the NEW headings/titles of
     the same document. The legacy corpus is NOT ground truth, so these are
     framed as REVIEW TRIGGERS, not failures — but they reliably surface
     structure the new parse dropped or demoted: PFTF 'Action N.' headings
     demoted to plain paragraphs (A/RES/79/1), SDG 'Goal N.' subheadings not
     captured (A/RES/70/1), whole section titles missing (A/RES/72/279,
     A/RES/79/226).

  e. (merge hook) — no computation; the TSV schema above is the contract a TOC
     tool joins on.

Ranked stdout summary: documents by number of DISTINCT failing checks,
citation-weighted when an audit_set.json is present. Output TSV:
<archive>/audit/invariants.tsv. Read-only.

Usage:
    uv run python python/fulltext_audit_invariants.py --audit-set   # audit set
    uv run python python/fulltext_audit_invariants.py --all         # full corpus
    uv run python python/fulltext_audit_invariants.py --audit-set <path>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import psycopg
from dotenv import dotenv_values

WORKTREE_ENV = "/Users/david/UN/digitallibrary.unfck.org/.claude/worktrees/fulltexts/.env"
MANDATES_ENV = "/Users/david/UN/mandates/.env"
SSL_CERTS = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]

DEFAULT_ARCHIVE_ROOT = "/Volumes/SSDAStorage/digitallibrary-fulltexts"
ARCHIVE_ROOT = Path(os.getenv("FULLTEXT_ARCHIVE_ROOT", DEFAULT_ARCHIVE_ROOT))
AUDIT_DIR = ARCHIVE_ROOT / "audit"
DEFAULT_AUDIT_SET = AUDIT_DIR / "audit_set.json"
OUTPUT_TSV = AUDIT_DIR / "invariants.tsv"

# ---------------------------------------------------------------------------
# Connection (reads both digitallibrary and mandates schemas; check (d) needs
# the legacy corpus, so a both-schema URL is preferred).
# ---------------------------------------------------------------------------


def _with_ssl(url: str) -> str:
    if "sslrootcert" in url:
        return url
    for p in SSL_CERTS:
        if os.path.exists(p):
            return url + ("&" if "?" in url else "?") + "sslrootcert=" + p
    return url


def get_conn() -> tuple[psycopg.Connection, bool]:
    """Return (conn, has_legacy). has_legacy is False if mandates is unreadable."""
    for path in (MANDATES_ENV, WORKTREE_ENV):  # mandates first: it can read both
        vals = dotenv_values(path)
        u = vals.get("DATABASE_URL")
        if not u:
            continue
        u = u.replace(":6432/", ":5432/")
        try:
            conn = psycopg.connect(_with_ssl(u))
            cur = conn.cursor()
            cur.execute("select 1 from digitallibrary.document_paragraphs limit 1")
            cur.fetchone()
            has_legacy = True
            try:
                cur.execute("select 1 from mandates.paragraphs limit 1")
                cur.fetchone()
            except Exception:
                conn.rollback()
                has_legacy = False
            return conn, has_legacy
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            continue
    raise RuntimeError("No usable DATABASE_URL for digitallibrary")


# ---------------------------------------------------------------------------
# Normalization / helpers
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def norm_symbol(s: str) -> str:
    return _WS.sub("", (s or "").upper()).strip()


def norm_text(s: str) -> str:
    """lowercase, strip punctuation, collapse whitespace — for fuzzy matching."""
    t = (s or "").replace("\xa0", " ").lower()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def ntokens(s: str) -> int:
    t = (s or "").strip()
    return len(t.split()) if t else 0


# A heading whose ENTIRE text is just an enumerator marker (roman/letter/number).
_BARE_MARKER = re.compile(r"^\(?([IVXLCDM]{1,7}|[A-Za-z]|\d{1,3})\)?\.?$")
# An enumerator prefix as printed ('1.', '(a)', '(iv)', '12', 'B.').
_ENUM_PREFIX = re.compile(
    r"^\(?(\d{1,3}|[a-z]{1,4}|[ivxlcdm]{1,7})\)?\.?$", re.IGNORECASE
)

# Non-boilerplate body element types for the null-heavy check.
_BODY_TYPES = {"title", "opening", "heading", "paragraph"}


def looks_like_title(text: str) -> bool:
    """A short, non-sentence line that reads like a dropped section title."""
    t = (text or "").strip()
    if not t or ntokens(t) > 12:
        return False
    # Titles rarely end in sentence punctuation and are not lowercase-led clauses.
    if t[-1] in ".:;," and not t.endswith(("...",)):
        return False
    return t[:1].isupper() or t[:1].isdigit()


# ---------------------------------------------------------------------------
# Per-document checks over the NEW corpus
# ---------------------------------------------------------------------------


def check_new_doc(sym: str, els: list[dict]) -> list[tuple[str, str, str, str]]:
    """Run checks a, b, c over one document's ordered elements."""
    out: list[tuple[str, str, str, str]] = []

    # (a) bare-section-heading
    for i, e in enumerate(els):
        if e["type"] != "heading":
            continue
        if e.get("subtype") in ("subres",):  # omnibus A/B/... letter markers
            continue
        txt = (e["text"] or "").strip()
        if not txt or not _BARE_MARKER.match(txt):
            continue
        # find the next non-empty element
        nxt = None
        for j in range(i + 1, len(els)):
            if (els[j]["text"] or "").strip():
                nxt = els[j]
                break
        if nxt is not None and nxt["type"] == "paragraph" and looks_like_title(nxt["text"]):
            out.append((
                sym, "bare-section-heading", "high",
                f"heading '{txt}' has no title; next element is a title-like "
                f"paragraph: '{(nxt['text'] or '')[:80]}'",
            ))
        else:
            out.append((
                sym, "bare-section-heading", "medium",
                f"heading text is only the marker '{txt}' (no title text)",
            ))

    # (b) null-heavy-doc — fraction of non-boilerplate body tokens that are null
    body_tok = 0
    null_tok = 0
    for e in els:
        if e["type"] not in _BODY_TYPES:
            continue
        t = ntokens(e["text"])
        body_tok += t
        if e["paragraph_type"] is None:
            null_tok += t
    if body_tok >= 50:
        frac = null_tok / body_tok
        if frac > 0.50:
            sev = "high" if frac > 0.80 else "medium"
            out.append((
                sym, "null-heavy-doc", sev,
                f"{frac*100:.0f}% of non-boilerplate body tokens are "
                f"paragraph_type=NULL ({null_tok}/{body_tok}) — substance likely "
                f"in an unlabelled annex/instrument",
            ))

    # (c) annex-invisible — per annex group (by annex_index; NULL = one group)
    annex_groups: dict[object, list[dict]] = defaultdict(list)
    for e in els:
        if e["section"] in ("annex", "appendix"):
            annex_groups[e["annex_index"]].append(e)
    for ai, grp in annex_groups.items():
        enum_n = sum(
            1 for e in grp
            if e["prefix"] and _ENUM_PREFIX.match(e["prefix"].strip())
        )
        labelled = sum(
            1 for e in grp if e["paragraph_type"] in ("operative", "preambular")
        )
        if enum_n >= 10 and labelled == 0:
            label = f"annex#{ai}" if ai is not None else "annex"
            out.append((
                sym, "annex-invisible", "high",
                f"{label} has {enum_n} enumerated elements but 0 "
                f"operative/preambular labels — invisible to the operative view",
            ))
    return out


# ---------------------------------------------------------------------------
# Check (d): old-heading-missing (cross-corpus)
# ---------------------------------------------------------------------------


def check_old_headings(
    sym: str, old_headings: list[str], new_targets: list[str]
) -> list[tuple[str, str, str, str]]:
    new_norm = [norm_text(t) for t in new_targets]
    new_norm = [t for t in new_norm if t]
    missing: list[str] = []
    for oh in old_headings:
        on = norm_text(oh)
        if len(on) < 3:
            continue
        matched = False
        for nn in new_norm:
            # containment (guard trivial short matches)
            shorter, longer = (on, nn) if len(on) <= len(nn) else (nn, on)
            if len(shorter) >= 5 and shorter in longer:
                matched = True
                break
            if SequenceMatcher(None, on, nn).ratio() > 0.8:
                matched = True
                break
        if not matched:
            missing.append(oh.strip())
    if not missing:
        return []
    sample = "; ".join(m[:60] for m in missing[:8])
    more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
    return [(
        sym, "old-heading-missing", "medium",
        f"{len(missing)} legacy heading(s) with no fuzzy match among new "
        f"headings/titles [review trigger]: {sample}{more}",
    )]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_audit_set(arg: str | None) -> tuple[set[str], dict[str, int]] | None:
    if arg is None:
        return None
    p = Path(arg)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return {e["symbol"] for e in data}, {e["symbol"]: e.get("citations", 0) for e in data}


def stream_new_docs(conn, restrict: set[str] | None):
    """Yield (symbol, [element dicts]) grouped by symbol, streamed in order."""
    where = "where lang = 'en'"
    params: list = []
    if restrict is not None:
        where += " and symbol_normalized = any(%s)"
        params.append(list(restrict))
    sql = f"""
        select symbol_normalized, position, type, subtype, section, annex_index,
               paragraph_type, heading_level, prefix, text
        from digitallibrary.document_paragraphs
        {where}
        order by symbol_normalized, position
    """
    # server-side cursor to bound memory on the full corpus
    with conn.cursor(name="inv_stream") as cur:
        cur.itersize = 20000
        cur.execute(sql, params)
        cur_sym = None
        buf: list[dict] = []
        for (s, pos, typ, sub, sec, ai, pt, hl, pfx, txt) in cur:
            if s != cur_sym:
                if cur_sym is not None:
                    yield cur_sym, buf
                cur_sym = s
                buf = []
            buf.append({
                "position": pos, "type": typ, "subtype": sub, "section": sec,
                "annex_index": ai, "paragraph_type": pt, "heading_level": hl,
                "prefix": pfx, "text": txt,
            })
        if cur_sym is not None:
            yield cur_sym, buf


def load_new_targets(conn, restrict: set[str] | None) -> dict[str, list[str]]:
    """symbol_normalized -> list of new heading/title texts."""
    where = "where lang = 'en' and type in ('heading','title')"
    params: list = []
    if restrict is not None:
        where += " and symbol_normalized = any(%s)"
        params.append(list(restrict))
    cur = conn.cursor()
    cur.execute(
        f"select symbol_normalized, text from digitallibrary.document_paragraphs {where}",
        params,
    )
    out: dict[str, list[str]] = defaultdict(list)
    for s, t in cur.fetchall():
        out[s].append(t or "")
    return out


def load_old_headings(conn, restrict: set[str] | None) -> dict[str, list[str]]:
    """normalized symbol -> list of legacy heading texts (mandates.paragraphs)."""
    cur = conn.cursor()
    cur.execute(
        "select document_symbol, text from mandates.paragraphs where type = 'heading'"
    )
    out: dict[str, list[str]] = defaultdict(list)
    for s, t in cur.fetchall():
        n = norm_symbol(s)
        if restrict is not None and n not in restrict:
            continue
        out[n].append(t or "")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--audit-set",
        nargs="?",
        const=str(DEFAULT_AUDIT_SET),
        default=None,
        help="restrict to symbols in this audit_set.json (default path if bare)",
    )
    ap.add_argument("--all", action="store_true", help="scan the whole corpus")
    ap.add_argument("--output", default=str(OUTPUT_TSV))
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    aset = load_audit_set(args.audit_set if args.audit_set else str(DEFAULT_AUDIT_SET))
    cits = aset[1] if aset else {}
    # Restriction: explicit --all overrides; else restrict to audit set if given
    # or if audit_set.json exists on disk.
    if args.all:
        restrict = None
    elif args.audit_set is not None:
        restrict = aset[0] if aset else set()
    elif aset is not None:
        restrict = aset[0]
    else:
        restrict = None

    conn, has_legacy = get_conn()

    findings: list[tuple[str, str, str, str]] = []

    # a/b/c over the new corpus
    ndocs = 0
    for sym, els in stream_new_docs(conn, restrict):
        ndocs += 1
        findings.extend(check_new_doc(sym, els))

    # d cross-corpus (needs its own cursors; run after streaming completes)
    if has_legacy:
        new_targets = load_new_targets(conn, restrict)
        old_headings = load_old_headings(conn, restrict)
        for sym, ohs in old_headings.items():
            if sym not in new_targets:
                continue  # only compare docs present in both corpora
            findings.extend(check_old_headings(sym, ohs, new_targets[sym]))
    else:
        print("WARNING: mandates.paragraphs not readable — skipping check (d)",
              file=sys.stderr)

    conn.close()

    # Write TSV (shared schema; a TOC tool appends toc-mismatch-count rows here).
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        fh.write("symbol\tcheck\tseverity\tdetail\n")
        for row in sorted(findings, key=lambda r: (r[0], r[1])):
            fh.write("\t".join(row) + "\n")

    # -------- ranked summary --------
    per_doc_checks: dict[str, set[str]] = defaultdict(set)
    per_check_counts: dict[str, int] = defaultdict(int)
    for sym, chk, _sev, _detail in findings:
        per_doc_checks[sym].add(chk)
        per_check_counts[chk] += 1

    scope = "full corpus" if restrict is None else "audit set"
    print(f"STRUCTURAL INVARIANTS  ({scope}: {ndocs} docs scanned)  ->  {out}")
    print(f"total findings: {len(findings)} across {len(per_doc_checks)} docs")
    print("by check: " + ", ".join(
        f"{k}={per_check_counts[k]}" for k in sorted(per_check_counts)
    ))
    if not has_legacy:
        print("  (check (d) skipped: legacy corpus unreadable)")
    print()

    ranked = sorted(
        per_doc_checks.items(),
        key=lambda kv: (-len(kv[1]), -cits.get(kv[0], 0), kv[0]),
    )
    print(f"{'#chk':>4}  {'cit':>4}  {'checks':<40}  symbol")
    print("-" * 78)
    for sym, chks in ranked[: args.top]:
        print(
            f"{len(chks):>4}  {cits.get(sym, '') or '':>4}  "
            f"{','.join(sorted(chks)):<40}  {sym}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
