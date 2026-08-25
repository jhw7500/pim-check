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
    "run_smart_verify.py",
    "run_channel_verify.py",
    "run_failed_retry.py",
    "test_vflip_frame_compare.sh",
}
SHELL_BREAKS = set(";&|(){}\n")
ENV_SHORT_OPTIONS = {"i", "0", "v"}
ENV_SHORT_OPTIONS_WITH_VALUE = {"u", "C", "S"}
ENV_LONG_OPTIONS = {
    "--ignore-environment",
    "--null",
    "--debug",
    "--list-signal-handling",
}
ENV_LONG_OPTIONS_WITH_VALUE = {"--unset", "--chdir", "--split-string"}
ENV_LONG_OPTIONS_WITH_OPTIONAL_VALUE = {
    "--block-signal",
    "--default-signal",
    "--ignore-signal",
}
EXEC_SHORT_OPTIONS = {"c", "l"}
PYTHON_OPTIONS_WITH_VALUE = {"-W", "-X", "--check-hash-based-pycs"}
TIMEOUT_OPTIONS_WITH_VALUE = {"-k", "--kill-after", "-s", "--signal"}
TIMEOUT_OPTIONS = {"--foreground", "--preserve-status", "--verbose", "-v"}
SHELLS = {"bash", "dash", "sh", "zsh"}
SHELL_OPTIONS_WITH_VALUE = {"-O", "+O", "-o", "+o", "--init-file", "--rcfile"}
MAX_LAUNCHER_DEPTH = 8
SHELL_COMMAND_PREFIXES = {"if", "then", "elif", "else", "while", "until", "do", "!"}
SHELL_CLOSING_WORDS = {"fi", "done", "esac"}
UNSUPPORTED_SHELL_COMPOUNDS = {"for", "select", "case", "function", "coproc", "time"}
REMEDIATION = (
    "run this command through scripts/with_pim_board.sh with --for/--until "
    "and --purpose"
)


