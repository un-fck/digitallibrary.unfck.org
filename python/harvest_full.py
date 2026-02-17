#!/usr/bin/env python3
"""Bulk harvest of UN Digital Library via search API with recid range slicing.

Fetches MARCXML for all "Documents and Publications" records and upserts into Postgres.

Usage:
    uv run python python/harvest_full.py                        # fresh start
    uv run python python/harvest_full.py --resume               # continue from checkpoint
    uv run python python/harvest_full.py --max-records 500      # limited test run
    uv run python python/harvest_full.py --dry-run              # parse only, no DB writes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from dotenv import load_dotenv

import psycopg
import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn

from marc_parser import parse_record

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEARCH_BASE = "https://digitallibrary.un.org/search"
COLLECTION = "Documents and Publications"
CRAWL_DELAY = 5  # seconds, per robots.txt
MAX_PER_REQUEST = 200
DEFAULT_BATCH_SIZE = 1000
STATE_PATH = Path("python/.harvest_state.json")

# Two dense ID blocks (discovered via investigation)
ID_BLOCKS = [
    (1, 865_000),
    (3_797_000, 4_103_000),
]

UPSERT_SQL = """
INSERT INTO digitallibrary.documents (
  recid, document_symbol, symbol_body, symbol_session, symbol_committee,
  title, title_statement, date_publication, date_text, publisher, pub_place,
  physical_desc, doc_class_code, doc_class_desc, languages, subjects,
  corporate_authors, un_body, un_committee, notes, summary, files,
  collections, resource_type, resource_subtype, vote_summary,
  agenda_items, related_documents, marcxml, harvested_at
)
VALUES (
  %(recid)s, %(document_symbol)s, %(symbol_body)s, %(symbol_session)s,
  %(symbol_committee)s, %(title)s, %(title_statement)s, %(date_publication)s,
  %(date_text)s, %(publisher)s, %(pub_place)s, %(physical_desc)s,
  %(doc_class_code)s, %(doc_class_desc)s, %(languages)s, %(subjects)s,
  %(corporate_authors)s::jsonb, %(un_body)s, %(un_committee)s, %(notes)s,
  %(summary)s, %(files)s::jsonb, %(collections)s, %(resource_type)s,
  %(resource_subtype)s, %(vote_summary)s, %(agenda_items)s::jsonb,
  %(related_documents)s::jsonb, %(marcxml)s, NOW()
)
ON CONFLICT (recid) DO UPDATE SET
  document_symbol = EXCLUDED.document_symbol,
  symbol_body = EXCLUDED.symbol_body,
  symbol_session = EXCLUDED.symbol_session,
  symbol_committee = EXCLUDED.symbol_committee,
  title = EXCLUDED.title,
  title_statement = EXCLUDED.title_statement,
  date_publication = EXCLUDED.date_publication,
  date_text = EXCLUDED.date_text,
  publisher = EXCLUDED.publisher,
  pub_place = EXCLUDED.pub_place,
  physical_desc = EXCLUDED.physical_desc,
  doc_class_code = EXCLUDED.doc_class_code,
  doc_class_desc = EXCLUDED.doc_class_desc,
  languages = EXCLUDED.languages,
  subjects = EXCLUDED.subjects,
  corporate_authors = EXCLUDED.corporate_authors,
  un_body = EXCLUDED.un_body,
  un_committee = EXCLUDED.un_committee,
  notes = EXCLUDED.notes,
  summary = EXCLUDED.summary,
  files = EXCLUDED.files,
  collections = EXCLUDED.collections,
  resource_type = EXCLUDED.resource_type,
  resource_subtype = EXCLUDED.resource_subtype,
  vote_summary = EXCLUDED.vote_summary,
  agenda_items = EXCLUDED.agenda_items,
  related_documents = EXCLUDED.related_documents,
  marcxml = EXCLUDED.marcxml,
  harvested_at = NOW();
