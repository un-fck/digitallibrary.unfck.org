#!/usr/bin/env python3
"""Metrics & accounting report for the semantic full-text parser (Track A, v1).

Runs over the parsed JSON in <ARCHIVE_ROOT>/parsed_dev/*.json together with the
raw extraction table (digitallibrary.document_paragraphs_raw) and the ledger
(digitallibrary.document_files). It:

  1. Enforces the accounting invariant per document: every raw position is
     consumed by exactly one element.positions[] or one dropped[].position.
  2. Prints per-family (RES / PRST / ...) and per-format (docx / doc / wpd)
     aggregates: docs parsed, % positions accounted, elements by type, docs with
     >=1 operative, docs with >=1 preambular (resolutions only), issue counts by
     problem, docs with annex sections, docs with text_index > 1.
  3. Lists the top-20 most common unclassified text heads.
  4. CROSS-CHECKS against the pre-existing mandates.paragraphs table for the
     overlap documents: compares per-doc operative and preambular counts
     (ours vs theirs) and flags docs differing by > 2 or > 20 %.

The full report is written to <ARCHIVE_ROOT>/parsed_dev/_metrics.txt and echoed
to stdout.

mandates.paragraphs lives in a different database (the mandates.un.org project)
than the digitallibrary tables. It is reached via a separate connection whose
DATABASE_URL is read from the mandates repo .env (override with MANDATES_ENV).
If that DB is unreachable the cross-check is skipped with a warning.

Usage:
    uv run python python/fulltext_parse_metrics.py
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from fulltext_common import ARCHIVE_ROOT, ensure_ssl_cert, get_conn

PARSED_DIR = ARCHIVE_ROOT / "parsed_dev"
REPORT_PATH = PARSED_DIR / "_metrics.txt"
MANDATES_ENV = os.getenv("MANDATES_ENV", "/Users/david/UN/mandates/.env")

DIFF_ABS = 2      # flag cross-check diffs larger than this many paragraphs ...
DIFF_PCT = 0.20   # ... or larger than this fraction.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def family(symbol: str) -> str:
    if "PRST" in symbol:
        return "PRST"
    if "/RES/" in symbol:
        return "RES"
    if "/DEC/" in symbol:
        return "DEC"
    return "OTHER"


def norm_symbol(symbol: str) -> str:
    """Match the raw symbol_normalized: upper-case, whitespace stripped."""
    return re.sub(r"\s", "", symbol).upper()


class Out:
    """Tee writer: collects lines for the report file and prints them."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, *parts: object) -> None:
        line = " ".join(str(p) for p in parts)
        self.lines.append(line)
        print(line)

    def flush(self, path: Path) -> None:
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Load parsed docs & raw counts
# ---------------------------------------------------------------------------


def load_parsed() -> list[dict]:
    docs = []
    for fp in sorted(PARSED_DIR.glob("*.json")):
        # skip the report and macOS AppleDouble sidecars ("._X.json")
        if fp.name.startswith("_") or fp.name.startswith("."):
            continue
        docs.append(json.loads(fp.read_text(encoding="utf-8")))
    return docs