def _segments(command: str) -> Iterator[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|(){}\n")
    lexer.whitespace = " \t\r"
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


def _dollar_substitution(command: str, start: int) -> tuple[str, int]:
    depth = 1
    quote: Optional[str] = None
    escaped = False
    index = start
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return command[start:index], index + 1
        index += 1
    raise ValueError("unterminated shell command substitution")


def _backtick_substitution(command: str, start: int) -> tuple[str, int]:
    escaped = False
    index = start
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "`":
            return command[start:index], index + 1
        index += 1
    raise ValueError("unterminated shell command substitution")


def _shell_substitutions(command: str) -> Iterator[str]:
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
        elif char == "\\" and quote != "'":
            escaped = True
            index += 1
        elif quote == "'":
            if char == "'":
                quote = None
            index += 1
        elif char == "'" and quote is None:
            quote = "'"
            index += 1
        elif char == '"':
            quote = None if quote == '"' else '"'
            index += 1
        elif command.startswith("$(", index):
            substitution, index = _dollar_substitution(command, index + 2)
            yield substitution
        elif char == "`":
            substitution, index = _backtick_substitution(command, index + 1)
            yield substitution
        else:
            index += 1


def _basename(token: str) -> str:
    return PurePosixPath(token).name


def _expand_env_split_string(
    tokens: list[str], index: int, operand: str, consumed: int
) -> int:
    expanded = shlex.split(operand)
    if not expanded:
        raise ValueError("env split-string operand is empty")
    tokens[index : index + consumed] = expanded
    return index


def _skip_env_short_options(tokens: list[str], index: int) -> int:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in ENV_SHORT_OPTIONS:
            position += 1
            continue
        if option not in ENV_SHORT_OPTIONS_WITH_VALUE:
            raise ValueError(f"unsupported env option: -{option}")
        attached = cluster[position + 1 :]
        if attached:
            operand = attached
            consumed = 1
        else:
            if index + 1 >= len(tokens):
                raise ValueError(f"env -{option} requires an operand")
            operand = tokens[index + 1]
            consumed = 2
        if option == "S":
            return _expand_env_split_string(tokens, index, operand, consumed)
        return index + consumed
    return index + 1


def _skip_env_options(tokens: list[str], index: int) -> int:
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        if token == "-":
            index += 1
            continue
        if not token.startswith("-"):
            return index
        if not token.startswith("--"):
            index = _skip_env_short_options(tokens, index)
            continue
        if token in {"--help", "--version"}:
            return len(tokens)
        if token in ENV_LONG_OPTIONS:
            index += 1
            continue
        option, separator, attached = token.partition("=")
        if option in ENV_LONG_OPTIONS_WITH_VALUE:
            if separator:
                operand = attached
                consumed = 1
            else:
                if index + 1 >= len(tokens):
                    raise ValueError(f"{option} requires an operand")
                operand = tokens[index + 1]
                consumed = 2
            if option == "--split-string":
                index = _expand_env_split_string(
                    tokens, index, operand, consumed
                )
            else:
                index += consumed
            continue
        if option in ENV_LONG_OPTIONS_WITH_OPTIONAL_VALUE:
            index += 1
            continue
        raise ValueError(f"unsupported env option: {token}")
    return index


def _skip_command_options(tokens: list[str], index: int) -> Optional[int]:
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        if not token.startswith("-") or token == "-":
            return index
        if token in {"-v", "-V"}:
            return None
        index += 1
    return index


def _command_index(tokens: list[str]) -> Optional[int]:
    index = 0
    allow_assignments = True
    while index < len(tokens):
        if allow_assignments:
            while index < len(tokens) and ASSIGNMENT.match(tokens[index]):
                index += 1
        if index >= len(tokens):
            return None
        executable = _basename(tokens[index])
        if executable == "env":
            index = _skip_env_options(tokens, index + 1)
            allow_assignments = True
            continue
        if executable == "command":
            command_index = _skip_command_options(tokens, index + 1)
            if command_index is None:
                return None
            index = command_index
            allow_assignments = False
            continue
        return index
    return None


def _python_script(tokens: list[str], command_index: int) -> tuple[Optional[str], list[str]]:
    index = command_index + 1
    while index < len(tokens) and tokens[index].startswith("-"):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-c":
            if index + 1 >= len(tokens):
                raise ValueError("-c requires a command operand")
            return None, []
        if token == "-m" or token.startswith("-m"):
            if token == "-m":
                if index + 1 >= len(tokens):
                    raise ValueError("-m requires a module operand")
                module = tokens[index + 1]
                arguments = tokens[index + 2 :]
            else:
                module = token[2:]
                arguments = tokens[index + 1 :]
            if module == "pim_check":
                return "pim_check.py", arguments
            return None, []
        if token in PYTHON_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} requires an operand")
            index += 2
            continue
        index += 1
    if index >= len(tokens):
        return None, []
    return _basename(tokens[index]), tokens[index + 1 :]


def _skip_exec_short_options(
    tokens: list[str], index: int
) -> tuple[int, bool]:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in EXEC_SHORT_OPTIONS:
            position += 1
            continue
        if option != "a":
            raise ValueError(f"unsupported exec option: -{option}")
        attached = cluster[position + 1 :]
        if attached:
            return index + 1, True
        if index + 1 >= len(tokens):
            raise ValueError("exec -a requires a name")
        return index + 2, True
    return index + 1, False


def _exec_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    name_override = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token.startswith("--"):
            raise ValueError(f"unsupported exec option: {token}")
        index, used_name_override = _skip_exec_short_options(tokens, index)
        name_override = name_override or used_name_override
    if index >= len(tokens):
        if name_override:
            raise ValueError("exec -a requires a command")
        return None
    return index


