#!/usr/bin/env python3
"""Block issue #108's direct board-mutating commands in Claude Bash calls."""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path, PurePosixPath
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
FIND_PLACEHOLDER_EXECUTION_TARGETS = tuple(
    sorted(HARDWARE_RUNNERS | {"pim_check.py", "pim-check", "pim_check"})
)
SHELL_BREAKS = set(";&|(){}\n")
ESCAPED_SEMICOLON_MARKER = "\0"
FIND_EXEC_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir"}
FIND_EXEC_TERMINATORS = {";", "+"}
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
STDBUF_SHORT_OPTIONS = {"i", "o", "e"}
STDBUF_LONG_OPTIONS = {"--input", "--output", "--error"}
XARGS_SHORT_OPTIONS = {"0", "o", "p", "r", "t", "x"}
XARGS_SHORT_OPTIONS_WITH_VALUE = {"a", "d", "E", "I", "L", "n", "P", "s"}
XARGS_SHORT_OPTIONS_WITH_OPTIONAL_VALUE = {"e", "i", "l"}
XARGS_LONG_OPTIONS = {
    "--null",
    "--open-tty",
    "--interactive",
    "--no-run-if-empty",
    "--show-limits",
    "--verbose",
    "--exit",
}
XARGS_LONG_OPTIONS_WITH_VALUE = {
    "--arg-file",
    "--delimiter",
    "--max-lines",
    "--max-args",
    "--max-procs",
    "--max-chars",
    "--process-slot-var",
}
XARGS_LONG_OPTIONS_WITH_OPTIONAL_VALUE = {"--eof", "--replace"}
SETSID_SHORT_OPTIONS = {"c", "f", "w"}
SETSID_LONG_OPTIONS = {"--ctty", "--fork", "--wait"}
SETSID_TERMINAL_SHORT_OPTIONS = {"h", "V"}
SETSID_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
WATCH_SHORT_OPTIONS = {"b", "c", "e", "g", "p", "t", "w", "x"}
WATCH_LONG_OPTIONS = {
    "--beep",
    "--color",
    "--errexit",
    "--chgexit",
    "--precise",
    "--no-title",
    "--no-wrap",
    "--exec",
}
WATCH_TERMINAL_SHORT_OPTIONS = {"h", "v"}
WATCH_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
TASKSET_SHORT_OPTIONS = {"a", "c"}
TASKSET_LONG_OPTIONS = {"--all-tasks", "--cpu-list"}
TASKSET_TERMINAL_SHORT_OPTIONS = {"h", "V"}
TASKSET_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
CHRT_SHORT_OPTIONS = {"a", "b", "d", "f", "i", "o", "r", "R", "v"}
CHRT_SHORT_OPTIONS_WITH_VALUE = {"D", "P", "T"}
CHRT_LONG_OPTIONS = {
    "--all-tasks",
    "--batch",
    "--deadline",
    "--fifo",
    "--idle",
    "--other",
    "--reset-on-fork",
    "--rr",
    "--verbose",
}
CHRT_LONG_OPTIONS_WITH_VALUE = {
    "--sched-deadline",
    "--sched-period",
    "--sched-runtime",
}
CHRT_TERMINAL_SHORT_OPTIONS = {"h", "m", "V"}
CHRT_TERMINAL_LONG_OPTIONS = {"--help", "--max", "--version"}
IONICE_SHORT_OPTIONS = {"t"}
IONICE_SHORT_OPTIONS_WITH_VALUE = {"c", "n"}
IONICE_TARGET_SHORT_OPTIONS_WITH_VALUE = {"p", "P", "u"}
IONICE_LONG_OPTIONS = {"--ignore"}
IONICE_LONG_OPTIONS_WITH_VALUE = {"--class", "--classdata"}
IONICE_TARGET_LONG_OPTIONS_WITH_VALUE = {"--pid", "--pgid", "--uid"}
IONICE_TERMINAL_SHORT_OPTIONS = {"h", "V"}
IONICE_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
SCRIPT_SHORT_OPTIONS = {"a", "e", "f", "q"}
SCRIPT_SHORT_OPTIONS_WITH_VALUE = {"B", "E", "I", "O", "T", "m", "o"}
SCRIPT_LONG_OPTIONS = {
    "--append",
    "--flush",
    "--force",
    "--quiet",
    "--return",
}
SCRIPT_LONG_OPTIONS_WITH_VALUE = {
    "--echo",
    "--log-in",
    "--log-io",
    "--log-out",
    "--log-timing",
    "--logging-format",
    "--output-limit",
}
SCRIPT_TERMINAL_SHORT_OPTIONS = {"h", "V"}
SCRIPT_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
PRLIMIT_RESOURCE_SHORT_OPTIONS = set("cdefilmnqrstuvxy")
PRLIMIT_RESOURCE_LONG_OPTIONS = {
    "--as",
    "--core",
    "--cpu",
    "--data",
    "--fsize",
    "--locks",
    "--memlock",
    "--msgqueue",
    "--nice",
    "--nofile",
    "--nproc",
    "--rss",
    "--rtprio",
    "--rttime",
    "--sigpending",
    "--stack",
}
PRLIMIT_LONG_OPTIONS = {"--noheadings", "--raw", "--verbose"}
PRLIMIT_TERMINAL_SHORT_OPTIONS = {"h", "V"}
PRLIMIT_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
SUDO_SHORT_OPTIONS = {"A", "b", "B", "E", "H", "i", "k", "n", "P", "S", "s"}
SUDO_SHORT_OPTIONS_WITH_VALUE = {"C", "D", "g", "p", "R", "r", "t", "T", "U", "u"}
SUDO_TERMINAL_SHORT_OPTIONS = {"K", "l", "v", "V"}
SUDO_LONG_OPTIONS = {
    "--askpass",
    "--background",
    "--bell",
    "--login",
    "--non-interactive",
    "--preserve-env",
    "--preserve-groups",
    "--reset-timestamp",
    "--set-home",
    "--shell",
    "--stdin",
}
SUDO_LONG_OPTIONS_WITH_VALUE = {
    "--chdir",
    "--chroot",
    "--close-from",
    "--command-timeout",
    "--group",
    "--host",
    "--other-user",
    "--prompt",
    "--role",
    "--type",
    "--user",
}
SUDO_TERMINAL_LONG_OPTIONS = {
    "--help",
    "--list",
    "--remove-timestamp",
    "--validate",
    "--version",
}
CANONICAL_BOARD_WRAPPERS = {
    "scripts/with_pim_board.sh",
    "./scripts/with_pim_board.sh",
    Path(__file__).resolve().with_name("with_pim_board.sh").as_posix(),
}
SHELLS = {"bash", "dash", "sh", "zsh"}
SHELL_OPTIONS_WITH_VALUE = {"-O", "+O", "-o", "+o", "--init-file", "--rcfile"}
SHELL_TERMINAL_OPTIONS = {"--help", "--version"}
SHELL_STDIN_SCRIPTS = {"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
SHELL_REDIRECTION = re.compile(
    r"^(?:\d+|\{[A-Za-z_][A-Za-z0-9_]*\})?[<>]"
)
MAX_LAUNCHER_DEPTH = 8
SHELL_COMMAND_PREFIXES = {"if", "then", "elif", "else", "while", "until", "do", "!"}
SHELL_CLOSING_WORDS = {"fi", "done", "esac"}
UNSUPPORTED_SHELL_COMPOUNDS = {"for", "select", "case", "function", "coproc", "time"}
REMEDIATION = (
    "run this command through scripts/with_pim_board.sh with --for/--until "
    "and --purpose"
)


def _protect_escaped_semicolons(command: str) -> str:
    if ESCAPED_SEMICOLON_MARKER in command:
        raise ValueError("shell command contains a NUL byte")
    protected: list[str] = []
    quote: Optional[str] = None
    escaped = False
    for char in command:
        if escaped:
            if char == ";" and quote is None:
                protected.append(ESCAPED_SEMICOLON_MARKER)
            else:
                protected.extend(("\\", char))
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        else:
            if quote:
                if char == quote:
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            protected.append(char)
    if escaped:
        protected.append("\\")
    return "".join(protected)


def _segments(command: str) -> Iterator[list[str]]:
    command = _protect_escaped_semicolons(command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    segment: list[str] = []
    for token in lexer:
        if token and token != "{}" and set(token) <= SHELL_BREAKS:
            if segment:
                yield segment
                segment = []
        else:
            segment.append(token.replace(ESCAPED_SEMICOLON_MARKER, ";"))
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


def _normalize_ansi_c_quotes(command: str) -> str:
    normalized: list[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            normalized.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            normalized.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            normalized.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if command.startswith("$'", index):
            body: list[str] = []
            body_has_escape = False
            index += 2
            while index < len(command):
                char = command[index]
                if char == "\\":
                    body_has_escape = True
                    index += 2
                    continue
                if char == "'":
                    break
                body.append(char)
                index += 1
            if index >= len(command):
                raise ValueError("unterminated Bash ANSI-C quote")
            if body_has_escape:
                raise ValueError("Bash ANSI-C escape sequences are unsupported")
            normalized.append(shlex.quote("".join(body)))
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
        normalized.append(char)
        index += 1
    return "".join(normalized)


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


def _is_canonical_board_wrapper(token: str) -> bool:
    return token in CANONICAL_BOARD_WRAPPERS


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


def _nice_command_index(tokens: list[str], command_index: int) -> Optional[int]:
    index = command_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in {"--help", "--version"}:
            return None
        if token in {"-n", "--adjustment"}:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} requires an operand")
            adjustment = tokens[index + 1]
            if not re.fullmatch(r"[+-]?\d+", adjustment):
                raise ValueError(f"invalid nice adjustment: {adjustment}")
            index += 2
            continue
        if token.startswith("--adjustment="):
            adjustment = token.partition("=")[2]
        elif token.startswith("-n") and len(token) > 2:
            adjustment = token[2:]
        elif re.fullmatch(r"-\d+", token):
            index += 1
            continue
        else:
            raise ValueError(f"unsupported nice option: {token}")
        if not re.fullmatch(r"[+-]?\d+", adjustment):
            raise ValueError(f"invalid nice adjustment: {adjustment}")
        index += 1
    if index >= len(tokens):
        return None
    return index


def _stdbuf_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    saw_mode = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in {"--help", "--version"}:
            return None
        if token.startswith("--"):
            option, separator, mode = token.partition("=")
            if option not in STDBUF_LONG_OPTIONS:
                raise ValueError(f"unsupported stdbuf option: {token}")
            if not separator:
                if index + 1 >= len(tokens):
                    raise ValueError(f"{option} requires an operand")
                mode = tokens[index + 1]
                index += 2
            else:
                index += 1
        else:
            option = token[1:2]
            if option not in STDBUF_SHORT_OPTIONS:
                raise ValueError(f"unsupported stdbuf option: {token}")
            mode = token[2:]
            if not mode:
                if index + 1 >= len(tokens):
                    raise ValueError(f"-{option} requires an operand")
                mode = tokens[index + 1]
                index += 2
            else:
                index += 1
        if not mode:
            raise ValueError("stdbuf mode must not be empty")
        saw_mode = True
    if not saw_mode:
        raise ValueError("stdbuf requires a buffering mode option")
    if index >= len(tokens):
        raise ValueError("stdbuf requires a command")
    return index


def _skip_xargs_short_options(tokens: list[str], index: int) -> int:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in XARGS_SHORT_OPTIONS:
            position += 1
            continue
        if option in XARGS_SHORT_OPTIONS_WITH_VALUE:
            if cluster[position + 1 :]:
                return index + 1
            if index + 1 >= len(tokens):
                raise ValueError(f"xargs -{option} requires an operand")
            return index + 2
        if option in XARGS_SHORT_OPTIONS_WITH_OPTIONAL_VALUE:
            return index + 1
        raise ValueError(f"unsupported xargs option: -{option}")
    return index + 1


def _xargs_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in {"--help", "--version"}:
            return None
        if not token.startswith("--"):
            index = _skip_xargs_short_options(tokens, index)
            continue
        if token in XARGS_LONG_OPTIONS:
            index += 1
            continue
        option, separator, operand = token.partition("=")
        if option in XARGS_LONG_OPTIONS_WITH_VALUE:
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if index + 1 >= len(tokens):
                    raise ValueError(f"{option} requires an operand")
                index += 2
            continue
        if option in XARGS_LONG_OPTIONS_WITH_OPTIONAL_VALUE:
            index += 1
            continue
        raise ValueError(f"unsupported xargs option: {token}")
    if index >= len(tokens):
        return None
    return index


def _setsid_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in SETSID_TERMINAL_LONG_OPTIONS:
            return None
        if token in SETSID_LONG_OPTIONS:
            index += 1
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported setsid option: {token}")
        terminal = False
        for option in token[1:]:
            if option in SETSID_SHORT_OPTIONS:
                continue
            if option in SETSID_TERMINAL_SHORT_OPTIONS:
                terminal = True
                continue
            raise ValueError(f"unsupported setsid option: -{option}")
        if terminal:
            return None
        index += 1
    if index >= len(tokens):
        raise ValueError("setsid requires a program")
    return index


def _skip_watch_short_options(
    tokens: list[str], index: int, exec_direct: bool
) -> tuple[int, bool, bool]:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in WATCH_TERMINAL_SHORT_OPTIONS:
            return index + 1, exec_direct, True
        if option in WATCH_SHORT_OPTIONS:
            exec_direct = exec_direct or option == "x"
            position += 1
            continue
        if option == "n":
            attached = cluster[position + 1 :]
            if attached:
                return index + 1, exec_direct, False
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                raise ValueError("watch -n requires an interval")
            return index + 2, exec_direct, False
        if option == "d":
            if cluster[position + 1 :]:
                return index + 1, exec_direct, False
            position += 1
            continue
        raise ValueError(f"unsupported watch option: -{option}")
    return index + 1, exec_direct, False


def _watch_command_index(
    tokens: list[str], command_index: int
) -> tuple[Optional[int], bool]:
    index = command_index + 1
    exec_direct = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in WATCH_TERMINAL_LONG_OPTIONS:
            return None, exec_direct
        if token in WATCH_LONG_OPTIONS:
            exec_direct = exec_direct or token == "--exec"
            index += 1
            continue
        option, separator, operand = token.partition("=")
        if option == "--interval":
            if separator:
                if not operand:
                    raise ValueError("watch --interval requires an interval")
                index += 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError("watch --interval requires an interval")
                index += 2
            continue
        if option == "--differences":
            if separator and not operand:
                raise ValueError("watch --differences has an empty mode")
            index += 1
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported watch option: {token}")
        index, exec_direct, terminal = _skip_watch_short_options(
            tokens, index, exec_direct
        )
        if terminal:
            return None, exec_direct
    if index >= len(tokens):
        raise ValueError("watch requires a command")
    return index, exec_direct


def _taskset_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    pid_mode = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in TASKSET_TERMINAL_LONG_OPTIONS:
            return None
        if token in TASKSET_LONG_OPTIONS:
            index += 1
            continue
        if token == "--pid":
            pid_mode = True
            index += 1
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported taskset option: {token}")
        terminal = False
        for option in token[1:]:
            if option in TASKSET_SHORT_OPTIONS:
                continue
            if option == "p":
                pid_mode = True
                continue
            if option in TASKSET_TERMINAL_SHORT_OPTIONS:
                terminal = True
                continue
            raise ValueError(f"unsupported taskset option: -{option}")
        if terminal:
            return None
        index += 1
    operand_count = len(tokens) - index
    if pid_mode:
        if operand_count not in {1, 2}:
            raise ValueError("taskset --pid requires a pid and optional affinity")
        return None
    if operand_count < 2:
        raise ValueError("taskset requires an affinity and command")
    return index + 1


def _skip_chrt_short_options(
    tokens: list[str], index: int, pid_mode: bool
) -> tuple[int, bool, bool]:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in CHRT_TERMINAL_SHORT_OPTIONS:
            return index + 1, pid_mode, True
        if option in CHRT_SHORT_OPTIONS:
            position += 1
            continue
        if option == "p":
            pid_mode = True
            position += 1
            continue
        if option in CHRT_SHORT_OPTIONS_WITH_VALUE:
            attached = cluster[position + 1 :]
            if attached:
                return index + 1, pid_mode, False
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                raise ValueError(f"chrt -{option} requires an operand")
            return index + 2, pid_mode, False
        raise ValueError(f"unsupported chrt option: -{option}")
    return index + 1, pid_mode, False


def _chrt_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    pid_mode = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in CHRT_TERMINAL_LONG_OPTIONS:
            return None
        if token in CHRT_LONG_OPTIONS:
            index += 1
            continue
        if token == "--pid":
            pid_mode = True
            index += 1
            continue
        option, separator, operand = token.partition("=")
        if option in CHRT_LONG_OPTIONS_WITH_VALUE:
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"{option} requires an operand")
                index += 2
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported chrt option: {token}")
        index, pid_mode, terminal = _skip_chrt_short_options(
            tokens, index, pid_mode
        )
        if terminal:
            return None
    operand_count = len(tokens) - index
    if pid_mode:
        if operand_count not in {1, 2}:
            raise ValueError("chrt --pid requires a pid and optional priority")
        return None
    if operand_count < 2:
        raise ValueError("chrt requires a priority and command")
    return index + 1


def _skip_ionice_short_options(
    tokens: list[str], index: int, target_mode: bool
) -> tuple[int, bool, bool]:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in IONICE_TERMINAL_SHORT_OPTIONS:
            return index + 1, target_mode, True
        if option in IONICE_SHORT_OPTIONS:
            position += 1
            continue
        if option in (
            IONICE_SHORT_OPTIONS_WITH_VALUE
            | IONICE_TARGET_SHORT_OPTIONS_WITH_VALUE
        ):
            if option in IONICE_TARGET_SHORT_OPTIONS_WITH_VALUE:
                target_mode = True
            attached = cluster[position + 1 :]
            if attached:
                return index + 1, target_mode, False
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                raise ValueError(f"ionice -{option} requires an operand")
            return index + 2, target_mode, False
        raise ValueError(f"unsupported ionice option: -{option}")
    return index + 1, target_mode, False


def _ionice_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    target_mode = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in IONICE_TERMINAL_LONG_OPTIONS:
            return None
        if token in IONICE_LONG_OPTIONS:
            index += 1
            continue
        option, separator, operand = token.partition("=")
        value_options = (
            IONICE_LONG_OPTIONS_WITH_VALUE
            | IONICE_TARGET_LONG_OPTIONS_WITH_VALUE
        )
        if option in value_options:
            if option in IONICE_TARGET_LONG_OPTIONS_WITH_VALUE:
                target_mode = True
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"{option} requires an operand")
                index += 2
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported ionice option: {token}")
        index, target_mode, terminal = _skip_ionice_short_options(
            tokens, index, target_mode
        )
        if terminal:
            return None
    if target_mode or index >= len(tokens):
        return None
    return index