def load_raw_counts() -> dict[str, int]:
    """position count per symbol_normalized in the raw table."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized, count(*) "
            "FROM digitallibrary.document_paragraphs_raw GROUP BY 1"
        )
        return {r[0]: r[1] for r in cur.fetchall()}


def doc_accounting(doc: dict, raw_count: int) -> tuple[int, int, str | None]:
    """Return (consumed, raw_count, error_or_None) for one parsed doc."""
    consumed: list[int] = []
    for el in doc["elements"]:
        consumed.extend(el["positions"])
    for d in doc.get("dropped", []):
        consumed.append(d["position"])
    cset = set(consumed)
    err = None
    if len(consumed) != len(cset):
        err = "duplicate positions"
    elif raw_count and len(cset) != raw_count:
        err = f"count mismatch consumed={len(cset)} raw={raw_count}"
    return len(cset), raw_count, err


# ---------------------------------------------------------------------------
# Cross-check against mandates.paragraphs
# ---------------------------------------------------------------------------


def get_mandates_conn() -> psycopg.Connection | None:
    env = dotenv_values(MANDATES_ENV)
    url = env.get("DATABASE_URL") or os.getenv("MANDATES_DATABASE_URL")
    if not url:
        return None
    try:
        return psycopg.connect(ensure_ssl_cert(url), autocommit=True)
    except Exception as exc:  # unreachable / wrong creds -> skip cross-check
        print(f"  (cross-check skipped: {type(exc).__name__}: {exc})")
        return None


def fetch_theirs(conn: psycopg.Connection) -> dict[str, dict[str, int]]:
    """Per normalized symbol: {'operative': n, 'preambular': n} from mandates.paragraphs."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"operative": 0, "preambular": 0})
    with conn.cursor() as cur:
        cur.execute(
            "SELECT upper(regexp_replace(document_symbol, '\\s', '', 'g')) AS s, "
            "       paragraph_type, count(*) "
            "FROM mandates.paragraphs "
            "WHERE paragraph_type IN ('operative','preambular') "
            "GROUP BY 1, 2"
        )
        for s, ptype, n in cur.fetchall():
            out[s][ptype] = n
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def group_report(out: Out, title: str, groups: dict[str, list[dict]],
                 raw_counts: dict[str, int]) -> None:
    out("")
    out(f"=== {title} ===")
    for key in sorted(groups):
        docs = groups[key]
        n = len(docs)
        type_counts: Counter = Counter()
        n_op = n_pre = n_annex = n_multitext = 0
        issue_counts: Counter = Counter()
        consumed_sum = raw_sum = 0
        acct_fail = 0
        for d in docs:
            types = Counter(e["type"] for e in d["elements"])
            type_counts.update(types)
            has_op = any(e.get("paragraph_type") == "operative" for e in d["elements"])
            has_pre = any(e.get("paragraph_type") == "preambular" for e in d["elements"])
            n_op += has_op
            n_pre += has_pre
            if any(e.get("section") in ("annex", "appendix") for e in d["elements"]):
                n_annex += 1
            if any(e.get("text_index", 1) > 1 for e in d["elements"]):
                n_multitext += 1
            for iss in d.get("issues", []):
                issue_counts[iss["problem"]] += 1
            rc = raw_counts.get(norm_symbol(d["symbol"]), 0)
            cons, rc2, err = doc_accounting(d, rc)
            consumed_sum += cons
            raw_sum += rc2
            if err:
                acct_fail += 1
        pct = (100.0 * consumed_sum / raw_sum) if raw_sum else 0.0
        out("")
        out(f"  [{key}]  docs={n}  positions_accounted={pct:.2f}%  accounting_failures={acct_fail}")
        out(f"    docs >=1 operative : {n_op}/{n}")
        out(f"    docs >=1 preambular: {n_pre}/{n}"
            + ("   (resolutions -- expected ~100%)" if key in ("RES", "docx", "doc", "wpd") else ""))
        out(f"    docs with annex/appendix section: {n_annex}")
        out(f"    docs with text_index>1 (multi-text): {n_multitext}")
        out("    elements by type: "
            + ", ".join(f"{t}={c}" for t, c in type_counts.most_common()))
        if issue_counts:
            out("    issues: " + ", ".join(f"{p}={c}" for p, c in issue_counts.most_common()))


