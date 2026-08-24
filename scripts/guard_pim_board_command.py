#!/usr/bin/env python3
"""Block issue #108's direct board-mutating commands in Claude Bash calls."""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import PurePosixPath
from typing import Iterator, Optional


PYTHON = re.compile(r"^python(?:3(?:\.\d+)?)?$")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HARDWARE_RUNNERS = {
    "run_mixed_combo_verify.py",
    "run_comprehensive_verify.py",
    "run_bps_quick.py",
}
SHELL_BREAKS = set(";&|")


def _segments(command: str) -> Iterator[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    segment: list[str] = []
    for token in lexer:
        if token and set(token) <= SHELL_BREAKS:
            if segment:
                yield segment
                segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def _basename(token: str) -> str:
    return PurePosixPath(token).name


def _command_index(tokens: list[str]) -> Optional[int]:
    index = 0
    if tokens and _basename(tokens[0]) == "env":
        index = 1
    while index < len(tokens) and ASSIGNMENT.match(tokens[index]):
        index += 1
    return index if index < len(tokens) else None


def _python_script(tokens: list[str], command_index: int) -> tuple[Optional[str], list[str]]:
    index = command_index + 1
    while index < len(tokens) and tokens[index].startswith("-"):
        if tokens[index] == "-c":
            return None, []
        if tokens[index] == "-m":
            if index + 1 < len(tokens) and tokens[index + 1] == "pim_check":
                return "pim_check.py", tokens[index + 2 :]
            return None, []
        index += 1
    if index >= len(tokens):
        return None, []
    return _basename(tokens[index]), tokens[index + 1 :]


def _segment_is_blocked(tokens: list[str]) -> bool:
    command_index = _command_index(tokens)
    if command_index is None:
        return False
    executable = _basename(tokens[command_index])
    if executable == "with_pim_board.sh":
        return False
    if executable in HARDWARE_RUNNERS:
        return True
    if executable in {"pim_check.py", "pim-check"}:
        return any(arg == "--plan" or arg.startswith("--plan=") for arg in tokens[command_index + 1 :])
    if not PYTHON.match(executable):
        return False
    script, arguments = _python_script(tokens, command_index)
    if script in HARDWARE_RUNNERS:
        return True
    return script == "pim_check.py" and any(
        arg == "--plan" or arg.startswith("--plan=") for arg in arguments
    )


def command_is_blocked(command: str) -> bool:
    try:
        return any(_segment_is_blocked(segment) for segment in _segments(command))
    except ValueError:
        return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError("command must be a string")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"PIM board guard: invalid hook input: {exc}", file=sys.stderr)
        return 2

    if command_is_blocked(command):
        print(
            "PIM board guard: run this command through scripts/with_pim_board.sh "
            "with --for/--until and --purpose; direct board plan execution is blocked.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