def _skip_script_short_options(
    tokens: list[str], index: int, child_command: Optional[str]
) -> tuple[int, Optional[str], bool]:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in SCRIPT_TERMINAL_SHORT_OPTIONS:
            return index + 1, child_command, True
        if option in SCRIPT_SHORT_OPTIONS:
            position += 1
            continue
        if option == "t":
            return index + 1, child_command, False
        if option == "c" or option in SCRIPT_SHORT_OPTIONS_WITH_VALUE:
            attached = cluster[position + 1 :]
            if attached:
                operand = attached
                next_index = index + 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"script -{option} requires an operand")
                operand = tokens[index + 1]
                next_index = index + 2
            if option == "c":
                if child_command is not None:
                    raise ValueError("script command option is duplicated")
                child_command = operand
            return next_index, child_command, False
        raise ValueError(f"unsupported script option: -{option}")
    return index + 1, child_command, False


def _script_child_command(
    tokens: list[str], command_index: int
) -> Optional[str]:
    index = command_index + 1
    child_command: Optional[str] = None
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in SCRIPT_TERMINAL_LONG_OPTIONS:
            return None
        if token in SCRIPT_LONG_OPTIONS:
            index += 1
            continue
        option, separator, operand = token.partition("=")
        if option == "--timing":
            index += 1
            continue
        if option == "--command" or option in SCRIPT_LONG_OPTIONS_WITH_VALUE:
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"{option} requires an operand")
                operand = tokens[index + 1]
                index += 2
            if option == "--command":
                if child_command is not None:
                    raise ValueError("script command option is duplicated")
                child_command = operand
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported script option: {token}")
        index, child_command, terminal = _skip_script_short_options(
            tokens, index, child_command
        )
        if terminal:
            return None
    if len(tokens) - index > 1:
        raise ValueError("script accepts at most one output file")
    if child_command is None:
        raise ValueError("script starts an interactive shell")
    return child_command


