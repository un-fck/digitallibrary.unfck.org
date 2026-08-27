"""Shared helpers for the document full-text pipeline (Track A).

Used by fulltext_fetch.py (fetch English Word files from ODS), fulltext_convert.py
(LibreOffice doc/wpd -> docx), and — later — the raw paragraph extractor.

Conventions mirror python/harvest_incremental.py: standalone scripts, DATABASE_URL
from the repo-root .env, short-lived psycopg (v3) connections, UPSERT with
ON CONFLICT, and pipeline state in digitallibrary.harvest_state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Archive layout
# ---------------------------------------------------------------------------

# Local SSD on David's Mac. original/ holds native ODS files as fetched;
# converted/ holds LibreOffice-produced .docx for legacy doc/wpd sources.
DEFAULT_ARCHIVE_ROOT = "/Volumes/SSDAStorage/digitallibrary-fulltexts"
ARCHIVE_ROOT = Path(os.getenv("FULLTEXT_ARCHIVE_ROOT", DEFAULT_ARCHIVE_ROOT))

SSL_CERT_PATHS = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]

# Magic-byte format signatures. Order matters: check binary containers before
# the permissive HTML fallback.
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.-]")


# ---------------------------------------------------------------------------
# Filenames & format sniffing
# ---------------------------------------------------------------------------

def sanitize_symbol(symbol: str) -> str:
    """Map a document symbol to a filesystem-safe stem.

    Matches the filename convention used elsewhere in the UN ecosystem: replace
    every char not in [A-Za-z0-9_.-] with '_'. Input is assumed already
    symbol_normalized (upper-cased, whitespace-stripped).

    'A/RES/79(I)' -> 'A_RES_79_I_'
    """
    return _SANITIZE_RE.sub("_", symbol)


def sniff_format(first_bytes: bytes) -> str:
    """Identify a file format from its leading bytes.

    Returns one of 'docx', 'doc', 'wpd', 'pdf', 'html', 'unknown'. Sniffing is
    done on magic bytes, NOT the server's Content-Type, because ODS reports
    text/html for every response including a real 200 HTML "not available" page.
    """
    if first_bytes.startswith(b"PK\x03\x04") or first_bytes.startswith(b"PK\x05\x06"):
        return "docx"  # any OOXML/zip container; real .docx for our corpus
    if first_bytes.startswith(b"\xd0\xcf\x11\xe0"):
        return "doc"  # OLE2 compound (legacy binary Word)
    if first_bytes.startswith(b"\xff\x57\x50\x43"):
        return "wpd"  # WordPerfect
    if first_bytes.startswith(b"%PDF"):
        return "pdf"
    head = first_bytes.lstrip()[:512].lower()
    if head.startswith(b"<") or b"<html" in head:
        return "html"
    return "unknown"


# Extension to write for a sniffed format when saving the original file.
FORMAT_EXT = {"docx": "docx", "doc": "doc", "wpd": "wpd", "pdf": "pdf"}


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def ensure_ssl_cert(url: str) -> str:
    """Append sslrootcert to DATABASE_URL if not already present (Azure PG)."""
    if "sslrootcert" in url:
        return url
    for path in SSL_CERT_PATHS:
        if os.path.exists(path):
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}sslrootcert={path}"
    return url


def get_conn() -> psycopg.Connection:
    """Open a short-lived connection using DATABASE_URL from the environment.

    Loads the repo-root .env if present (same as the harvest scripts). Callers
    own the connection lifecycle: open, do a batch of work, close — never hold a
    connection across a network fetch.
    """
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required (set it in .env or the environment)")
    return psycopg.connect(ensure_ssl_cert(database_url), autocommit=False)


# ---------------------------------------------------------------------------
# Ledger upsert
# ---------------------------------------------------------------------------

# Columns a caller may set on digitallibrary.document_files. The PK
# (symbol_normalized, lang) is handled separately; updated_at is always now().
_LEDGER_COLUMNS = (
    "format",
    "content_type",
    "size_bytes",
    "sha256",
    "ods_url",
    "archive_path",
    "converted_path",
    "converter",
    "status",
    "error",
    "fetched_at",
    "source_symbol",  # volume-split children: parent volume/report symbol (migration 005)
)


def upsert_document_file(
    conn: psycopg.Connection,
    symbol_normalized: str,
    lang: str = "en",
    **fields: object,
) -> None:
    """Insert or update one ledger row.

    Only the columns passed in **fields are written; on conflict they overwrite
    the existing values (so a convert pass can update converted_path/status
    without clobbering the fetch columns). `status` is required.

    Unknown field names raise, to catch typos early.
    """
    unknown = set(fields) - set(_LEDGER_COLUMNS)
    if unknown:
        raise ValueError(f"upsert_document_file: unknown columns {sorted(unknown)}")
    if "status" not in fields:
        raise ValueError("upsert_document_file: 'status' is required")

    cols = [c for c in _LEDGER_COLUMNS if c in fields]
    insert_cols = ["symbol_normalized", "lang", *cols, "updated_at"]
    placeholders = ["%s", "%s", *["%s"] * len(cols), "now()"]
    values: list[object] = [symbol_normalized, lang, *[fields[c] for c in cols]]

    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    set_clause = f"{set_clause}, updated_at = now()" if set_clause else "updated_at = now()"

    sql = (
        f"INSERT INTO digitallibrary.document_files ({', '.join(insert_cols)}) "
        f"VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT (symbol_normalized, lang) DO UPDATE SET {set_clause}"
    )
    with conn.cursor() as cur:
        cur.execute(sql, values)


# ---------------------------------------------------------------------------
# Pipeline state (digitallibrary.harvest_state)
# ---------------------------------------------------------------------------

def read_state(conn: psycopg.Connection, key: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM digitallibrary.harvest_state WHERE key = %s",
            [key],
        )
        row = cur.fetchone()
        return row[0] if row else {}


def write_state(conn: psycopg.Connection, key: str, state: dict) -> None:
    state = {**state, "updated_at": utc_now()}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO digitallibrary.harvest_state (key, value, updated_at)
               VALUES (%s, %s::jsonb, NOW())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
            [key, json.dumps(state)],
        )
    conn.commit()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
