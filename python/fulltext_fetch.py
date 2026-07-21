#!/usr/bin/env python3
"""Fetch English Word files for UN resolutions/decisions/PRSTs from ODS.

Stage 1 of the full-text pipeline (Track A). Downloads the native English file
for each in-scope document from the ODS symbol-access endpoint, sniffs its real
format from magic bytes, archives it to the local SSD, and records a ledger row
in digitallibrary.document_files.

ODS empirics (post-1993): a document comes back as WordPerfect 5.1 (~1993-2000),
binary .doc (2000s-2010s), or real .docx (recent). A *failure* returns an HTTP
200 text/html page (~1.3 KB) — detected here by magic bytes, not Content-Type.
`t=docx` does NOT convert; it returns the same native file, so we ask for t=doc.

Politeness strategy. (Historical note: an early sample run appeared to get
soft-blocked; that turned out to be the %20-vs-'+' encoding bug below — no
rate limiting has ever been observed from ODS. The machinery is kept as a
safety net:)

  * ~3 s between requests, with ±30% random jitter (no robotic cadence).
  * A scheduled rest break of 1-2 min after every ~500 requests, regardless of
    whether anything failed.
  * An html/unknown response is recorded 'unavailable' immediately (it is
    ODS's deterministic error page; whole volumes are genuinely absent, e.g.
    E/RES 2011). The --recheck-unavailable pass is the second chance.
  * A circuit breaker: 25 consecutive block/miss outcomes ⇒ pause 5 min, then
    15 min on later trips; never exits (consecutive misses are usually genuine
    ODS absence clusters, not blocks).
  * HTTP 429/403 or a connection reset ⇒ an immediate 10-min pause before
    retrying, and it counts toward the circuit breaker.

Net effect: ~1800-2000 requests/hour; the full backfill takes several hours.

Two structural facts about the corpus, both verified by hand on the sample:

  * A/DEC/* and E/DEC/* (GA and ECOSOC *decisions*) do NOT exist as individual
    ODS documents — decisions are only published inside compilation volumes.
    They are excluded from default catalog selection (still fetchable via
    --symbols-file if you list them explicitly). See docs/fulltexts.md.
  * Digital Library invents *bracket pseudo-symbols* (A/RES/50/204[A],
    A/RES/63/108[B-IV]) for parts of a combined resolution; ODS only knows the
    parent (A/RES/50/204). We never issue a request containing '['. Bracketed
    targets are collapsed onto their parent: if the parent is already a target
    or in the ledger we drop the bracket entirely (the parent's file covers it);
    otherwise we fetch the parent file and record it under the parent
    symbol_normalized.

Usage:
    uv run python python/fulltext_fetch.py                       # full catalog
    uv run python python/fulltext_fetch.py --limit 100 --rate 4
    uv run python python/fulltext_fetch.py --symbols-file todo.txt
    uv run python python/fulltext_fetch.py --recheck-unavailable  # slow 2nd pass
    uv run python python/fulltext_fetch.py --dry-run
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ODS_URL = "https://documents.un.org/api/symbol/access"
USER_AGENT = "curl/8.7.1"  # a curl-like UA avoids the WAF JS challenge
STATE_KEY = "fulltext_fetch"

# Corpus: 8 symbol families, English, published 1994-01-01 or later. A/DEC and
# E/DEC are matched here but filtered out of default catalog selection below
# (see DECISION_FAMILY_RE) — kept in the regex only so their exclusion count is
# auditable and so --symbols-file can still reach them.
CATALOG_REGEX = r"^(A/RES/|A/DEC/|S/RES/|S/PRST/|E/RES/|E/DEC/|A/HRC/RES/|A/HRC/PRST/)"
MIN_DATE = "1994-01-01"

# GA/ECOSOC decisions: structurally absent as standalone ODS documents.
DECISION_FAMILY_RE = re.compile(r"^(A/DEC/|E/DEC/|A/HRC/(?:RES|PRST)/(?:[1-9]|1[01])/)")

HTTP_TIMEOUT = 60
MAX_RETRIES = 3          # per-request retries on 5xx / connection error / empty
FLUSH_EVERY = 20         # buffer ledger writes, flush (open→upsert→close) this often
PROGRESS_EVERY = 25
STATE_EVERY = 50

# --- Politeness knobs (see module docstring) -------------------------------
DEFAULT_RATE = 1.5                 # seconds between requests
JITTER_FRAC = 0.30                 # ±30% on every sleep
REST_EVERY = 500                   # requests between scheduled rest breaks
REST_MIN, REST_MAX = 60.0, 120.0   # rest break length: 1-2 min
SOFT_BLOCK_SLEEPS = ()             # html = deterministic error page: no in-run retries;
                                   # --recheck-unavailable is the second chance
BLOCK_PAUSE = 600.0                # 10 min on HTTP 429/403 or connection reset
CIRCUIT_THRESHOLD = 25             # consecutive block/miss outcomes ⇒ trip
CIRCUIT_SLEEPS = (300.0, 900.0)    # trip #1 ⇒ 5 min, trip #2+ ⇒ 15 min (never exits)

_WS_RE = re.compile(r"\s")


def normalize_symbol(symbol: str) -> str:
    """Local equivalent of the documents.symbol_normalized generated column."""
    return _WS_RE.sub("", symbol).upper()


def strip_bracket(symbol: str) -> str:
    """Strip a DL bracket suffix to recover the parent symbol.

    'A/RES/50/204[A]'    -> 'A/RES/50/204'
    'A/RES/63/108[B-IV]' -> 'A/RES/63/108'
    A symbol with no '[' is returned unchanged.
    """
    idx = symbol.find("[")
    return symbol[:idx].rstrip() if idx != -1 else symbol


# ---------------------------------------------------------------------------
# Timing helpers (jitter + logged long pauses)
# ---------------------------------------------------------------------------

def _jitter(seconds: float) -> float:
    """Apply ±JITTER_FRAC random jitter so sleeps never form a robotic cadence."""
    return seconds * random.uniform(1.0 - JITTER_FRAC, 1.0 + JITTER_FRAC)


def nap(seconds: float) -> None:
    """Short, unlogged, jittered inter-request sleep."""
    time.sleep(max(0.0, _jitter(seconds)))


def long_pause(seconds: float, reason: str, *, jitter: bool = True) -> None:
    """A logged, deliberate pause (rest break / back-off / circuit breaker)."""
    secs = _jitter(seconds) if jitter else seconds
    print(f"  [pause ~{secs / 60:.1f} min] {reason}", file=sys.stderr, flush=True)
    time.sleep(max(0.0, secs))


class RunState:
    """Session-level counters for auditable friendliness."""

    def __init__(self) -> None:
        self.requests = 0     # total ODS GETs issued (incl. retries)
        self.block_hits = 0   # 429/403/connection-reset events observed
        self.started = time.monotonic()

    def per_hour(self) -> float:
        elapsed = time.monotonic() - self.started
        return self.requests * 3600.0 / elapsed if elapsed > 0 else 0.0


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------

def load_already_done(conn) -> set[str]:
    """symbol_normalized values already fetched/unavailable/etc. — everything
    except 'failed', which we retry."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized FROM digitallibrary.document_files "
            "WHERE lang = 'en' AND status <> 'failed'"
        )
        return {row[0] for row in cur.fetchall()}


