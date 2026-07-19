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

Politeness / anti-block strategy (learned from a 317-doc sample run at 1 req/s,
after which ODS started soft-blocking — returning the ~1.3 KB "not available"
HTML page for documents that ARE available, indistinguishable from a genuine
not-found, and recovering only minutes later). This script therefore runs
*deliberately slowly* to stay far below that threshold:

  * ~3 s between requests, with ±30% random jitter (no robotic cadence).
  * A scheduled rest break of 1-2 min after every ~500 requests, regardless of
    whether anything failed.
  * Soft-block handling: an html/unknown response is retried up to 2 more times
    (sleeps of 30 s then 120 s) before the symbol is recorded 'unavailable'.
  * A tiered circuit breaker: 8 consecutive block/miss outcomes ⇒ pause 15 min
    (1st trip), 60 min (2nd trip), then exit cleanly on the 3rd (resumable — we
    would rather stop than hammer).
  * HTTP 429/403 or a connection reset ⇒ an immediate 10-min pause before
    retrying, and it counts toward the circuit breaker.

Net effect: ≲1200 requests/hour. The full backfill takes about a day of
wall-clock time. That is intentional — run it and forget it.

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
DECISION_FAMILY_RE = re.compile(r"^(A/DEC/|E/DEC/)")

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
SOFT_BLOCK_SLEEPS = (30.0, 120.0)  # extra retries on html/unknown before 'unavailable'
BLOCK_PAUSE = 600.0                # 10 min on HTTP 429/403 or connection reset
CIRCUIT_THRESHOLD = 8              # consecutive block/miss outcomes ⇒ trip
CIRCUIT_SLEEPS = (900.0, 3600.0)   # trip #1 ⇒ 15 min, trip #2 ⇒ 60 min, trip #3 ⇒ exit

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
    session: requests.Session, document_symbol: str, run: RunState
) -> tuple[int, bytes, str]:
    """GET the ODS file for a symbol. Retries (exp backoff) on connection
    errors, 5xx, and empty bodies. On HTTP 429/403 or a connection reset it
    pauses BLOCK_PAUSE before retrying and bumps run.block_hits. Returns
    (status_code, content, content_type)."""
    params = {"s": document_symbol, "l": "en", "t": "doc"}
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            run.requests += 1
            resp = session.get(ODS_URL, params=params, timeout=HTTP_TIMEOUT)
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

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch English Word files from ODS")
    p.add_argument("--symbols-file", type=Path,
                   help="newline-separated document symbols to fetch (priority order)")
    p.add_argument("--recheck-unavailable", action="store_true",
                   help="re-probe every ledger row currently status='unavailable' "
                        "(slow second pass to recover soft-blocked docs)")
    p.add_argument("--limit", type=int, help="stop after N targets")
    p.add_argument("--rate", type=float, default=DEFAULT_RATE,
                   help=f"seconds to sleep between requests (default {DEFAULT_RATE}; "
                        "±30%% jitter is applied automatically)")
    p.add_argument("--dry-run", action="store_true",
                   help="list targets, do not fetch or write")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.recheck_unavailable and args.symbols_file:
        print("error: --recheck-unavailable and --symbols-file are mutually exclusive",
              file=sys.stderr)
        return 2

    # Build the target list and the skip set with one short-lived connection.
    with get_conn() as conn:
        already = load_already_done(conn)
        if args.recheck_unavailable:
            targets = targets_unavailable(conn)
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

    if args.recheck_unavailable:
        pending = list(targets)  # these ARE the 'unavailable' rows — re-probe them
    else:
        pending = [(n, s) for (n, s) in targets if n not in already]
    if args.limit:
        pending = pending[: args.limit]

    mode = ("recheck-unavailable" if args.recheck_unavailable
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

        # A clean success resets the circuit; anything else (miss, failure, or a
        # target that hit a 429/403/reset) counts toward it.
        if res.outcome == "fetched" and not block_during:
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

        # Tiered circuit breaker: trip #1 ⇒ 15 min, #2 ⇒ 60 min, #3 ⇒ stop.
        if consecutive_block >= CIRCUIT_THRESHOLD:
            circuit_trips += 1
            flush()
            persist_watermark(done)
            if circuit_trips > len(CIRCUIT_SLEEPS):
                print(f"\n!! Circuit breaker tripped {circuit_trips}× "
                      f"({consecutive_block} consecutive block/miss outcomes). "
                      f"Exiting cleanly — this is resumable, re-run later to continue.",
                      file=sys.stderr, flush=True)
                print(f"\nStopped early. ok={ok} unavailable={unavailable} "
                      f"failed={failed} of {total} (processed {done}).")
                return 0
            long_pause(CIRCUIT_SLEEPS[circuit_trips - 1],
                       f"circuit breaker trip #{circuit_trips}: "
                       f"{consecutive_block} consecutive block/miss outcomes — "
                       f"assuming soft-block")
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
