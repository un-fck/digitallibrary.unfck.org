#!/usr/bin/env python3
"""Fetch PDF files for PRE-1994 UN resolutions/decisions/PRSTs from ODS.

Stage 1 of the DETERMINISTIC PDF path (Track A, pre-1994 corpus). The ~17.7k
documents published before 1994 have NO Word source on ODS — only PDFs, many of
them scans that carry an OCR text layer (some are pure images). This fetcher is
the PDF-shaped twin of `fulltext_fetch.py`: same ODS endpoint, same magic-byte
sniffing, same ledger, same politeness discipline — but it asks for `t=pdf` and
records `format='pdf'` rows.

It is a SEPARATE BACKFILL command, deliberately NOT part of the top-up
orchestrator (`fulltext_pipeline.py`) and NOT run alongside the Word backfill:
the two would compete for the same ODS budget. Run it only AFTER the Word
backfill has drained, at a gentle rate.

ODS empirics (pre-1994): the document comes back as a PDF (`%PDF`). A *failure*
returns an HTTP 200 text/html page (~1.3 KB), detected here by magic bytes, not
Content-Type (identical to the Word path). The `%20`-vs-`+` encoding rule is
critical: spaces in the symbol MUST be percent-encoded; a `+` redirects to an
error page that is byte-identical to "document not found".

Usage:
    # sample / priority list (one symbol per line) — the ONLY mode used while iterating
    uv run python python/fulltext_fetch_pdf.py --symbols-file sample.txt --rate 4

    # deferred bulk backfill of the whole pre-1994 PDF corpus (run AFTER the Word
    # backfill completes; expect several hours at rate 1.5-2s):
    uv run python python/fulltext_fetch_pdf.py --catalog --rate 1.8

    uv run python python/fulltext_fetch_pdf.py --symbols-file sample.txt --dry-run
    uv run python python/fulltext_fetch_pdf.py --recheck-unavailable
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests

# Reuse the Word fetcher's helpers verbatim (importing — never modifying it), so
# the two fetchers share one politeness/ledger implementation.
from fulltext_common import (
    ARCHIVE_ROOT,
    FORMAT_EXT,
    get_conn,
    read_state,
    sanitize_symbol,
    sha256_bytes,
    sniff_format,
    upsert_document_file,
    write_state,
)
from fulltext_fetch import (
    HTTP_TIMEOUT,
    JITTER_FRAC,
    MAX_RETRIES,
    ODS_URL,
    SOFT_BLOCK_SLEEPS,
    USER_AGENT,
    RunState,
    _is_conn_reset,
    dedupe_brackets,
    load_already_done,
    long_pause,
    nap,
    normalize_symbol,
    resolve_document_symbols,
    save_atomic,
)

STATE_KEY = "fulltext_fetch_pdf"

# Pre-1994 PDF corpus selection: same 8 families as the Word path, but the
# COMPLEMENT of the date window (published BEFORE 1994-01-01 or with no date).
CATALOG_REGEX = r"^(A/RES/|A/DEC/|S/RES/|S/PRST/|E/RES/|E/DEC/|A/HRC/RES/|A/HRC/PRST/)"
MAX_DATE = "1994-01-01"

# --- Politeness knobs. Default rate is gentle (this backfill is not urgent and
# must never contend with the concurrent Word fetch). ------------------------
DEFAULT_RATE = 4.0                 # seconds between requests (jittered ±30%)
REST_EVERY = 500
REST_MIN, REST_MAX = 60.0, 120.0
CIRCUIT_THRESHOLD = 8
CIRCUIT_SLEEPS = (900.0, 3600.0)   # trip #1 => 15 min, #2 => 60 min, #3 => exit
BLOCK_PAUSE = 600.0
FLUSH_EVERY = 20
PROGRESS_EVERY = 10
STATE_EVERY = 50


# ---------------------------------------------------------------------------
# HTTP (t=pdf variant of fulltext_fetch.fetch_ods)
# ---------------------------------------------------------------------------

def fetch_ods_pdf(
    session: requests.Session, document_symbol: str, run: RunState
) -> tuple[int, bytes, str]:
    """GET the ODS PDF for a symbol. Same retry/backoff discipline as the Word
    fetcher, but t=pdf. Spaces are %20-encoded (quote_via=quote), never '+'."""
    query = urllib.parse.urlencode(
        {"s": document_symbol, "l": "en", "t": "pdf"},
        quote_via=urllib.parse.quote,
    )
    url = f"{ODS_URL}?{query}"
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            run.requests += 1
            resp = session.get(url, timeout=HTTP_TIMEOUT)
            content = resp.content
            if resp.status_code in (403, 429):
                run.block_hits += 1
                last_exc = requests.RequestException(f"HTTP {resp.status_code}")
                if attempt < MAX_RETRIES - 1:
                    long_pause(BLOCK_PAUSE,
                               f"HTTP {resp.status_code} for {document_symbol} "
                               f"— backing off before retry")
                    continue
                break
            if resp.status_code >= 500:
                raise requests.RequestException(f"server {resp.status_code}")
            if resp.status_code == 200 and not content:
                raise requests.RequestException("empty body")
            return resp.status_code, content, resp.headers.get("Content-Type", "")
        except (requests.RequestException, OSError) as exc:
            last_exc = exc
            if attempt == MAX_RETRIES - 1:
                break
            if _is_conn_reset(exc):
                run.block_hits += 1
                long_pause(BLOCK_PAUSE,
                           f"connection reset for {document_symbol} "
                           f"— backing off before retry")
            else:
                wait = (2 ** attempt) * random.uniform(1 - JITTER_FRAC, 1 + JITTER_FRAC)
                print(f"  retry {attempt + 1}/{MAX_RETRIES - 1} for {document_symbol} "
                      f"after {wait:.1f}s: {exc}", file=sys.stderr)
                time.sleep(max(0.0, wait))
    raise requests.RequestException(str(last_exc) if last_exc else "unknown error")


def fetch_target_pdf(
    session: requests.Session, document_symbol: str, run: RunState
) -> tuple[str, int | None, bytes, str | None, str | None, str | None]:
    """Fetch one symbol as PDF with the short soft-block ladder (15s/45s).

    Returns (outcome, status_code, content, content_type, fmt, error) where
    outcome is 'fetched' | 'unavailable' | 'failed'. A genuine PDF is 'fetched';
    an html/unknown body after the ladder is 'unavailable'; a network/HTTP error
    after fetch_ods_pdf's own retries is 'failed'."""
    attempts = 1 + len(SOFT_BLOCK_SLEEPS)
    status_code: int | None = None
    content = b""
    content_type: str | None = None
    fmt: str | None = None
    for attempt in range(attempts):
        try:
            status_code, content, content_type = fetch_ods_pdf(session, document_symbol, run)
        except requests.RequestException as exc:
            return ("failed", None, b"", None, None, str(exc)[:500])
        fmt = sniff_format(content[:512])
        # For the PDF path we accept ONLY a real PDF: a Word file coming back here
        # would mean the doc actually has a Word source (belongs to the other path).
        if status_code == 200 and fmt == "pdf":
            return ("fetched", status_code, content, content_type, fmt, None)
        if attempt < attempts - 1:
            long_pause(SOFT_BLOCK_SLEEPS[attempt],
                       f"{document_symbol}: HTTP {status_code}/{fmt} — soft-block "
                       f"retry {attempt + 1}/{len(SOFT_BLOCK_SLEEPS)}")
    return ("unavailable", status_code, content, content_type, fmt,
            f"HTTP {status_code}, sniffed {fmt}")


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------

