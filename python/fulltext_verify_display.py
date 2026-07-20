"""Display-coverage gate: how much of a parsed document the website can EVER show.

The mandates.un.org paragraph view (ParagraphsSection.tsx +
src/lib/data/paragraphs.ts) is NOT a full-text reader. Deriving what it can
render from the actual code:

  * ``src/lib/data/paragraphs.ts`` drops masthead frontmatter at the query
    (``NOT (type='frontmatter' AND subtype='masthead')``) and passes every
    other element to the client unchanged.
  * ``ParagraphsSection`` hard-codes ``paragraphFilter='operative'`` (a
    ``useState('operative')`` with no setter ever called), so its main list
    shows ONLY ``type==='heading'`` (always rendered) and
    ``paragraph_type==='operative'`` clauses.
  * A separate collapsible block renders ``paragraph_type==='preambular'``
    non-heading elements behind a Show/Hide toggle (default hidden, but
    reachable).
  * EVERYTHING ELSE — every ``paragraph_type IS NULL`` non-heading element
    (annex bodies, statement bodies, un-labelled 'Action N.' lines), votes,
    tables, signatures, footnotes, non-masthead frontmatter — is never shown
    under any UI state.

So the set the UI can EVER surface is exactly:

    type = 'heading'  OR  paragraph_type IN ('operative','preambular')

This gate measures, per document over ``digitallibrary.document_paragraphs``:

  * total_tokens   — word tokens across all elements EXCEPT masthead-subtype
                     frontmatter and 'divider' (the two pieces of pure
                     boilerplate the view discards outright);
  * visible_tokens — word tokens the UI can ever show (the set above);
  * visible_pct    — visible/total;
  * a main-vs-annex breakdown of the same.

Documents below --threshold (default 60%) are flagged: their substance lives in
elements the website silently hides. This is exactly the class that made
A/RES/69/313 (Addis Ababa), A/RES/73/195 and A/RES/70/1 (2030 Agenda) render as
near-nothing — their operative content sits in an annexed instrument with
``paragraph_type=NULL``.

Output: a ranked report (worst first) to stdout and to
<archive>/audit/display_coverage.tsv. Read-only. Exit code is 1 if any
audit-set document is flagged (so it can gate CI on the docs that matter),
else 0.

Usage:
    uv run python python/fulltext_verify_display.py
    uv run python python/fulltext_verify_display.py --audit-set <path>
    uv run python python/fulltext_verify_display.py --threshold 70
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
OUTPUT_TSV = AUDIT_DIR / "display_coverage.tsv"


def _with_ssl(url: str) -> str:
    if "sslrootcert" in url:
        return url
    for p in SSL_CERTS:
        if os.path.exists(p):
            return url + ("&" if "?" in url else "?") + "sslrootcert=" + p
    return url


def get_conn() -> psycopg.Connection:
    """Connection that can read digitallibrary (falls back to mandates .env)."""
    last = None
    for path in (WORKTREE_ENV, MANDATES_ENV):
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
            return conn
        except Exception as e:  # noqa: BLE001
            last = e
            try:
                conn.close()
            except Exception:
                pass
            continue
    raise RuntimeError(f"No usable DATABASE_URL for digitallibrary: {last}")


def load_audit_set(path: str | None) -> tuple[set[str], dict[str, int]] | None:
    """Return (symbols, citations-by-symbol) or None when no audit set is used."""
    p = Path(path) if path else DEFAULT_AUDIT_SET
    if path is None and not p.exists():
        return None
    data = json.loads(Path(p).read_text())
    syms = {e["symbol"] for e in data}
    cits = {e["symbol"]: e.get("citations", 0) for e in data}
    return syms, cits


# Word-token count of an element's text, in SQL. Guards empty/whitespace text.
_TOK = (
    "case when btrim(text) = '' then 0 "
    "else array_length(regexp_split_to_array(btrim(text), '\\s+'), 1) end"
)
# A row counts toward the denominator unless it is pure boilerplate.
_IN_TOTAL = "not (type = 'frontmatter' and subtype = 'masthead') and type <> 'divider'"
# A row is visible iff the UI can ever surface it.
_VISIBLE = "(type = 'heading' or paragraph_type in ('operative','preambular'))"
_IS_ANNEX = "section in ('annex','appendix')"


def compute(conn, restrict: set[str] | None) -> list[dict]:
    cur = conn.cursor()
    where = "where lang = 'en'"
    params: list = []
    if restrict is not None:
        where += " and symbol_normalized = any(%s)"
        params.append(list(restrict))
    cur.execute(
        f"""
        select
          symbol_normalized,
          sum(case when {_IN_TOTAL} then {_TOK} else 0 end)                               as total,
          sum(case when {_IN_TOTAL} and {_VISIBLE} then {_TOK} else 0 end)                as visible,
          sum(case when {_IN_TOTAL} and not {_IS_ANNEX} then {_TOK} else 0 end)           as main_total,
          sum(case when {_IN_TOTAL} and not {_IS_ANNEX} and {_VISIBLE} then {_TOK} else 0 end) as main_visible,
          sum(case when {_IN_TOTAL} and {_IS_ANNEX} then {_TOK} else 0 end)               as annex_total,
          sum(case when {_IN_TOTAL} and {_IS_ANNEX} and {_VISIBLE} then {_TOK} else 0 end)    as annex_visible
        from digitallibrary.document_paragraphs
        {where}
        group by symbol_normalized
        """,
        params,
    )
    rows = []
    for (sym, total, vis, mt, mv, at, av) in cur.fetchall():
        total = total or 0
        vis = vis or 0
        rows.append(
            {
                "symbol": sym,
                "total": total,
                "visible": vis,
                "pct": (100.0 * vis / total) if total else 100.0,
                "main_total": mt or 0,
                "main_visible": mv or 0,
                "annex_total": at or 0,
                "annex_visible": av or 0,
            }
        )
    return rows


def pctstr(v: float) -> str:
    return f"{v:5.1f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threshold", type=float, default=60.0, help="flag visible_pct below this")
    ap.add_argument(
        "--audit-set",
        nargs="?",
        const=str(DEFAULT_AUDIT_SET),
        default=None,
        help="restrict to symbols in this audit_set.json (default path if bare)",
    )
    ap.add_argument("--top", type=int, default=20, help="rows to print to stdout")
    ap.add_argument("--output", default=str(OUTPUT_TSV))
    args = ap.parse_args()

    # An explicit --audit-set restricts the scan; otherwise scan the whole
    # corpus but still HIGHLIGHT audit-set docs if audit_set.json exists.
    aset = load_audit_set(args.audit_set)
    restrict = aset[0] if (args.audit_set is not None and aset) else None
    highlight = aset[0] if aset else set()
    cits = aset[1] if aset else {}

    conn = get_conn()
    rows = compute(conn, restrict)
    conn.close()

    rows.sort(key=lambda r: (r["pct"], -r["total"]))
    flagged = [r for r in rows if r["pct"] < args.threshold]
    flagged_audit = [r for r in flagged if r["symbol"] in highlight]

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        fh.write(
            "symbol\tvisible_pct\tvisible_tokens\ttotal_tokens\t"
            "main_visible\tmain_total\tannex_visible\tannex_total\t"
            "flagged\tin_audit_set\tcitations\n"
        )
        for r in rows:
            fh.write(
                f"{r['symbol']}\t{r['pct']:.1f}\t{r['visible']}\t{r['total']}\t"
                f"{r['main_visible']}\t{r['main_total']}\t"
                f"{r['annex_visible']}\t{r['annex_total']}\t"
                f"{'Y' if r['pct'] < args.threshold else ''}\t"
                f"{'Y' if r['symbol'] in highlight else ''}\t"
                f"{cits.get(r['symbol'], '')}\n"
            )

    scope = "audit set" if restrict is not None else "full corpus"
    print(f"DISPLAY-COVERAGE GATE  ({scope}: {len(rows)} docs)  ->  {out}")
    print(
        f"visibility rule: total = all except masthead-frontmatter/divider; "
        f"visible = type='heading' OR paragraph_type IN ('operative','preambular')"
    )
    print(f"flagged below {args.threshold:.0f}%: {len(flagged)} docs"
          + (f"  ({len(flagged_audit)} in audit set)" if highlight else ""))
    print()
    header = (
        f"{'visible%':>8}  {'vis':>6}/{'total':<6}  "
        f"{'main%':>6}  {'annex%':>6}  {'cit':>4}  symbol"
    )
    print(header)
    print("-" * len(header))
    for r in rows[: args.top]:
        mainp = (100.0 * r["main_visible"] / r["main_total"]) if r["main_total"] else 100.0
        annexp = (100.0 * r["annex_visible"] / r["annex_total"]) if r["annex_total"] else float("nan")
        star = "*" if r["symbol"] in highlight else " "
        annex_s = "   -  " if r["annex_total"] == 0 else pctstr(annexp)
        print(
            f"{pctstr(r['pct']):>8}  {r['visible']:>6}/{r['total']:<6}  "
            f"{pctstr(mainp):>6}  {annex_s:>6}  {cits.get(r['symbol'], '') or '':>4}  "
            f"{star}{r['symbol']}"
        )

    if highlight:
        print("\nAudit-set docs, worst first:")
        for r in [x for x in rows if x["symbol"] in highlight][: args.top]:
            flag = "FLAG" if r["pct"] < args.threshold else "ok"
            print(f"  {pctstr(r['pct']):>6}%  {flag:>4}  {r['symbol']}  "
                  f"(cit {cits.get(r['symbol'], 0)})")

    # Nonzero exit if any audit-set doc is flagged (gate the docs that matter).
    return 1 if flagged_audit else 0


if __name__ == "__main__":
    sys.exit(main())