def resolve_document_symbols(conn, norms: list[str]) -> dict[str, str]:
    """Map symbol_normalized -> canonical document_symbol (highest recid)."""
    if not norms:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (symbol_normalized) symbol_normalized, document_symbol
            FROM digitallibrary.documents
            WHERE symbol_normalized = ANY(%s)
            ORDER BY symbol_normalized, recid DESC
            """,
            [norms],
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def targets_from_catalog(conn) -> list[tuple[str, str]]:
    """Return [(symbol_normalized, document_symbol)] for the in-scope corpus,
    one row per symbol_normalized (highest recid wins), newest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol_normalized, document_symbol
            FROM (
              SELECT DISTINCT ON (symbol_normalized)
                     symbol_normalized, document_symbol, recid, date_publication
              FROM digitallibrary.documents
              WHERE deleted_at IS NULL
                AND date_publication >= %s
                AND (cardinality(languages) = 0 OR 'eng' = ANY(languages))
                AND symbol_normalized ~ %s
              ORDER BY symbol_normalized, recid DESC
            ) t
            ORDER BY date_publication DESC NULLS LAST, symbol_normalized
            """,
            [MIN_DATE, CATALOG_REGEX],
        )
        return [(row[0], row[1]) for row in cur.fetchall()]


def targets_from_file(conn, path: Path) -> list[tuple[str, str]]:
    """Read symbols (one per line, priority order) and resolve each to its
    canonical document_symbol via the catalog, preserving file order."""
    raw = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
    norms = [normalize_symbol(s) for s in raw]
    canonical = resolve_document_symbols(conn, norms)
    # Preserve file order; fall back to the raw line when not in the catalog.
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_sym, norm in zip(raw, norms):
        if norm in seen:
            continue
        seen.add(norm)
        out.append((norm, canonical.get(norm, raw_sym)))
    return out


def targets_unavailable(conn) -> list[tuple[str, str]]:
    """Every ledger row currently status='unavailable' — the re-probe set for
    --recheck-unavailable, resolved to canonical document symbols."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized FROM digitallibrary.document_files "
            "WHERE lang = 'en' AND status = 'unavailable' "
            "ORDER BY symbol_normalized"
        )
        norms = [row[0] for row in cur.fetchall()]
    canonical = resolve_document_symbols(conn, norms)
    return [(n, canonical.get(n, n)) for n in norms]