def targets_pre1994(conn) -> list[tuple[str, str]]:
    """[(symbol_normalized, document_symbol)] for the pre-1994 PDF corpus:
    the 8 families, English, published BEFORE 1994 (or undated), newest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol_normalized, document_symbol
            FROM (
              SELECT DISTINCT ON (symbol_normalized)
                     symbol_normalized, document_symbol, recid, date_publication
              FROM digitallibrary.documents
              WHERE deleted_at IS NULL
                AND (date_publication IS NULL OR date_publication < %s)
                AND (cardinality(languages) = 0 OR 'eng' = ANY(languages))
                AND symbol_normalized ~ %s
              ORDER BY symbol_normalized, recid DESC
            ) t
            ORDER BY date_publication DESC NULLS LAST, symbol_normalized
            """,
            [MAX_DATE, CATALOG_REGEX],
        )
        return [(row[0], row[1]) for row in cur.fetchall()]


def targets_from_file(conn, path: Path) -> list[tuple[str, str]]:
    """Read symbols (one per line) and resolve each to its canonical
    document_symbol via the catalog, preserving file order."""
    raw = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
    norms = [normalize_symbol(s) for s in raw]
    canonical = resolve_document_symbols(conn, norms)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_sym, norm in zip(raw, norms):
        if norm in seen:
            continue
        seen.add(norm)
        out.append((norm, canonical.get(norm, raw_sym)))
    return out