def _prlimit_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    pid_mode = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in PRLIMIT_TERMINAL_LONG_OPTIONS:
            return None
        if token in PRLIMIT_LONG_OPTIONS:
            index += 1
            continue
        option, separator, operand = token.partition("=")
        if option in PRLIMIT_RESOURCE_LONG_OPTIONS:
            if separator and not operand:
                raise ValueError(f"{option} has an empty limit")
            index += 1
            continue
        if option in {"--output", "--pid"}:
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if (
                    index + 1 >= len(tokens)
                    or not tokens[index + 1]
                    or tokens[index + 1].startswith("-")
                ):
                    raise ValueError(f"{option} requires an operand")
                operand = tokens[index + 1]
                index += 2
            if option == "--pid":
                if pid_mode:
                    raise ValueError("prlimit pid option is duplicated")
                pid_mode = True
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported prlimit option: {token}")
        cluster = token[1:]
        short_option = cluster[0]
        if short_option in PRLIMIT_TERMINAL_SHORT_OPTIONS:
            return None
        if short_option in PRLIMIT_RESOURCE_SHORT_OPTIONS:
            index += 1
            continue
        if short_option in {"o", "p"}:
            operand = cluster[1:]
            if operand:
                index += 1
            else:
                if (
                    index + 1 >= len(tokens)
                    or not tokens[index + 1]
                    or tokens[index + 1].startswith("-")
                ):
                    raise ValueError(
                        f"prlimit -{short_option} requires an operand"
                    )
                operand = tokens[index + 1]
                index += 2
            if short_option == "p":
                if pid_mode:
                    raise ValueError("prlimit pid option is duplicated")
                pid_mode = True
            continue
        raise ValueError(f"unsupported prlimit option: {token}")
    if pid_mode:
        if index < len(tokens):
            raise ValueError("prlimit --pid does not accept a command")
        return None
    if index >= len(tokens):
        return None
    return index


