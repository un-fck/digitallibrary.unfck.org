#!/usr/bin/env python3
"""Top-up orchestrator for the document full-text pipeline (Track A).

Thin wrapper that runs the post-fetch stages in order, each as its own
`uv run python python/<stage>.py` subprocess:

    convert       fulltext_convert.py       doc/wpd -> docx   (converted)
    extract_raw   fulltext_extract_raw.py   docx -> raw rows  (extracted)
    parse         fulltext_parse.py --to-db raw -> semantic DB (parsed)

This is what a cron job or a manual top-up calls AFTER a fetch batch lands new
`fetched` rows (fetch is deliberately NOT part of this orchestrator: it is slow,
soft-block-sensitive, and run detached — see docs/fulltexts.md). Each stage is
idempotent and only picks up rows in the right status, so re-running is safe.

`--limit N` is forwarded to every stage (handy for a smoke test). Extra tuning
flags are passed through: `--workers N` to convert, `--force` to extract+parse
re-runs. Prints a per-stage summary table and exits non-zero if ANY stage fails
(so a cron wrapper can alert). Stages run in order and a failing stage aborts the
rest — a broken convert should not feed a half-empty extract.

Usage:
    uv run python python/fulltext_pipeline.py                 # full top-up cycle
    uv run python python/fulltext_pipeline.py --limit 20      # smoke test
    uv run python python/fulltext_pipeline.py --workers 8
"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (label, script, extra fixed args). --limit / passthrough flags are appended.
STAGES = [
    ("convert", "python/fulltext_convert.py", []),
    ("extract_raw", "python/fulltext_extract_raw.py", []),
    ("parse", "python/fulltext_parse.py", ["--to-db"]),
]


def run_stage(label: str, script: str, extra: list[str]) -> tuple[bool, float]:
    cmd = ["uv", "run", "python", script, *extra]
    print(f"\n=== stage: {label} ===\n$ {' '.join(cmd)}", flush=True)
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    dt = time.monotonic() - t0
    return proc.returncode == 0, dt


def main() -> int:
    ap = argparse.ArgumentParser(description="Full-text pipeline top-up orchestrator")
    ap.add_argument("--limit", type=int, help="forward --limit N to every stage")
    ap.add_argument("--workers", type=int, help="forward --workers N to the convert stage")
    ap.add_argument("--force", action="store_true",
                    help="forward --force to extract_raw (re-extract already-'extracted' rows); "
                         "parse always re-parses extracted+parsed docs, so it needs no --force")
    args = ap.parse_args()

    results: list[tuple[str, bool, float]] = []
    overall_ok = True
    for label, script, extra in STAGES:
        stage_args = list(extra)
        if args.limit is not None:
            stage_args += ["--limit", str(args.limit)]
        if args.workers is not None and label == "convert":
            stage_args += ["--workers", str(args.workers)]
        if args.force and label == "extract_raw":
            stage_args += ["--force"]
        ok, dt = run_stage(label, script, stage_args)
        results.append((label, ok, dt))
        if not ok:
            overall_ok = False
            print(f"\n!! stage '{label}' failed (exit != 0) — aborting remaining stages.")
            break

    print("\n=== pipeline summary ===")
    print(f"{'stage':<14} {'result':<8} {'seconds':>9}")
    print(f"{'-' * 14} {'-' * 8} {'-' * 9}")
    for label, ok, dt in results:
        print(f"{label:<14} {'ok' if ok else 'FAILED':<8} {dt:>9.1f}")
    for label, _, _ in STAGES:
        if label not in {r[0] for r in results}:
            print(f"{label:<14} {'skipped':<8} {'-':>9}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