def main() -> int:
    out = Out()
    docs = load_parsed()
    raw_counts = load_raw_counts()

    out("=" * 72)
    out("SEMANTIC FULL-TEXT PARSER -- METRICS REPORT")
    out(f"parsed docs: {len(docs)}   parser_version: "
        + (docs[0]["parser_version"] if docs else "?"))
    out("=" * 72)

    # --- global accounting -------------------------------------------------
    total_consumed = total_raw = 0
    acct_fail_docs = []
    for d in docs:
        rc = raw_counts.get(norm_symbol(d["symbol"]), 0)
        cons, rc2, err = doc_accounting(d, rc)
        total_consumed += cons
        total_raw += rc2
        if err:
            acct_fail_docs.append((d["symbol"], err))
    out("")
    out(f"GLOBAL positions accounted: {total_consumed}/{total_raw} "
        f"({100.0*total_consumed/total_raw:.3f}%)")
    out(f"GLOBAL accounting failures: {len(acct_fail_docs)}")
    for sym, err in acct_fail_docs[:20]:
        out(f"    ! {sym}: {err}")

    # --- per-family & per-format ------------------------------------------
    by_family: dict[str, list[dict]] = defaultdict(list)
    by_format: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_family[family(d["symbol"])].append(d)
        by_format[d["format"]].append(d)
    group_report(out, "PER FAMILY", by_family, raw_counts)
    group_report(out, "PER FORMAT", by_format, raw_counts)

    # --- global element-type & issue totals -------------------------------
    all_types: Counter = Counter()
    all_issues: Counter = Counter()
    dropped_reasons: Counter = Counter()
    unclassified_heads: Counter = Counter()
    for d in docs:
        all_types.update(e["type"] for e in d["elements"])
        for dr in d.get("dropped", []):
            dropped_reasons[dr["reason"]] += 1
        for iss in d.get("issues", []):
            all_issues[iss["problem"]] += 1
            if iss["problem"] == "unclassified paragraph":
                unclassified_heads[iss["text_head"]] += 1
    out("")
    out("=== GLOBAL element types ===")
    for t, c in all_types.most_common():
        out(f"    {t:<14} {c}")
    out("")
    out("=== GLOBAL dropped reasons ===")
    for r, c in dropped_reasons.most_common():
        out(f"    {r:<16} {c}")
    out("")
    out("=== GLOBAL issues by problem ===")
    if all_issues:
        for p, c in all_issues.most_common():
            out(f"    {p:<28} {c}")
    else:
        out("    (none)")

    out("")
    out("=== TOP-20 unclassified text heads ===")
    if unclassified_heads:
        for head, c in unclassified_heads.most_common(20):
            out(f"    {c:>3}  {head!r}")
    else:
        out("    (no unclassified paragraphs)")

    # --- cross-check against mandates.paragraphs --------------------------
    out("")
    out("=" * 72)
    out("CROSS-CHECK vs mandates.paragraphs (overlap docs)")
    out("=" * 72)
    conn = get_mandates_conn()
    if conn is None:
        out("  mandates DB unreachable -- cross-check skipped.")
    else:
        try:
            theirs = fetch_theirs(conn)
        finally:
            conn.close()
        ours = {}
        for d in docs:
            s = norm_symbol(d["symbol"])
            op = sum(1 for e in d["elements"] if e.get("paragraph_type") == "operative")
            pre = sum(1 for e in d["elements"] if e.get("paragraph_type") == "preambular")
            ours[s] = (op, pre, d["symbol"], d["format"])
        overlap = sorted(set(ours) & set(theirs))
        out(f"  overlap documents: {len(overlap)}")
        out(f"  {'symbol':<22}{'fmt':<6}{'op(ours/theirs)':<18}{'pre(ours/theirs)':<18}flag")
        n_flag_op = n_flag_pre = 0
        for s in overlap:
            o_op, o_pre, sym, fmt = ours[s]
            t_op = theirs[s]["operative"]
            t_pre = theirs[s]["preambular"]
            flag_op = _diff_flag(o_op, t_op)
            flag_pre = _diff_flag(o_pre, t_pre)
            n_flag_op += bool(flag_op)
            n_flag_pre += bool(flag_pre)
            if flag_op or flag_pre:
                flag = ("OP:" + flag_op if flag_op else "") + (" PRE:" + flag_pre if flag_pre else "")
                out(f"  {sym:<22}{fmt:<6}{f'{o_op}/{t_op}':<18}{f'{o_pre}/{t_pre}':<18}{flag}")
        out("")
        out(f"  docs flagged (operative diff >{DIFF_ABS} or >{int(DIFF_PCT*100)}%): {n_flag_op}")
        out(f"  docs flagged (preambular diff >{DIFF_ABS} or >{int(DIFF_PCT*100)}%): {n_flag_pre}")
        # aggregate totals
        tot_o_op = sum(ours[s][0] for s in overlap)
        tot_t_op = sum(theirs[s]["operative"] for s in overlap)
        tot_o_pre = sum(ours[s][1] for s in overlap)
        tot_t_pre = sum(theirs[s]["preambular"] for s in overlap)
        out(f"  TOTAL operative  ours={tot_o_op}  theirs={tot_t_op}")
        out(f"  TOTAL preambular ours={tot_o_pre}  theirs={tot_t_pre}")

    out("")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.flush(REPORT_PATH)
    print(f"\nReport written to {REPORT_PATH}")
    return 0


def _diff_flag(ours: int, theirs: int) -> str:
    """Return a short flag string if ours vs theirs differ materially, else ''."""
    d = abs(ours - theirs)
    if d == 0:
        return ""
    base = max(theirs, ours, 1)
    if d > DIFF_ABS or (d / base) > DIFF_PCT:
        return f"+{ours-theirs}"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