def targets_unavailable(conn) -> list[tuple[str, str]]:
    """Every ledger row currently format='pdf' AND status='unavailable' — the
    re-probe set for --recheck-unavailable."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized FROM digitallibrary.document_files "
            "WHERE lang = 'en' AND status = 'unavailable' AND format = 'pdf' "
            "ORDER BY symbol_normalized"
        )
        norms = [row[0] for row in cur.fetchall()]
    canonical = resolve_document_symbols(conn, norms)
    return [(n, canonical.get(n, n)) for n in norms]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch pre-1994 PDF files from ODS")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--symbols-file", type=Path,
                     help="newline-separated document symbols to fetch (priority order)")
    src.add_argument("--catalog", action="store_true",
                     help="select the whole pre-1994 PDF corpus (DEFERRED bulk backfill; "
                          "run only after the Word backfill completes)")
    src.add_argument("--recheck-unavailable", action="store_true",
                     help="re-probe every pdf ledger row currently status='unavailable'")
    p.add_argument("--limit", type=int, help="stop after N targets")
    p.add_argument("--rate", type=float, default=DEFAULT_RATE,
                   help=f"seconds between requests (default {DEFAULT_RATE}; ±30%% jitter)")
    p.add_argument("--dry-run", action="store_true", help="list targets, do not fetch")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not (args.symbols_file or args.catalog or args.recheck_unavailable):
        print("error: choose a source — --symbols-file, --catalog, or "
              "--recheck-unavailable (no default, to prevent an accidental bulk run)",
              file=sys.stderr)
        return 2

    with get_conn() as conn:
        already = load_already_done(conn)  # any status except 'failed'
        if args.recheck_unavailable:
            targets = targets_unavailable(conn)
        elif args.symbols_file:
            targets = targets_from_file(conn, args.symbols_file)
        else:
            targets = targets_pre1994(conn)
        state = read_state(conn, STATE_KEY)

    # Collapse DL bracket pseudo-symbols onto their parent (never request '[').
    targets, collapsed_brackets = dedupe_brackets(targets, already)

    if args.recheck_unavailable:
        pending = list(targets)
    else:
        pending = [(n, s) for (n, s) in targets if n not in already]
    if args.limit:
        pending = pending[: args.limit]

    mode = ("recheck-unavailable" if args.recheck_unavailable
            else "symbols-file" if args.symbols_file else "catalog")
    print(f"Mode: {mode} | format=pdf")
    print(f"Targets: {len(targets)} | already done: {len(already)} | to fetch: {len(pending)} "
          f"| brackets collapsed: {collapsed_brackets}")
    if state:
        print(f"Previous run watermark: {state}")

    if args.dry_run:
        for norm, sym in pending[:12]:
            print(f"  would fetch: {sym}  ->  original/{sanitize_symbol(norm)}.pdf")
        if len(pending) > 12:
            print(f"  ... and {len(pending) - 12} more")
        return 0

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    run = RunState()
    total = len(pending)
    ok = unavailable = failed = 0
    consecutive_block = 0
    circuit_trips = 0
    next_rest = REST_EVERY
    buffer: list[dict] = []

    def flush() -> None:
        if not buffer:
            return
        with get_conn() as conn:
            for row in buffer:
                sym_norm = row.pop("symbol_normalized")
                upsert_document_file(conn, sym_norm, "en", **row)
            conn.commit()
        buffer.clear()

    def persist_watermark(done: int) -> None:
        with get_conn() as conn:
            write_state(conn, STATE_KEY, {
                "last_index": done, "total": total,
                "ok": ok, "unavailable": unavailable, "failed": failed,
                "requests": run.requests, "mode": mode,
            })

    for i, (norm, document_symbol) in enumerate(pending):
        ods_url = f"{ODS_URL}?s={requests.utils.quote(document_symbol)}&l=en&t=pdf"
        before_blocks = run.block_hits
        outcome, status_code, content, content_type, fmt, error = fetch_target_pdf(
            session, document_symbol, run)
        block_during = run.block_hits > before_blocks

        if outcome == "fetched":
            rel_path = f"original/{sanitize_symbol(norm)}.{FORMAT_EXT['pdf']}"
            save_atomic(ARCHIVE_ROOT / rel_path, content)
            ok += 1
            buffer.append({
                "symbol_normalized": norm, "status": "fetched", "format": "pdf",
                "content_type": content_type, "size_bytes": len(content),
                "sha256": sha256_bytes(content), "ods_url": ods_url,
                "archive_path": rel_path, "error": None,
                "fetched_at": datetime.now(timezone.utc),
            })
        elif outcome == "unavailable":
            unavailable += 1
            buffer.append({
                "symbol_normalized": norm, "status": "unavailable", "format": fmt,
                "content_type": content_type, "ods_url": ods_url,
                "size_bytes": len(content), "error": error,
            })
        else:
            failed += 1
            buffer.append({
                "symbol_normalized": norm, "status": "failed",
                "ods_url": ods_url, "content_type": None, "error": error,
            })

        if outcome == "fetched" and not block_during:
            consecutive_block = 0
        else:
            consecutive_block += 1

        done = i + 1
        if len(buffer) >= FLUSH_EVERY:
            flush()
        if done % PROGRESS_EVERY == 0 or done == total:
            print(f"fetched {done}/{total} ok={ok} unavailable={unavailable} "
                  f"failed={failed} | {run.requests} reqs, {run.per_hour():.0f}/h")
        if done % STATE_EVERY == 0:
            flush()
            persist_watermark(done)

        if consecutive_block >= CIRCUIT_THRESHOLD:
            circuit_trips += 1
            flush()
            persist_watermark(done)
            if circuit_trips > len(CIRCUIT_SLEEPS):
                print(f"\n!! Circuit breaker tripped {circuit_trips}× — exiting cleanly "
                      f"(resumable).", file=sys.stderr, flush=True)
                print(f"\nStopped early. ok={ok} unavailable={unavailable} "
                      f"failed={failed} of {total} (processed {done}).")
                return 0
            long_pause(CIRCUIT_SLEEPS[circuit_trips - 1],
                       f"circuit breaker trip #{circuit_trips}: {consecutive_block} "
                       f"consecutive block/miss outcomes — assuming soft-block")
            consecutive_block = 0

        if run.requests >= next_rest and done != total:
            flush()
            long_pause(random.uniform(REST_MIN, REST_MAX),
                       f"scheduled rest after {run.requests} requests", jitter=False)
            next_rest = run.requests + REST_EVERY

        if done != total:
            nap(args.rate)

    flush()
    persist_watermark(total)
    print(f"\nDone. ok={ok} unavailable={unavailable} failed={failed} of {total} "
          f"| {run.requests} requests, {run.per_hour():.0f}/h avg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