def _builtin_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    if index >= len(tokens):
        return None
    if tokens[index] == "--":
        index += 1
        return index if index < len(tokens) else None
    if tokens[index].startswith("-"):
        raise ValueError(f"unsupported builtin option: {tokens[index]}")
    return index


def _sudo_command_index(
    tokens: list[str], command_index: int
) -> Optional[int]:
    index = command_index + 1
    reset_timestamp = False
    shell_mode = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in SUDO_TERMINAL_LONG_OPTIONS:
            return None
        if token == "--edit":
            raise ValueError("sudo edit mode does not identify an executed command")
        option, separator, operand = token.partition("=")
        if option == "--preserve-env":
            if separator and not operand:
                raise ValueError("--preserve-env requires a non-empty list")
            index += 1
            continue
        if token in SUDO_LONG_OPTIONS:
            reset_timestamp = reset_timestamp or token == "--reset-timestamp"
            shell_mode = shell_mode or token in {"--login", "--shell"}
            index += 1
            continue
        if option in SUDO_LONG_OPTIONS_WITH_VALUE:
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"{option} requires an operand")
                index += 2
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported sudo option: {token}")
        if token == "-h" and index + 1 >= len(tokens):
            return None
        cluster = token[1:]
        position = 0
        while position < len(cluster):
            short_option = cluster[position]
            if short_option in SUDO_TERMINAL_SHORT_OPTIONS:
                return None
            if short_option == "e":
                raise ValueError(
                    "sudo edit mode does not identify an executed command"
                )
            if short_option in SUDO_SHORT_OPTIONS:
                reset_timestamp = reset_timestamp or short_option == "k"
                shell_mode = shell_mode or short_option in {"i", "s"}
                position += 1
                continue
            if short_option in SUDO_SHORT_OPTIONS_WITH_VALUE or short_option == "h":
                attached = cluster[position + 1 :]
                if attached:
                    position = len(cluster)
                    continue
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"sudo -{short_option} requires an operand")
                index += 1
                position = len(cluster)
                continue
            raise ValueError(f"unsupported sudo option: -{short_option}")
        index += 1
    while index < len(tokens) and ASSIGNMENT.match(tokens[index]):
        index += 1
    if index >= len(tokens):
        if reset_timestamp and not shell_mode:
            return None
        raise ValueError("sudo requires a command")
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
        if token in SHELL_TERMINAL_OPTIONS:
            return None, None
        if token == "-":
            reads_stdin = True
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
        raise ValueError(
            f"{_basename(tokens[command_index])} reads commands from stdin"
        )
    script = tokens[index]
    if script in SHELL_STDIN_SCRIPTS or SHELL_REDIRECTION.match(script):
        raise ValueError(
            f"{_basename(tokens[command_index])} reads commands from stdin"
        )
    return None, _basename(script)


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