def targets_recheck_recent(conn, days: int) -> list[tuple[str, str]]:
    """Re-probe set for --recheck-recent-days: ledger rows currently
    status='unavailable' whose underlying document was PUBLISHED within `days`
    (freshly adopted docs lag ODS by days-to-weeks).

    Deliberately date_publication-only: an updated_at window would sweep in the
    thousands of historical absences recorded during a backfill and blow the CI
    time budget, while adding nothing — late-HARVESTED old records have no
    ledger row at all and are picked up by the fetch-new stage instead.

    documents is joined on symbol_normalized, DISTINCT ON (recid DESC)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH d AS (
              SELECT DISTINCT ON (symbol_normalized) symbol_normalized, date_publication
              FROM digitallibrary.documents
              ORDER BY symbol_normalized, recid DESC
            )
            SELECT f.symbol_normalized
            FROM digitallibrary.document_files f
            LEFT JOIN d USING (symbol_normalized)
            WHERE f.lang = 'en' AND f.status = 'unavailable'
              AND d.date_publication >= (now()::date - %s)
            ORDER BY f.symbol_normalized
            """,
            [days],
        )
        norms = [row[0] for row in cur.fetchall()]
    canonical = resolve_document_symbols(conn, norms)
    return [(n, canonical.get(n, n)) for n in norms]


