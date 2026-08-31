#!/usr/bin/env python3
"""Reject staged root-level runtime result JSON artifacts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import PurePosixPath


def _staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git diff exited with {result.returncode}")
    return [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]


def _is_runtime_result_artifact(path: str) -> bool:
    candidate = PurePosixPath(path)
    return len(candidate.parts) == 1 and (
        candidate.name.endswith("_results.json") or candidate.name == "temp_awb_retry.json"
    )


def main() -> int:
    try:
        staged_paths = _staged_paths()
    except RuntimeError as exc:
        print(f"Unable to inspect staged files: {exc}", file=sys.stderr)
        return 2

    offenders = sorted(path for path in staged_paths if _is_runtime_result_artifact(path))
    if not offenders:
        return 0

    print("Runtime result JSON artifacts must not be committed:", file=sys.stderr)
    for path in offenders:
        print(f"  - {path}", file=sys.stderr)
    print("Keep runtime output local; commit fixtures under tests/golden/ instead.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
