#!/usr/bin/env python3
"""Incremental harvest of UN Digital Library — fetches new/changed records since last run.

Designed to run nightly via GitHub Action. State is stored in the database
(digitallibrary.harvest_state) so it works across ephemeral CI runners.

Usage:
    uv run python python/harvest_incremental.py           # auto (reads watermark from DB)
    uv run python python/harvest_incremental.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, date, timedelta, timezone
from urllib.parse import quote

import psycopg
import requests
from dotenv import load_dotenv

from marc_parser import parse_record

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEARCH_BASE = "https://digitallibrary.un.org/search"
COLLECTION = "Documents and Publications"
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
CRAWL_DELAY = 5
MAX_PER_REQUEST = 200
STATE_KEY = "incremental_sync"

# We scan the top of the ID space for new records, plus re-check recent IDs
# for modifications. New records get assigned IDs at the frontier.
FRONTIER_LOOKBACK = 5000  # scan this many IDs beyond the last known max

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

_RECORD_RE = re.compile(r"<record\b[^>]*>.*?</record>", re.DOTALL)


# ---------------------------------------------------------------------------
# DB state helpers
# ---------------------------------------------------------------------------

def read_state(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM digitallibrary.harvest_state WHERE key = %s",
            [STATE_KEY],
        )
        row = cur.fetchone()
        return row[0] if row else {}


def write_state(conn: psycopg.Connection, state: dict) -> None:
    state["updated_at"] = _utc_now()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO digitallibrary.harvest_state (key, value, updated_at)
               VALUES (%s, %s::jsonb, NOW())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
            [STATE_KEY, json.dumps(state)],
        )
    conn.commit()


def get_max_recid(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(recid), 0) FROM digitallibrary.documents")
        return cur.fetchone()[0]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# HTTP + parsing
# ---------------------------------------------------------------------------

def fetch_marcxml_range(start: int, end: int, session: requests.Session) -> list[str]:
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
            if resp.status_code == 202 or not resp.text.strip():
                raise requests.RequestException(
                    f"Empty/202 response (WAF challenge?), status={resp.status_code}"
                )
            # Force UTF-8 — server may not declare charset, causing
            # requests to default to ISO-8859-1 and mangle non-ASCII text.
            resp.encoding = "utf-8"
            return _RECORD_RE.findall(resp.text)
        except (requests.RequestException, OSError) as exc:
            if attempt == 3:
                raise
            wait = 2 ** attempt * CRAWL_DELAY
            print(f"  Retry {attempt + 1}/3 after {wait}s: {exc}", file=sys.stderr)
            time.sleep(wait)
    return []


def fetch_and_parse_range(start: int, end: int, session: requests.Session) -> list[dict]:
    """Fetch a recid range, subdivide if truncated."""
    record_xmls = fetch_marcxml_range(start, end, session)

    if len(record_xmls) >= MAX_PER_REQUEST and (end - start) > 1:
        mid = (start + end) // 2
        time.sleep(CRAWL_DELAY)
        left = fetch_and_parse_range(start, mid, session)
        time.sleep(CRAWL_DELAY)
        right = fetch_and_parse_range(mid + 1, end, session)
        return left + right

    parsed = []
    for xml_str in record_xmls:
        rec = parse_record(xml_str)
        if rec:
            parsed.append(rec)
    return parsed


def prepare_row(rec: dict) -> dict:
    return {
        **rec,
        "date_publication": rec["date_publication"].isoformat() if rec["date_publication"] else None,
        "corporate_authors": json.dumps(rec["corporate_authors"], ensure_ascii=False),
        "files": json.dumps(rec["files"], ensure_ascii=False),
        "agenda_items": json.dumps(rec["agenda_items"], ensure_ascii=False),
        "related_documents": json.dumps(rec["related_documents"], ensure_ascii=False),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incremental UNDL harvest")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse only, no DB writes")
    return parser.parse_args()


def _connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(ensure_ssl_cert(database_url), autocommit=False)


def main() -> int:
    load_dotenv()
    args = parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2

    # Read initial state with a short-lived connection to avoid idle timeouts
    # on serverless Postgres (Supabase/Neon reap idle connections)
    with _connect(database_url) as conn:
        state = read_state(conn)
        max_recid = get_max_recid(conn)

    print(f"Current max recid in DB: {max_recid}")
    print(f"Last incremental run: {state.get('last_run_at', 'never')}")

    # Strategy: scan the frontier for new records beyond current max
    # New records in UNDL get sequentially higher IDs
    scan_start = max(max_recid - 200, 1)  # re-check a small overlap
    scan_end = max_recid + FRONTIER_LOOKBACK

    print(f"Scanning recid range: {scan_start} → {scan_end}")

    session = requests.Session()
    # Use default python-requests UA — custom UAs trigger AWS WAF JS challenges

    total_upserted = 0
    chunk_start = scan_start

    while chunk_start <= scan_end:
        chunk_end = min(chunk_start + MAX_PER_REQUEST - 1, scan_end)

        try:
            records = fetch_and_parse_range(chunk_start, chunk_end, session)
        except Exception as exc:
            print(f"  Failed range {chunk_start}→{chunk_end}: {exc}", file=sys.stderr)
            chunk_start = chunk_end + 1
            time.sleep(CRAWL_DELAY)
            continue

        if records and not args.dry_run:
            with _connect(database_url) as conn:
                with conn.cursor() as cur:
                    for rec in records:
                        cur.execute(UPSERT_SQL, prepare_row(rec))
                conn.commit()
            total_upserted += len(records)

            # Extend scan if we found records near the frontier
            new_max = max(r["recid"] for r in records)
            if new_max >= scan_end - MAX_PER_REQUEST:
                scan_end = new_max + FRONTIER_LOOKBACK
                print(f"  Extended scan to {scan_end} (found records near frontier)")
        elif records and args.dry_run:
            total_upserted += len(records)

        if records:
            print(f"  recid {chunk_start}→{chunk_end}: {len(records)} records")

        chunk_start = chunk_end + 1
        time.sleep(CRAWL_DELAY)

    # Save state
    if not args.dry_run:
        with _connect(database_url) as conn:
            new_max = get_max_recid(conn)
            write_state(conn, {
                "last_run_at": _utc_now(),
                "records_upserted": total_upserted,
                "max_recid_after": new_max,
                "scan_range": [scan_start, scan_end],
            })

    print(f"\nDone. {'Parsed' if args.dry_run else 'Upserted'}: {total_upserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