def _timeout_command_index(tokens: list[str], command_index: int) -> int:
    index = command_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-") or token == "-":
            break
        if token in TIMEOUT_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} requires an operand")
            index += 2
            continue
        if any(
            token.startswith(f"{option}=")
            for option in ("--kill-after", "--signal")
        ):
            if not token.partition("=")[2]:
                raise ValueError(f"{token.partition('=')[0]} requires an operand")
            index += 1
            continue
        if token.startswith(("-k", "-s")) and len(token) > 2:
            index += 1
            continue
        if token not in TIMEOUT_OPTIONS:
            raise ValueError(f"unsupported timeout option: {token}")
        index += 1
    if index >= len(tokens):
        raise ValueError("timeout requires a duration")
    index += 1
    if index >= len(tokens):
        raise ValueError("timeout requires a command")
    return index


def _nohup_command_index(tokens: list[str], command_index: int) -> Optional[int]:
    index = command_index + 1
    if index < len(tokens) and tokens[index] == "--":
        index += 1
    elif index < len(tokens) and tokens[index] in {"--help", "--version"}:
        return None
    elif index < len(tokens) and tokens[index].startswith("-"):
        raise ValueError(f"unsupported nohup option: {tokens[index]}")
    if index >= len(tokens):
        raise ValueError("nohup requires a command")
    return index


def _shell_child(
    tokens: list[str], command_index: int
) -> tuple[Optional[str], Optional[str]]:
    index = command_index + 1
    reads_stdin = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if not token.startswith(("-", "+")):
            break
        if token == "-c" or (
            token.startswith("-")
            and not token.startswith("--")
            and "c" in token[1:]
        ):
            if index + 1 >= len(tokens):
                raise ValueError(f"{_basename(tokens[command_index])} -c requires a command")
            return tokens[index + 1], None
        if token.startswith("-") and not token.startswith("--") and "s" in token[1:]:
            reads_stdin = True
        if token in SHELL_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} requires an operand")
            index += 2
            continue
        index += 1
    if reads_stdin or index >= len(tokens):
        return None, None
    return None, _basename(tokens[index])


def _shell_command_tokens(tokens: list[str]) -> list[str]:
    while tokens and tokens[0] in SHELL_COMMAND_PREFIXES:
        tokens = tokens[1:]
    if not tokens:
        return []
    if tokens[0] in SHELL_CLOSING_WORDS:
        if len(tokens) > 1:
            raise ValueError(f"unexpected tokens after {tokens[0]}")
        return []
    if tokens[0] in UNSUPPORTED_SHELL_COMPOUNDS:
        raise ValueError(f"unsupported shell compound syntax: {tokens[0]}")
    return tokens


def _segment_is_blocked(tokens: list[str], depth: int = 0) -> bool:
    if depth > MAX_LAUNCHER_DEPTH:
        raise ValueError("launcher nesting is too deep")
    tokens = _shell_command_tokens(tokens)
    if not tokens:
        return False
    command_index = _command_index(tokens)
    if command_index is None:
        return False
    executable = _basename(tokens[command_index])
    if executable == "with_pim_board.sh":
        return False
    if executable == "exec":
        child_index = _exec_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1
        )
    if executable == "timeout":
        child_index = _timeout_command_index(tokens, command_index)
        return _segment_is_blocked(tokens[child_index:], depth + 1)
    if executable == "nohup":
        child_index = _nohup_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1
        )
    if executable in SHELLS:
        child_command, child_script = _shell_child(tokens, command_index)
        if child_command is not None:
            return _command_is_blocked(child_command, depth + 1)
        return child_script in HARDWARE_RUNNERS
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


def _command_is_blocked(command: str, depth: int = 0) -> bool:
    if depth > MAX_LAUNCHER_DEPTH:
        raise ValueError("launcher nesting is too deep")
    if any(
        _command_is_blocked(substitution, depth + 1)
        for substitution in _shell_substitutions(command)
    ):
        return True
    return any(
        _segment_is_blocked(segment, depth) for segment in _segments(command)
    )


def command_is_blocked(command: str) -> bool:
    try:
        return _command_is_blocked(command)
    except ValueError:
        return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError("command must be a string")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(
            f"PIM board guard: invalid hook input: {exc}; {REMEDIATION}.",
            file=sys.stderr,
        )
        return 2

    if command_is_blocked(command):
        print(
            f"PIM board guard: {REMEDIATION}; "
            "direct board plan execution is blocked.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