def _segment_changes_directory(tokens: list[str]) -> bool:
    tokens = _shell_command_tokens(tokens)
    if not tokens:
        return False
    command_index = _command_index(tokens)
    if command_index is None:
        return False
    executable = _basename(tokens[command_index])
    if executable == "builtin":
        child_index = _builtin_command_index(tokens, command_index)
        if child_index is None:
            return False
        executable = _basename(tokens[child_index])
    return executable in {"cd", "pushd", "popd"}


def _find_child_commands(
    tokens: list[str], command_index: int
) -> Iterator[list[str]]:
    index = command_index + 1
    while index < len(tokens):
        if tokens[index] not in FIND_EXEC_ACTIONS:
            index += 1
            continue
        command_start = index + 1
        if command_start >= len(tokens):
            raise ValueError(f"find {tokens[index]} requires a command")
        command_end = command_start
        while (
            command_end < len(tokens)
            and tokens[command_end] not in FIND_EXEC_TERMINATORS
        ):
            command_end += 1
        if command_end >= len(tokens):
            raise ValueError(f"find {tokens[index]} requires ; or +")
        if command_end == command_start:
            raise ValueError(f"find {tokens[index]} requires a command")
        yield tokens[command_start:command_end]
        index = command_end + 1


def _find_child_is_blocked(
    tokens: list[str], depth: int, relative_wrapper_allowed: bool
) -> bool:
    if _segment_is_blocked(tokens, depth, relative_wrapper_allowed):
        return True
    if not any("{}" in token for token in tokens):
        return False
    return any(
        _segment_is_blocked(
            [token.replace("{}", target) for token in tokens],
            depth,
            relative_wrapper_allowed,
        )
        for target in FIND_PLACEHOLDER_EXECUTION_TARGETS
    )


