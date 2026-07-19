#!/usr/bin/env python3
"""Convert legacy fetched files (doc/wpd) to .docx via LibreOffice headless.

Stage 2 of the full-text pipeline (Track A). Native .docx files fetched in
stage 1 need no work and are flagged 'converted' in bulk. Legacy binary .doc
and WordPerfect .wpd files are converted to .docx with `soffice --headless`,
verified as valid zips, and recorded in digitallibrary.document_files.

Parallel soffice instances share a single user profile by default and will
deadlock; each worker therefore gets its own `-env:UserInstallation` profile.

Usage:
    uv run python python/fulltext_convert.py                     # 4 workers, doc,wpd
    uv run python python/fulltext_convert.py --workers 8 --limit 500
    uv run python python/fulltext_convert.py --formats doc
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from fulltext_common import ARCHIVE_ROOT, get_conn, sniff_format, upsert_document_file

CONVERTED_SUBDIR = "converted"
SUBPROCESS_TIMEOUT = 180  # seconds per file


# ---------------------------------------------------------------------------
# LibreOffice discovery
# ---------------------------------------------------------------------------

def find_soffice() -> str:
    """Locate the soffice binary (env override, PATH, then the macOS bundle)."""
    env = os.getenv("SOFFICE_BIN")
    if env and Path(env).exists():
        return env
    which = shutil.which("soffice")
    if which:
        return which
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if Path(mac).exists():
        return mac
    raise RuntimeError("soffice not found — set SOFFICE_BIN or install LibreOffice")


def soffice_version(soffice: str) -> str:
    """Return e.g. 'libreoffice 25.2' from `soffice --version`."""
    try:
        out = subprocess.run(
            [soffice, "--version"], capture_output=True, text=True, timeout=60
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return "libreoffice unknown"
    m = re.search(r"(\d+\.\d+)", out)
    return f"libreoffice {m.group(1)}" if m else "libreoffice unknown"


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------

def flag_native_docx(conn) -> int:
    """Native docx rows are ready as-is: mark 'converted', converted_path NULL."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE digitallibrary.document_files
            SET status = 'converted', converted_path = NULL, updated_at = now()
            WHERE format = 'docx' AND status = 'fetched'
            """
        )
        n = cur.rowcount
    conn.commit()
    return n


def rows_to_convert(conn, formats: list[str], limit: int | None) -> list[tuple[str, str, str]]:
    """Return [(symbol_normalized, lang, archive_path)] needing conversion."""
    sql = (
        "SELECT symbol_normalized, lang, archive_path "
        "FROM digitallibrary.document_files "
        "WHERE status = 'fetched' AND format = ANY(%s) "
        "AND converted_path IS NULL AND archive_path IS NOT NULL "
        "ORDER BY symbol_normalized"
    )
    params: list[object] = [formats]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def convert_one(soffice: str, profile: Path, src: Path, outdir: Path) -> Path:
    """Run soffice to convert `src` to .docx in `outdir`. Returns the expected
    output path. Raises on non-zero exit or timeout."""
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        soffice, "--headless",
        f"-env:UserInstallation=file://{profile}",
        "--convert-to", "docx",
        "--outdir", str(outdir),
        str(src),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"soffice exit {proc.returncode}: {proc.stderr.strip()[:300]}")
    return outdir / f"{src.stem}.docx"


def is_valid_docx(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as fh:
        return sniff_format(fh.read(8)) == "docx"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_counts = {"converted": 0, "failed": 0, "done": 0}


def _bump(key: str, total: int) -> None:
    with _lock:
        _counts[key] += 1
        _counts["done"] += 1
        done = _counts["done"]
    if done % 25 == 0 or done == total:
        print(f"converted {done}/{total} ok={_counts['converted']} "
              f"failed={_counts['failed']}")


def worker(
    worker_id: int,
    rows: list[tuple[str, str, str]],
    soffice: str,
    converter: str,
    total: int,
) -> None:
    profile = Path(f"/tmp/lo_profile_{worker_id}")
    outdir = ARCHIVE_ROOT / CONVERTED_SUBDIR
    for symbol_normalized, lang, archive_path in rows:
        src = ARCHIVE_ROOT / archive_path
        rel_out = f"{CONVERTED_SUBDIR}/{src.stem}.docx"
        try:
            if not src.exists():
                raise FileNotFoundError(f"original missing: {archive_path}")
            out = convert_one(soffice, profile, src, outdir)
            if not is_valid_docx(out):
                raise RuntimeError("output missing or not a valid docx zip")
            status, error, converted_path = "converted", None, rel_out
        except Exception as exc:  # never let one bad file crash the run
            status, error, converted_path = "convert_failed", str(exc)[:500], None

        with get_conn() as conn:
            upsert_document_file(
                conn, symbol_normalized, lang,
                status=status, error=error,
                converted_path=converted_path, converter=converter,
            )
            conn.commit()
        _bump("converted" if status == "converted" else "failed", total)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert doc/wpd -> docx via LibreOffice")
    p.add_argument("--workers", type=int, default=4, help="parallel soffice workers (default 4)")
    p.add_argument("--limit", type=int, help="convert at most N files")
    p.add_argument("--formats", default="doc,wpd",
                   help="comma-separated formats to convert (default 'doc,wpd')")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    soffice = find_soffice()
    converter = soffice_version(soffice)
    print(f"Using {soffice} ({converter})")

    with get_conn() as conn:
        flagged = flag_native_docx(conn)
        rows = rows_to_convert(conn, formats, args.limit)
    print(f"Native docx flagged ready: {flagged} | to convert ({','.join(formats)}): {len(rows)}")

    if not rows:
        return 0

    total = len(rows)
    n_workers = max(1, min(args.workers, total))
    # Round-robin partition so each worker owns a distinct profile end-to-end.
    partitions: list[list[tuple[str, str, str]]] = [[] for _ in range(n_workers)]
    for idx, row in enumerate(rows):
        partitions[idx % n_workers].append(row)

    threads = [
        threading.Thread(target=worker, args=(wid, part, soffice, converter, total))
        for wid, part in enumerate(partitions)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"\nDone. converted={_counts['converted']} failed={_counts['failed']} of {total}")
    return 0 if _counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