def targets_sync_archive(conn) -> list[tuple[str, str, str, str, str | None]]:
    """Ledger rows whose archived ORIGINAL file is missing from ARCHIVE_ROOT.

    Selects status IN ('fetched','converted','extracted','parsed') with a
    non-NULL archive_path, then keeps only those whose file does not exist on
    disk. Returns [(symbol_normalized, format, archive_path, status, sha256)]."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol_normalized, format, archive_path, status, sha256 "
            "FROM digitallibrary.document_files "
            "WHERE lang = 'en' AND archive_path IS NOT NULL "
            "AND status IN ('fetched','converted','extracted','parsed') "
            "ORDER BY symbol_normalized"
        )
        rows = cur.fetchall()
    return [(r[0], r[1], r[2], r[3], r[4]) for r in rows
            if not (ARCHIVE_ROOT / r[2]).exists()]


def dedupe_brackets(
    targets: list[tuple[str, str]], already: set[str]
) -> tuple[list[tuple[str, str]], int]:
    """Collapse DL bracket pseudo-symbols onto their parent.

    Guarantees no returned symbol contains '['. A bracketed target is dropped
    when its parent is already covered (another target or a ledger row);
    otherwise it is promoted to fetch the parent file and stored under the
    parent symbol_normalized. Returns (targets, collapsed_count).
    """
    plain = {n for (n, _) in targets if "[" not in n}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    collapsed = 0
    for norm, doc_sym in targets:
        if "[" in norm:
            parent = normalize_symbol(strip_bracket(norm))
            if parent in plain or parent in already or parent in seen:
                collapsed += 1
                continue
            # Parent is not in the corpus: fetch it, store under the parent.
            norm, doc_sym = parent, strip_bracket(doc_sym)
            collapsed += 1
        if norm in seen:
            continue
        seen.add(norm)
        out.append((norm, doc_sym))
    return out, collapsed


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _is_conn_reset(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    text = str(exc).lower()
    return "reset" in text or "aborted" in text or "broken pipe" in text


def fetch_ods(
    session: requests.Session, document_symbol: str, run: RunState, t: str = "doc"
) -> tuple[int, bytes, str]:
    """GET the ODS file for a symbol. Retries (exp backoff) on connection
    errors, 5xx, and empty bodies. On HTTP 429/403 or a connection reset it
    pauses BLOCK_PAUSE before retrying and bumps run.block_hits. Returns
    (status_code, content, content_type).

    `t` selects the ODS format token: 'doc' (default, the Word path) or 'pdf'.
    The pseudo-redirect follow below only fires on the `t=pdf` archive bodies
    ('Found. Redirecting to /doc/UNDOC/...'); a Word (t=doc) response never
    starts with that marker, so the default path is byte-for-byte unchanged."""
    # ODS requires %20 for spaces in the symbol; requests' params dict would
    # encode them as '+', which redirects to the /error page (looks identical
    # to "document not found"). Build the query string ourselves.
    query = urllib.parse.urlencode(
        {"s": document_symbol, "l": "en", "t": t},
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
            # ODS pseudo-redirect (archive PDFs): 200 text/plain
            # "Found. Redirecting to /doc/UNDOC/.../NRxxxxx.PDF". Follow it with
            # a second GET. Never triggers on the Word (t=doc) path.
            if content.startswith(b"Found. Redirecting to "):
                path = content.decode("ascii", "ignore").split("Redirecting to ", 1)[1].strip()
                if path.startswith("/"):
                    run.requests += 1
                    nap(1.0)
                    resp = session.get(f"https://documents.un.org{path}", timeout=HTTP_TIMEOUT)
                    content = resp.content
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
                wait = _jitter(2 ** attempt)
                print(f"  retry {attempt + 1}/{MAX_RETRIES - 1} for {document_symbol} "
                      f"after {wait:.1f}s: {exc}", file=sys.stderr)
                time.sleep(max(0.0, wait))
    raise requests.RequestException(str(last_exc) if last_exc else "unknown error")


@dataclass
class FetchOutcome:
    outcome: str                 # 'fetched' | 'unavailable' | 'failed'
    status_code: int | None
    content: bytes
    content_type: str | None
    fmt: str | None
    error: str | None


def fetch_target(
    session: requests.Session, document_symbol: str, run: RunState
) -> FetchOutcome:
    """Fetch one symbol, retrying transient html/unknown ("soft-block")
    responses with the SOFT_BLOCK_SLEEPS long sleeps before giving up. Network
    or HTTP failures (after fetch_ods's own retries) become 'failed'; a valid
    file is 'fetched'; anything still html/unknown is 'unavailable'."""
    attempts = 1 + len(SOFT_BLOCK_SLEEPS)
    status_code: int | None = None
    content = b""
    content_type: str | None = None
    fmt: str | None = None
    for attempt in range(attempts):
        try:
            status_code, content, content_type = fetch_ods(session, document_symbol, run)
        except requests.RequestException as exc:
            return FetchOutcome("failed", None, b"", None, None, str(exc)[:500])
        fmt = sniff_format(content[:512])
        if status_code == 200 and fmt not in ("html", "unknown"):
            return FetchOutcome("fetched", status_code, content, content_type, fmt, None)
        if attempt < attempts - 1:
            long_pause(SOFT_BLOCK_SLEEPS[attempt],
                       f"{document_symbol}: HTTP {status_code}/{fmt} — soft-block "
                       f"retry {attempt + 1}/{len(SOFT_BLOCK_SLEEPS)}")
    return FetchOutcome("unavailable", status_code, content, content_type, fmt,
                        f"HTTP {status_code}, sniffed {fmt}")


def save_atomic(dest: Path, data: bytes) -> None:
    """Write bytes to a temp file in the same dir, then rename into place."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_sync_archive(args: argparse.Namespace) -> int:
    """Bring the local SSD archive back in sync with the ledger.

    For every ledger row (fetched/converted/extracted/parsed) whose archive_path
    file is missing on disk, re-download the original from ODS (same politeness)
    and save it to archive_path. Word rows use t=doc, PDF rows (format='pdf')
    route to t=pdf with the pseudo-redirect follow. The LEDGER IS NOT CHANGED
    except updated_at (always) and sha256 (only when the re-downloaded bytes
    differ) — status/paths are preserved. Local-only; not part of the nightly."""
    with get_conn() as conn:
        missing = targets_sync_archive(conn)
        canonical = resolve_document_symbols(conn, [m[0] for m in missing])

    print(f"Mode: sync-archive | ARCHIVE_ROOT={ARCHIVE_ROOT}")
    print(f"Ledger rows with a missing archive file: {len(missing)}")
    if args.dry_run:
        for norm, fmt, rel, status, _ in missing[:12]:
            print(f"  would re-download: {canonical.get(norm, norm)} "
                  f"[{fmt}/{status}] -> {rel}")
        if len(missing) > 12:
            print(f"  ... and {len(missing) - 12} more")
        return 0
    if not missing:
        print("Archive is already in sync with the ledger.")
        return 0

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    run = RunState()
    restored = 0
    changed = 0
    still_missing = 0
    total = len(missing)
    for i, (norm, fmt, rel, status, old_sha) in enumerate(missing):
        document_symbol = canonical.get(norm, norm)
        t = "pdf" if fmt == "pdf" else "doc"
        try:
            status_code, content, _ = fetch_ods(session, document_symbol, run, t=t)
        except requests.RequestException as exc:
            still_missing += 1
            print(f"  ! {document_symbol}: fetch failed: {exc}", file=sys.stderr)
            if i != total - 1:
                nap(args.rate)
            continue
        sniffed = sniff_format(content[:512])
        if status_code != 200 or sniffed in ("html", "unknown"):
            still_missing += 1
            print(f"  ! {document_symbol}: HTTP {status_code}/{sniffed} — cannot "
                  f"restore {rel}", file=sys.stderr)
        else:
            save_atomic(ARCHIVE_ROOT / rel, content)
            new_sha = sha256_bytes(content)
            restored += 1
            with get_conn() as conn:
                if new_sha != (old_sha or ""):
                    changed += 1
                    upsert_document_file(conn, norm, "en", status=status, sha256=new_sha)
                else:
                    upsert_document_file(conn, norm, "en", status=status)
                conn.commit()
        done = i + 1
        if done % PROGRESS_EVERY == 0 or done == total:
            print(f"synced {done}/{total} restored={restored} changed={changed} "
                  f"still_missing={still_missing} | {run.requests} reqs, "
                  f"{run.per_hour():.0f}/h")
        if i != total - 1:
            nap(args.rate)

    print(f"\nDone. restored {restored}/{total} archive files "
          f"({changed} with changed sha256, {still_missing} still missing).")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch English Word files from ODS")
    p.add_argument("--catalog", action="store_true",
                   help="explicitly select the in-scope catalog (this is already the "
                        "default when no other source is given; the flag documents intent "
                        "and mirrors fulltext_fetch_pdf.py)")
    p.add_argument("--symbols-file", type=Path,
                   help="newline-separated document symbols to fetch (priority order)")
    p.add_argument("--recheck-unavailable", action="store_true",
                   help="re-probe every ledger row currently status='unavailable' "
                        "(slow second pass to recover soft-blocked docs)")
    p.add_argument("--recheck-recent-days", type=int, metavar="N",
                   help="re-probe only 'unavailable' rows whose document was published "
                        "within N days OR whose ledger row was (re)touched within N days "
                        "(updated_at proxy — no created_at column). For the nightly: "
                        "recover freshly-adopted docs that ODS published after we first "
                        "probed. Decision-family exclusions still apply.")
    p.add_argument("--sync-archive", action="store_true",
                   help="LOCAL-ONLY: re-download any archived original that is missing "
                        "from ARCHIVE_ROOT (fetched/converted/extracted/parsed rows). "
                        "Ledger unchanged except updated_at/sha256. Not part of the nightly.")
    p.add_argument("--limit", type=int, help="stop after N targets")
    p.add_argument("--rate", type=float, default=DEFAULT_RATE,
                   help=f"seconds to sleep between requests (default {DEFAULT_RATE}; "
                        "±30%% jitter is applied automatically)")
    p.add_argument("--dry-run", action="store_true",
                   help="list targets, do not fetch or write")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # --sync-archive is a standalone maintenance mode with its own flow.
    if args.sync_archive:
        return run_sync_archive(args)

    sources = sum(bool(x) for x in (
        args.symbols_file, args.recheck_unavailable,
        args.recheck_recent_days is not None))
    if sources > 1:
        print("error: --symbols-file, --recheck-unavailable and --recheck-recent-days "
              "are mutually exclusive", file=sys.stderr)
        return 2

    is_recheck = args.recheck_unavailable or args.recheck_recent_days is not None

    # Build the target list and the skip set with one short-lived connection.
    with get_conn() as conn:
        already = load_already_done(conn)
        if args.recheck_unavailable:
            targets = targets_unavailable(conn)
        elif args.recheck_recent_days is not None:
            targets = targets_recheck_recent(conn, args.recheck_recent_days)
        elif args.symbols_file:
            targets = targets_from_file(conn, args.symbols_file)
        else:
            targets = targets_from_catalog(conn)
        state = read_state(conn, STATE_KEY)

    # Exclude structurally-absent decision families from any non-explicit run
    # (catalog and recheck). --symbols-file lets a caller reach them anyway.
    excluded_decisions = 0
    if not args.symbols_file:
        kept: list[tuple[str, str]] = []
        for norm, sym in targets:
            if DECISION_FAMILY_RE.match(norm):
                excluded_decisions += 1
            else:
                kept.append((norm, sym))
        targets = kept

    # Collapse DL bracket pseudo-symbols onto their parent (never request '[').
    targets, collapsed_brackets = dedupe_brackets(targets, already)

    if is_recheck:
        pending = list(targets)  # these ARE the 'unavailable' rows — re-probe them
    else:
        pending = [(n, s) for (n, s) in targets if n not in already]
    if args.limit:
        pending = pending[: args.limit]

    mode = ("recheck-unavailable" if args.recheck_unavailable
            else f"recheck-recent-{args.recheck_recent_days}d"
            if args.recheck_recent_days is not None
            else "symbols-file" if args.symbols_file else "catalog")
    print(f"Mode: {mode}")
    print(f"Targets: {len(targets)} | already done: {len(already)} | "
          f"to fetch: {len(pending)}")
    print(f"Excluded A/DEC+E/DEC decisions (structurally absent): {excluded_decisions} "
          f"| bracket pseudo-symbols collapsed onto parent: {collapsed_brackets}")
    if state:
        print(f"Previous run watermark: {state}")

    if args.dry_run:
        for norm, sym in pending[:10]:
            print(f"  would fetch: {sym}  ->  {sanitize_symbol(norm)}")
        if len(pending) > 10:
            print(f"  ... and {len(pending) - 10} more")
        return 0

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    run = RunState()
    total = len(pending)
    ok = unavailable = failed = 0
    consecutive_block = 0
    circuit_trips = 0
    next_rest = REST_EVERY
    buffer: list[dict] = []  # buffered ledger rows, flushed without an HTTP call open

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
        ods_url = f"{ODS_URL}?s={requests.utils.quote(document_symbol)}&l=en&t=doc"
        before_blocks = run.block_hits
        res = fetch_target(session, document_symbol, run)
        block_during = run.block_hits > before_blocks

        if res.outcome == "fetched":
            ext = FORMAT_EXT[res.fmt]
            rel_path = f"original/{sanitize_symbol(norm)}.{ext}"
            save_atomic(ARCHIVE_ROOT / rel_path, res.content)
            ok += 1
            buffer.append({
                "symbol_normalized": norm, "status": "fetched", "format": res.fmt,
                "content_type": res.content_type, "size_bytes": len(res.content),
                "sha256": sha256_bytes(res.content), "ods_url": ods_url,
                "archive_path": rel_path, "error": None,
                "fetched_at": datetime.now(timezone.utc),
            })
        elif res.outcome == "unavailable":
            unavailable += 1
            buffer.append({
                "symbol_normalized": norm, "status": "unavailable",
                "format": res.fmt, "content_type": res.content_type, "ods_url": ods_url,
                "size_bytes": len(res.content), "error": res.error,
            })
        else:  # failed
            failed += 1
            buffer.append({
                "symbol_normalized": norm, "status": "failed",
                "ods_url": ods_url, "content_type": None, "error": res.error,
            })

        # Only real block signals (429/403/connection resets) count toward the
        # circuit; html misses are genuine ODS absences (often whole missing
        # volumes, e.g. E/RES 2011) — they are data, not danger.
        if block_during:
            consecutive_block += 1
        elif res.outcome == "fetched":
            consecutive_block = 0

        done = i + 1
        if len(buffer) >= FLUSH_EVERY:
            flush()
        if done % PROGRESS_EVERY == 0 or done == total:
            print(f"fetched {done}/{total} ok={ok} unavailable={unavailable} "
                  f"failed={failed} | {run.requests} reqs, {run.per_hour():.0f}/h")
        if done % STATE_EVERY == 0:
            flush()
            persist_watermark(done)

        # Circuit breaker: consecutive misses are usually genuine absence
        # clusters (e.g. E/RES/2011/12-16 share a missing volume), NOT blocks
        # (the phantom-block era ended with the %20 encoding fix). So: high
        # threshold, short pauses, and never exit — just log loudly.
        if consecutive_block >= CIRCUIT_THRESHOLD:
            circuit_trips += 1
            flush()
            persist_watermark(done)
            pause = CIRCUIT_SLEEPS[min(circuit_trips, len(CIRCUIT_SLEEPS)) - 1]
            long_pause(pause,
                       f"circuit breaker trip #{circuit_trips}: "
                       f"{consecutive_block} consecutive misses — likely an "
                       f"absence cluster; pausing as a precaution")
            consecutive_block = 0

        # Scheduled rest break to keep well below the observed block threshold.
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
