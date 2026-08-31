#!/usr/bin/env python3
"""Reject workflow IPv4 literals outside the canonical hardware target."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional, Sequence


IPV4_LITERAL = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
CANONICAL_WORKFLOW = "hw-evidence-measure.yml"
CANONICAL_TARGET = "192.168.214.4"
CANONICAL_ARGUMENT = re.compile(
    rf"(?:^|\s)--target-host\s+(?P<host>{re.escape(CANONICAL_TARGET)})(?:\s|$)"
)


def _allowed_spans(path: Path, line: str) -> set[tuple[int, int]]:
    if path.name != CANONICAL_WORKFLOW:
        return set()
    return {match.span("host") for match in CANONICAL_ARGUMENT.finditer(line)}


def find_offenders(workflow_dir: Path) -> list[str]:
    """Return workflow lines containing disallowed IPv4 literals."""
    offenders = []
    paths = sorted(
        path
        for path in workflow_dir.iterdir()
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    for path in paths:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            allowed_spans = _allowed_spans(path, line)
            if any(match.span() not in allowed_spans for match in IPV4_LITERAL.finditer(line)):
                relative_path = path.relative_to(workflow_dir).as_posix()
                offenders.append(f"{relative_path}:{line_number}:{line.strip()}")
    return offenders


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow-dir",
        type=Path,
        default=Path(".github/workflows"),
        help="directory containing GitHub Actions workflow YAML files",
    )
    args = parser.parse_args(argv)

    if not args.workflow_dir.is_dir():
        parser.error(f"workflow directory does not exist: {args.workflow_dir}")

    offenders = find_offenders(args.workflow_dir)
    if offenders:
        print(
            "Hard-coded IPv4 addresses found; use repository variables except "
            "the canonical hardware-evidence target:"
        )
        print("\n".join(offenders))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
