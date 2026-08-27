"""Build the standing importance-weighted audit set for the full-text pipeline.

Motivation: the worst full-text-display defects hide in the *most-cited*
documents (an annexed instrument that renders as nothing, a dropped section
title). A blanket corpus gate drowns those in noise, so we keep a small standing
set of the documents that matter most — the resolutions/decisions the UN
programme budget actually cites — and hold *those* to a high bar in the other
two tools (``fulltext_verify_display.py``, ``fulltext_audit_invariants.py``),
which accept ``--audit-set PATH`` to restrict to it.

Importance = citation count. ``ppb2026.source_document_citations`` records one
row per (budget document -> cited mandate document); the cited symbol is
``ppb_full_document_symbol``. We count citations per cited symbol, keep only the
symbols that actually have parsed full text (join to the
``digitallibrary.document_files`` ledger, ``status='parsed'`` == present in
``document_paragraphs``), and take the top --top N. A --extra-file list of
symbols (one per line) is UNIONed in so hand-picked review cases always ride
along even if they are not highly cited.

Output: JSON to <archive>/audit/audit_set.json, a list of
``{symbol, symbol_display, citations, rank, has_fulltext}`` (``symbol`` is the
whitespace-stripped upper-cased normalized form other tools join on), plus a
printed table. Read-only.

The mandates/ppb2026 schemas are not readable by the worktree's
digitallibrary_rw role, so — like fulltext_verbs_eval.py — we prefer a
DATABASE_URL that can read both schemas, falling back to the sibling
mandates-repo .env.

Usage:
    uv run python python/fulltext_audit_set.py                 # top 100
    uv run python python/fulltext_audit_set.py --top 150
    uv run python python/fulltext_audit_set.py --extra-file review.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
OUTPUT_PATH = AUDIT_DIR / "audit_set.json"


def _with_ssl(url: str) -> str:
    if "sslrootcert" in url:
        return url
    for p in SSL_CERTS:
        if os.path.exists(p):
            return url + ("&" if "?" in url else "?") + "sslrootcert=" + p
    return url


def get_conn() -> psycopg.Connection:
    """Open a connection that can read BOTH digitallibrary and ppb2026 schemas."""
    for path in (WORKTREE_ENV, MANDATES_ENV):
        vals = dotenv_values(path)
        u = vals.get("DATABASE_URL")
        if not u:
            continue
        u = u.replace(":6432/", ":5432/")  # direct port, no pooler
        try:
            conn = psycopg.connect(_with_ssl(u))
            cur = conn.cursor()
            cur.execute("select 1 from ppb2026.source_document_citations limit 1")
            cur.fetchone()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            continue
    raise RuntimeError(
        "No DATABASE_URL can read both digitallibrary and ppb2026 schemas"
    )


def norm_symbol(s: str) -> str:
    """App-wide normalized join key: upper-case, strip all whitespace."""
    return re.sub(r"\s+", "", (s or "").upper()).strip()


def build(top: int, extra_file: str | None) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()

    # Citations per cited symbol (ppb_full_document_symbol is the cited document).
    cur.execute(
        """
        select ppb_full_document_symbol, count(*) as c
        from ppb2026.source_document_citations
        where ppb_full_document_symbol is not null
          and btrim(ppb_full_document_symbol) <> ''
        group by ppb_full_document_symbol
        """
    )
    # Collapse to normalized key, summing citations across display variants and
    # keeping the highest-frequency display form as the label.
    by_norm: dict[str, dict] = {}
    for disp, c in cur.fetchall():
        n = norm_symbol(disp)
        if not n:
            continue
        e = by_norm.setdefault(n, {"symbol": n, "citations": 0, "_forms": {}})
        e["citations"] += c
        e["_forms"][disp] = e["_forms"].get(disp, 0) + c

    # Which normalized symbols have parsed full text (ledger status='parsed').
    cur.execute(
        "select distinct symbol_normalized from digitallibrary.document_files "
        "where status = 'parsed'"
    )
    have_fulltext = {r[0] for r in cur.fetchall()}
    conn.close()

    for n, e in by_norm.items():
        e["has_fulltext"] = n in have_fulltext
        e["symbol_display"] = max(e["_forms"], key=e["_forms"].get)
        del e["_forms"]

    # Rank the cited-with-fulltext symbols, keep top N.
    cited_ft = sorted(
        (e for e in by_norm.values() if e["has_fulltext"]),
        key=lambda e: (-e["citations"], e["symbol"]),
    )
    selected = {e["symbol"]: dict(e) for e in cited_ft[:top]}

    # UNION the extra-file symbols (always included, even if 0 citations / no
    # fulltext — a caller asking to review a specific symbol gets it).
    extras: list[str] = []
    if extra_file:
        for line in Path(extra_file).read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            n = norm_symbol(s)
            extras.append(n)
            if n not in selected:
                base = by_norm.get(
                    n,
                    {
                        "symbol": n,
                        "symbol_display": s,
                        "citations": 0,
                        "has_fulltext": n in have_fulltext,
                    },
                )
                selected[n] = dict(base)
            selected[n]["extra"] = True

    # Final ranking by citations desc; rank is 1-based position in the set.
    out = sorted(selected.values(), key=lambda e: (-e["citations"], e["symbol"]))
    for i, e in enumerate(out, 1):
        e["rank"] = i
        e.setdefault("extra", False)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=100, help="most-cited symbols to keep")
    ap.add_argument(
        "--extra-file",
        help="path to a newline-delimited symbol list UNIONed into the set",
    )
    ap.add_argument("--output", default=str(OUTPUT_PATH), help="output JSON path")
    args = ap.parse_args()

    rows = build(args.top, args.extra_file)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Persist a compact, stable record (drop internal helper keys).
    payload = [
        {
            "symbol": e["symbol"],
            "symbol_display": e["symbol_display"],
            "citations": e["citations"],
            "rank": e["rank"],
            "has_fulltext": e["has_fulltext"],
            "extra": e["extra"],
        }
        for e in rows
    ]
    out_path.write_text(json.dumps(payload, indent=2))

    # Printed table.
    print(f"AUDIT SET  ({len(rows)} symbols)  ->  {out_path}")
    print(f"{'rank':>4}  {'citations':>9}  {'ft':>2}  symbol")
    print("-" * 60)
    for e in rows:
        mark = "*" if e.get("extra") else " "
        ft = "Y" if e["has_fulltext"] else "n"
        print(
            f"{e['rank']:>4}  {e['citations']:>9}  {ft:>2}  "
            f"{e['symbol_display']}{'  (extra)' if e.get('extra') else ''}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
