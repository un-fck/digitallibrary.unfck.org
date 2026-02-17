#!/usr/bin/env python3
"""Fix mojibake in existing records caused by Latin-1 misinterpretation of UTF-8.

Reverses the damage: encode garbled text back to Latin-1 bytes, then decode as UTF-8.
Only touches records where the fix actually changes something.

Usage:
    uv run python python/fix_encoding.py --dry-run     # preview what would change
    uv run python python/fix_encoding.py                # apply fixes
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg
from dotenv import load_dotenv

SSL_CERT_PATHS = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]
BATCH_SIZE = 2000


def ensure_ssl_cert(url: str) -> str:
    if "sslrootcert" in url:
        return url
    for path in SSL_CERT_PATHS:
        if os.path.exists(path):
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}sslrootcert={path}"
    return url


def fix_text(s: str | None) -> str | None:
    """Attempt to reverse Latin-1 mojibake. Returns original if not fixable."""
    if not s:
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        # Only return fixed version if it actually changed and looks valid
        if fixed != s:
            return fixed
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return s


def fix_text_list(items: list[str] | None) -> list[str] | None:
    if not items:
        return items
    return [fix_text(s) or s for s in items]


def fix_files(files: list[dict] | None) -> list[dict] | None:
    if not files:
        return files
    fixed = []
    for f in files:
        entry = dict(f)
        entry["lang"] = fix_text(entry.get("lang"))
        fixed.append(entry)
    return fixed


def fix_corporate_authors(authors: list[dict] | None) -> list[dict] | None:
    if not authors:
        return authors
    fixed = []
    for a in authors:
        entry = dict(a)
        entry["name"] = fix_text(entry.get("name")) or entry.get("name")
        entry["type"] = fix_text(entry.get("type"))
        fixed.append(entry)
    return fixed


def fix_agenda_items(items: list[dict] | None) -> list[dict] | None:
    if not items:
        return items
    fixed = []
    for a in items:
        entry = dict(a)
        for key in ("doc", "item", "desc", "topic"):
            entry[key] = fix_text(entry.get(key))
        fixed.append(entry)
    return fixed


def fix_related_documents(docs: list[dict] | None) -> list[dict] | None:
    if not docs:
        return docs
    fixed = []
    for d in docs:
        entry = dict(d)
        entry["symbol"] = fix_text(entry.get("symbol")) or entry.get("symbol")
        entry["relationship"] = fix_text(entry.get("relationship"))
        fixed.append(entry)
    return fixed


# Text columns to fix
TEXT_COLS = [
    "document_symbol", "symbol_body", "symbol_session", "symbol_committee",
    "title", "title_statement", "date_text", "publisher", "pub_place",
    "physical_desc", "doc_class_code", "doc_class_desc",
    "un_body", "un_committee", "summary", "resource_type", "resource_subtype",
    "vote_summary", "marcxml",
]

# Array text columns
ARRAY_COLS = ["languages", "subjects", "notes", "collections"]

# JSONB columns with special handlers
JSONB_FIXERS = {
    "files": fix_files,
    "corporate_authors": fix_corporate_authors,
    "agenda_items": fix_agenda_items,
    "related_documents": fix_related_documents,
}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Fix encoding mojibake in DB records")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N records (0 = all)")
    parser.add_argument("--skip", type=int, default=0, help="Skip first N records (for resuming)")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2

    db_url = ensure_ssl_cert(database_url)
    # Use separate connections for read (server cursor) and write (commits).
    # Committing on a connection destroys its open server-side cursors.
    read_conn = psycopg.connect(db_url, autocommit=False)
    write_conn = psycopg.connect(db_url, autocommit=False) if not args.dry_run else None

    # Build column list for SELECT
    all_cols = ["recid"] + TEXT_COLS + ARRAY_COLS + list(JSONB_FIXERS.keys())
    select_sql = f"SELECT {', '.join(all_cols)} FROM digitallibrary.documents ORDER BY recid"
    if args.skip:
        select_sql += f" OFFSET {args.skip}"
    if args.limit:
        select_sql += f" LIMIT {args.limit}"

    total_fixed = 0
    total_scanned = 0

    try:
        with read_conn.cursor(name="fix_encoding_cursor") as read_cur:
            read_cur.itersize = BATCH_SIZE
            read_cur.execute(select_sql)

            batch_updates: list[tuple] = []

            for row in read_cur:
                total_scanned += 1
                rec = dict(zip(all_cols, row))
                updates: dict[str, object] = {}

                # Fix text columns
                for col in TEXT_COLS:
                    original = rec[col]
                    fixed = fix_text(original)
                    if fixed != original:
                        updates[col] = fixed

                # Fix array columns
                for col in ARRAY_COLS:
                    original = rec[col]
                    fixed = fix_text_list(original)
                    if fixed != original:
                        updates[col] = fixed

                # Fix JSONB columns
                for col, fixer in JSONB_FIXERS.items():
                    original = rec[col]
                    fixed = fixer(original)
                    if fixed != original:
                        updates[col] = json.dumps(fixed, ensure_ascii=False) if fixed else None

                if not updates:
                    continue

                total_fixed += 1

                if args.dry_run:
                    changed_cols = list(updates.keys())
                    # Show what changed (exclude marcxml for brevity)
                    sample_cols = [c for c in changed_cols if c != "marcxml"]
                    print(f"  recid {rec['recid']}: would fix {len(changed_cols)} columns: {', '.join(sample_cols)}")
                    for col in sample_cols:
                        orig = rec[col]
                        fixed_val = updates[col]
                        if isinstance(orig, str):
                            orig_preview = orig[:120]
                            fixed_preview = str(fixed_val)[:120]
                        elif isinstance(orig, list):
                            orig_preview = str(orig)[:120]
                            fixed_preview = str(fixed_val)[:120]
                        else:
                            orig_preview = str(orig)[:120]
                            fixed_preview = str(fixed_val)[:120]
                        print(f"    {col}:")
                        print(f"      before: {orig_preview!r}")
                        print(f"      after:  {fixed_preview!r}")
                else:
                    # Build UPDATE for this record
                    set_parts = []
                    values = []
                    for col, val in updates.items():
                        if col in JSONB_FIXERS:
                            set_parts.append(f"{col} = %s::jsonb")
                        else:
                            set_parts.append(f"{col} = %s")
                        values.append(val)
                    values.append(rec["recid"])
                    batch_updates.append((
                        f"UPDATE digitallibrary.documents SET {', '.join(set_parts)} WHERE recid = %s",
                        tuple(values),
                    ))

                    if len(batch_updates) >= BATCH_SIZE:
                        with write_conn.pipeline():
                            with write_conn.cursor() as write_cur:
                                for sql, params in batch_updates:
                                    write_cur.execute(sql, params)
                        write_conn.commit()
                        print(f"  Committed {total_fixed} fixes ({total_scanned} scanned)...")
                        batch_updates = []

            # Flush remaining
            if batch_updates and write_conn:
                with write_conn.pipeline():
                    with write_conn.cursor() as write_cur:
                        for sql, params in batch_updates:
                            write_cur.execute(sql, params)
                write_conn.commit()

    finally:
        read_conn.close()
        if write_conn:
            write_conn.close()

    action = "Would fix" if args.dry_run else "Fixed"
    print(f"\n{action} {total_fixed} of {total_scanned} records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