def _segment_is_blocked(
    tokens: list[str],
    depth: int = 0,
    relative_wrapper_allowed: bool = True,
) -> bool:
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
        if _is_canonical_board_wrapper(tokens[command_index]):
            if (
                not relative_wrapper_allowed
                and not PurePosixPath(tokens[command_index]).is_absolute()
            ):
                raise ValueError(
                    "relative PIM board wrapper path after directory change"
                )
            return False
        raise ValueError("non-canonical PIM board wrapper path")
    if executable == "source" or tokens[command_index] == ".":
        script_index = command_index + 1
        if script_index < len(tokens) and tokens[script_index] == "--":
            script_index += 1
        if script_index >= len(tokens):
            return False
        return _basename(tokens[script_index]) in HARDWARE_RUNNERS
    if executable == "eval":
        if command_index + 1 >= len(tokens):
            return False
        return _command_is_blocked(
            " ".join(tokens[command_index + 1 :]),
            depth + 1,
            relative_wrapper_allowed,
        )
    if executable == "exec":
        child_index = _exec_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "timeout":
        child_index = _timeout_command_index(tokens, command_index)
        return _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "nohup":
        child_index = _nohup_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "nice":
        child_index = _nice_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "stdbuf":
        child_index = _stdbuf_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "xargs":
        child_index = _xargs_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "setsid":
        child_index = _setsid_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "builtin":
        child_index = _builtin_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "sudo":
        child_index = _sudo_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "find":
        return any(
            _find_child_is_blocked(
                child, depth + 1, relative_wrapper_allowed
            )
            for child in _find_child_commands(tokens, command_index)
        )
    if executable == "watch":
        child_index, exec_direct = _watch_command_index(tokens, command_index)
        if child_index is None:
            return False
        if exec_direct:
            return _segment_is_blocked(
                tokens[child_index:], depth + 1, relative_wrapper_allowed
            )
        return _command_is_blocked(
            " ".join(tokens[child_index:]),
            depth + 1,
            relative_wrapper_allowed,
        )
    if executable == "taskset":
        child_index = _taskset_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "chrt":
        child_index = _chrt_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "ionice":
        child_index = _ionice_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "script":
        child_command = _script_child_command(tokens, command_index)
        return child_command is not None and _command_is_blocked(
            child_command, depth + 1, relative_wrapper_allowed
        )
    if executable == "prlimit":
        child_index = _prlimit_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable in SHELLS:
        child_command, child_script = _shell_child(tokens, command_index)
        if child_command is not None:
            return _command_is_blocked(
                child_command, depth + 1, relative_wrapper_allowed
            )
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


def _command_is_blocked(
    command: str,
    depth: int = 0,
    relative_wrapper_allowed: bool = True,
) -> bool:
    if depth > MAX_LAUNCHER_DEPTH:
        raise ValueError("launcher nesting is too deep")
    command = _normalize_ansi_c_quotes(command)
    segments = list(_segments(command))
    substitutions_allow_relative = relative_wrapper_allowed and not any(
        _segment_changes_directory(segment) for segment in segments
    )
    if any(
        _command_is_blocked(
            substitution, depth + 1, substitutions_allow_relative
        )
        for substitution in _shell_substitutions(command)
    ):
        return True
    for segment in segments:
        if _segment_is_blocked(
            segment, depth, relative_wrapper_allowed
        ):
            return True
        if _segment_changes_directory(segment):
            relative_wrapper_allowed = False
    return False


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