"""

console = Console()

SSL_CERT_PATHS = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]


def ensure_ssl_cert(url: str) -> str:
    """Append sslrootcert to DATABASE_URL if not already present."""
    if "sslrootcert" in url:
        return url
    for path in SSL_CERT_PATHS:
        if os.path.exists(path):
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}sslrootcert={path}"
    return url


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _utc_now()
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# HTTP fetching
# ---------------------------------------------------------------------------

def fetch_marcxml(start: int, end: int, session: requests.Session) -> str:
    """Fetch MARCXML for a recid range. Returns raw response text."""
    url = (
        f"{SEARCH_BASE}"
        f"?p=recid%3A{start}-%3E{end}"
        f"&of=xm"
        f"&rg={MAX_PER_REQUEST}"
        f"&cc={quote(COLLECTION)}"
    )
    for attempt in range(4):
        try:
            resp = session.get(url, timeout=120)
            resp.raise_for_status()
            if resp.status_code != 200:
                raise requests.RequestException(
                    f"Unexpected status {resp.status_code} (expected 200)"
                )
            if not resp.text.strip():
                raise requests.RequestException("Empty response body")
            return resp.text
        except (requests.RequestException, OSError) as exc:
            if attempt == 3:
                raise
            wait = 2 ** attempt * CRAWL_DELAY
            console.print(f"  [yellow]Retry {attempt + 1}/3 after {wait}s: {exc}[/yellow]")
            time.sleep(wait)
    return ""  # unreachable


_RECORD_RE = re.compile(
    r"<record\b[^>]*>.*?</record>",
    re.DOTALL,
)


def extract_records(response_text: str) -> list[str]:
    """Extract individual <record>...</record> strings from a MARCXML response."""
    return _RECORD_RE.findall(response_text)


def fetch_and_parse(start: int, end: int, session: requests.Session) -> list[dict]:
    """Fetch a recid range and parse records. Subdivides if possibly truncated."""
    raw = fetch_marcxml(start, end, session)
    record_xmls = extract_records(raw)

    if len(record_xmls) >= MAX_PER_REQUEST and (end - start) > 1:
        # Possibly truncated — subdivide
        mid = (start + end) // 2
        time.sleep(CRAWL_DELAY)
        left = fetch_and_parse(start, mid, session)
        time.sleep(CRAWL_DELAY)
        right = fetch_and_parse(mid + 1, end, session)
        return left + right

    parsed = []
    parse_failures = 0
    for xml_str in record_xmls:
        rec = parse_record(xml_str)
        if rec:
            parsed.append(rec)
        else:
            parse_failures += 1

    if record_xmls and not parsed:
        raise RuntimeError(
            f"All {len(record_xmls)} XML records in range {start}→{end} failed to parse. "
            f"Parser may be broken."
        )
    if parse_failures > 0:
        console.print(f"  [yellow]Warning: {parse_failures}/{len(record_xmls)} records failed to parse in {start}→{end}[/yellow]")

    return parsed


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def prepare_row(rec: dict) -> dict:
    """Convert parsed record dict to psycopg-compatible params."""
    return {
        **rec,
        "date_publication": rec["date_publication"].isoformat() if rec["date_publication"] else None,
        "corporate_authors": json.dumps(rec["corporate_authors"], ensure_ascii=False),
        "files": json.dumps(rec["files"], ensure_ascii=False),
        "agenda_items": json.dumps(rec["agenda_items"], ensure_ascii=False),
        "related_documents": json.dumps(rec["related_documents"], ensure_ascii=False),
    }


def upsert_batch(conn: psycopg.Connection, records: list[dict]) -> int:
    """Upsert a batch of records using pipeline mode to minimize round-trips."""
    if not records:
        return 0
    rows = [prepare_row(rec) for rec in records]
    with conn.pipeline():
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(UPSERT_SQL, row)
    conn.commit()
    return len(records)


# ---------------------------------------------------------------------------
# Main harvest loop
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk harvest UNDL Documents and Publications")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse only, no DB writes")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Records per DB commit")
    parser.add_argument("--max-records", type=int, default=0, help="Stop after N records (0 = unlimited)")
    parser.add_argument("--chunk-size", type=int, default=100, help="Recid range per HTTP request (keep below MAX_PER_REQUEST to avoid subdivision)")
    parser.add_argument("--force", action="store_true", help="Force fresh start, ignore existing checkpoint")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    if not args.dry_run:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            console.print("[red]DATABASE_URL is required[/red]", highlight=False)
            return 2

    if args.resume:
        state = load_state()
        if not state:
            console.print("[yellow]No checkpoint found — starting from scratch[/yellow]")
    else:
        existing = load_state()
        if existing and existing.get("total_upserted", 0) > 0 and not existing.get("finished_at") and not args.force:
            console.print(
                f"[bold red]WARNING:[/bold red] Existing checkpoint has {existing['total_upserted']} records upserted "
                f"(last recid {existing.get('last_completed_end', '?')}). "
                f"Did you mean [bold]--resume[/bold]?"
            )
            console.print("Run with [bold]--resume[/bold] to continue, or [bold]--force[/bold] to start fresh.")
            return 1
        state = {}

    resume_block = state.get("block_index", 0)
    resume_start = state.get("last_completed_end", ID_BLOCKS[resume_block][0] - 1) + 1 if args.resume else None

    total_upserted = state.get("total_upserted", 0)
    total_parsed = state.get("total_parsed", 0)
    skipped_ranges: list[list[int]] = state.get("skipped_ranges", [])

    if not args.dry_run:
        state["started_at"] = state.get("started_at", _utc_now())

    # Estimate total ranges for progress bar
    total_ids = sum(end - start for start, end in ID_BLOCKS)

    session = requests.Session()
    # Use default python-requests UA — custom UAs trigger AWS WAF JS challenges

    conn = None
    if not args.dry_run:
        conn = psycopg.connect(ensure_ssl_cert(database_url), autocommit=False)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Harvesting", total=total_ids)

            # Advance progress bar to resume point
            if args.resume:
                done_ids = 0
                for bi in range(resume_block):
                    done_ids += ID_BLOCKS[bi][1] - ID_BLOCKS[bi][0]
                if resume_start:
                    done_ids += resume_start - ID_BLOCKS[resume_block][0]
                progress.update(task, advance=done_ids)

            batch: list[dict] = []

            for block_index, (block_start, block_end) in enumerate(ID_BLOCKS):
                if block_index < resume_block:
                    continue

                start = resume_start if (block_index == resume_block and resume_start) else block_start
                resume_start = None  # Only applies to first block on resume

                chunk_start = start
                while chunk_start < block_end:
                    if args.max_records and (total_upserted + total_parsed) >= args.max_records:
                        break

                    chunk_end = min(chunk_start + args.chunk_size - 1, block_end)
                    progress.update(task, description=f"Block {block_index + 1}/2  recid {chunk_start}→{chunk_end}")

                    try:
                        records = fetch_and_parse(chunk_start, chunk_end, session)
                    except RuntimeError as exc:
                        # Parse failures are fatal — don't silently skip
                        progress.stop()
                        console.print(f"\n[bold red]FATAL:[/bold red] {exc}")
                        save_state(state)
                        return 1
                    except Exception as exc:
                        console.print(f"  [red]Failed range {chunk_start}→{chunk_end}: {exc}[/red]")
                        skipped_ranges.append([chunk_start, chunk_end])
                        chunk_start = chunk_end + 1
                        progress.update(task, advance=args.chunk_size)
                        time.sleep(CRAWL_DELAY)
                        continue

                    batch.extend(records)

                    if args.dry_run:
                        total_parsed += len(records)
                    elif len(batch) >= args.batch_size:
                        try:
                            written = upsert_batch(conn, batch)
                            total_upserted += written
                        except Exception as exc:
                            conn.rollback()
                            progress.stop()
                            console.print(f"\n[bold red]DB ERROR — aborting:[/bold red] {exc}")
                            console.print("[yellow]Fix the issue and re-run with --resume.[/yellow]")
                            save_state(state)
                            return 1
                        batch = []

                        # Only checkpoint after successful DB commit
                        # so resume re-fetches any uncommitted records
                        state.update({
                            "block_index": block_index,
                            "last_completed_end": chunk_end,
                            "total_upserted": total_upserted,
                            "total_parsed": total_parsed,
                            "skipped_ranges": skipped_ranges,
                        })
                        save_state(state)

                    progress.update(task, advance=chunk_end - chunk_start + 1)
                    chunk_start = chunk_end + 1
                    time.sleep(CRAWL_DELAY)

                if args.max_records and (total_upserted + total_parsed) >= args.max_records:
                    break

            # Flush remaining batch
            if batch and not args.dry_run:
                try:
                    written = upsert_batch(conn, batch)
                    total_upserted += written
                except Exception as exc:
                    conn.rollback()
                    console.print(f"\n[bold red]DB ERROR on final batch — aborting:[/bold red] {exc}")
                    save_state(state)
                    return 1

    finally:
        if conn:
            conn.close()

    state.update({
        "block_index": len(ID_BLOCKS) - 1,
        "last_completed_end": ID_BLOCKS[-1][1],
        "total_upserted": total_upserted,
        "total_parsed": total_parsed,
        "skipped_ranges": skipped_ranges,
        "finished_at": _utc_now(),
    })
    save_state(state)

    console.print(f"\n[green]Done.[/green] {'Parsed' if args.dry_run else 'Upserted'}: {total_upserted + total_parsed}")
    if skipped_ranges:
        console.print(f"[yellow]Skipped {len(skipped_ranges)} ranges (see state file)[/yellow]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
