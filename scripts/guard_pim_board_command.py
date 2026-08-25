#!/usr/bin/env python3
"""Block issue #108's direct board-mutating commands in Claude Bash calls."""
from __future__ import annotations

import json
import posixpath
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
PIM_CHECK_PLAN_OPTIONS = {"--pl", "--pla", "--plan"}
SHELL_BREAKS = set(";&|(){}\n")
ESCAPED_SEMICOLON_MARKER = "\0"
CLOBBER_REDIRECTION_MARKER = "\1"
FIND_EXEC_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir"}
FIND_EXEC_TERMINATORS = {";", "+"}
RUNTIME_SOURCE_FD = re.compile(
    r"^/(?:dev/fd/[0-9]+|proc/(?:self|thread-self|[0-9]+)/fd/[0-9]+)$"
)
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
GNU_TIME_SHORT_OPTIONS = {"a", "p", "q", "v"}
GNU_TIME_SHORT_OPTIONS_WITH_VALUE = {"f", "o"}
GNU_TIME_TERMINAL_SHORT_OPTIONS = {"V"}
GNU_TIME_LONG_OPTIONS = {
    "--append",
    "--portability",
    "--quiet",
    "--verbose",
}
GNU_TIME_LONG_OPTIONS_WITH_VALUE = {"--format", "--output"}
GNU_TIME_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
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
XARGS_APPENDED_ARGUMENT_PROBES = (
    ("python3", "pim_check.py", "--plan", "smoke"),
    ("pim_check.py", "--plan", "smoke"),
    ("pim_check", "--plan", "smoke"),
    ("-m", "pim_check", "--plan", "smoke"),
    ("--plan", "smoke"),
    ("--plan=smoke",),
    ("-exec", "python3", "pim_check.py", "--plan", "smoke", ";"),
) + tuple((runner,) for runner in sorted(HARDWARE_RUNNERS))
XARGS_REPLACEMENT_PROBES = (
    "python3",
    "--plan",
    "--plan=smoke",
    "python3 pim_check.py --plan smoke",
    "pim_check.py --plan smoke",
    *FIND_PLACEHOLDER_EXECUTION_TARGETS,
)
SETSID_SHORT_OPTIONS = {"c", "f", "w"}
SETSID_LONG_OPTIONS = {"--ctty", "--fork", "--wait"}
SETSID_TERMINAL_SHORT_OPTIONS = {"h", "V"}
SETSID_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
FLOCK_SHORT_OPTIONS = set("Fexnosu")
FLOCK_SHORT_OPTIONS_WITH_VALUE = {"E", "w"}
FLOCK_TERMINAL_SHORT_OPTIONS = {"h", "V"}
FLOCK_LONG_OPTIONS = {
    "--no-fork",
    "--exclusive",
    "--nb",
    "--nonblock",
    "--close",
    "--shared",
    "--unlock",
    "--verbose",
}
FLOCK_LONG_OPTIONS_WITH_VALUE = {
    "--conflict-exit-code",
    "--wait",
    "--timeout",
}
FLOCK_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
SETARCH_EXECUTABLES = {"setarch", "i386", "linux32", "linux64", "x86_64"}
SETARCH_SHORT_OPTIONS = set("v3BFILRSTXZ")
SETARCH_TERMINAL_SHORT_OPTIONS = {"h", "V"}
SETARCH_LONG_OPTIONS = {
    "--uname-2.6",
    "--3gb",
    "--4gb",
    "--32bit",
    "--fdpic-funcptrs",
    "--short-inode",
    "--addr-compat-layout",
    "--addr-no-randomize",
    "--whole-seconds",
    "--sticky-timeouts",
    "--read-implies-exec",
    "--mmap-page-zero",
    "--verbose",
}
SETARCH_TERMINAL_LONG_OPTIONS = {"--help", "--list", "--version"}
START_STOP_DAEMON_ACTION_SHORT_OPTIONS = {
    "S": "start",
    "K": "stop",
    "T": "status",
}
START_STOP_DAEMON_SHORT_OPTIONS = {"t", "o", "q", "b", "C", "m", "v"}
START_STOP_DAEMON_SHORT_OPTIONS_WITH_VALUE = {
    "p",
    "x",
    "n",
    "u",
    "g",
    "s",
    "R",
    "a",
    "c",
    "r",
    "d",
    "O",
    "N",
    "P",
    "I",
    "k",
}
START_STOP_DAEMON_TERMINAL_SHORT_OPTIONS = {"H", "V"}
START_STOP_DAEMON_ACTION_LONG_OPTIONS = {
    "--start": "start",
    "--stop": "stop",
    "--status": "status",
}
START_STOP_DAEMON_LONG_OPTIONS = {
    "--test",
    "--oknodo",
    "--quiet",
    "--background",
    "--notify-await",
    "--no-close",
    "--make-pidfile",
    "--remove-pidfile",
    "--verbose",
}
START_STOP_DAEMON_LONG_OPTIONS_WITH_VALUE = {
    "--pid",
    "--ppid",
    "--pidfile",
    "--exec",
    "--name",
    "--user",
    "--group",
    "--signal",
    "--retry",
    "--startas",
    "--chuid",
    "--chroot",
    "--chdir",
    "--notify-timeout",
    "--output",
    "--nicelevel",
    "--procsched",
    "--iosched",
    "--umask",
}
START_STOP_DAEMON_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
CHROOT_LONG_OPTIONS_WITH_VALUE = {"--groups", "--userspec"}
CHROOT_LONG_OPTIONS = {"--skip-chdir"}
CHROOT_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
SYSTEMD_RUN_SHORT_OPTIONS = {"r", "d", "t", "P", "q", "G"}
SYSTEMD_RUN_SHORT_OPTIONS_WITH_VALUE = {"u", "E"}
SYSTEMD_RUN_UNTRUSTED_SHORT_OPTIONS_WITH_VALUE = {"p", "H", "M"}
SYSTEMD_RUN_TERMINAL_SHORT_OPTIONS = {"h"}
SYSTEMD_RUN_LONG_OPTIONS = {
    "--no-ask-password",
    "--scope",
    "--slice-inherit",
    "--remain-after-exit",
    "--send-sighup",
    "--same-dir",
    "--pty",
    "--pipe",
    "--quiet",
    "--on-clock-change",
    "--on-timezone-change",
    "--no-block",
    "--wait",
    "--collect",
    "--user",
    "--system",
}
SYSTEMD_RUN_LONG_OPTIONS_WITH_VALUE = {
    "--unit",
    "--description",
    "--slice",
    "--service-type",
    "--uid",
    "--gid",
    "--nice",
    "--working-directory",
    "--setenv",
    "--on-active",
    "--on-boot",
    "--on-startup",
    "--on-unit-active",
    "--on-unit-inactive",
    "--on-calendar",
}
SYSTEMD_RUN_UNTRUSTED_LONG_OPTIONS_WITH_VALUE = {
    "--property",
    "--path-property",
    "--socket-property",
    "--timer-property",
    "--host",
    "--machine",
}
SYSTEMD_RUN_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
UNSHARE_NAMESPACE_SHORT_OPTIONS = set("imnpuUCT")
UNSHARE_SHORT_OPTIONS = {"f", "r", "c"}
UNSHARE_SHORT_OPTIONS_WITH_VALUE = {"R", "w", "S", "G"}
UNSHARE_TERMINAL_SHORT_OPTIONS = {"h", "V"}
UNSHARE_NAMESPACE_LONG_OPTIONS = {
    "--ipc",
    "--mount",
    "--net",
    "--pid",
    "--uts",
    "--user",
    "--cgroup",
    "--time",
}
UNSHARE_LONG_OPTIONS = {
    "--fork",
    "--keep-caps",
    "--map-root-user",
    "--map-current-user",
}
UNSHARE_LONG_OPTIONS_WITH_OPTIONAL_VALUE = {
    "--kill-child",
    "--mount-proc",
}
UNSHARE_LONG_OPTIONS_WITH_VALUE = {
    "--map-user",
    "--map-group",
    "--propagation",
    "--setgroups",
    "--root",
    "--wd",
    "--setuid",
    "--setgid",
    "--monotonic",
    "--boottime",
}
UNSHARE_TERMINAL_LONG_OPTIONS = {"--help", "--version"}
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
SHELL_EXPANSION_MARKERS = frozenset("$`*?[{")
SHELL_REDIRECTION = re.compile(
    r"^(?:\d+|\{[A-Za-z_][A-Za-z0-9_]*\})?"
    r"(?P<operator><<<|<<-?|<>|>>|>\||<|>)"
    r"(?P<target>.*)$"
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


def _protect_clobber_redirections(command: str) -> str:
    if CLOBBER_REDIRECTION_MARKER in command:
        raise ValueError("shell command contains a control byte")
    protected: list[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif command.startswith(">|", index):
            protected.extend((">", CLOBBER_REDIRECTION_MARKER))
            index += 2
            continue
        protected.append(char)
        index += 1
    return "".join(protected)


def _segments(command: str) -> Iterator[list[str]]:
    command = _protect_escaped_semicolons(command)
    command = _protect_clobber_redirections(command)
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
            segment.append(
                token.replace(ESCAPED_SEMICOLON_MARKER, ";").replace(
                    CLOBBER_REDIRECTION_MARKER, "|"
                )
            )
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


def _require_static_token(token: str, description: str) -> str:
    if any(marker in token for marker in SHELL_EXPANSION_MARKERS):
        raise ValueError(f"{description} requires shell expansion")
    return token


def _is_canonical_board_wrapper(token: str) -> bool:
    return token in CANONICAL_BOARD_WRAPPERS


def _operand_reads_runtime_fd(token: str) -> bool:
    path = posixpath.normpath(token)
    if token.startswith("/"):
        path = f"/{path.lstrip('/')}"
    return path == "/dev/stdin" or RUNTIME_SOURCE_FD.fullmatch(path) is not None


def _expand_env_split_string(
    tokens: list[str], index: int, operand: str, consumed: int
) -> int:
    expanded = shlex.split(operand)
    if not expanded:
        raise ValueError("env split-string operand is empty")
    tokens[index : index + consumed] = expanded
    return index


def _skip_env_short_options(
    tokens: list[str], index: int
) -> tuple[int, bool]:
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
            return (
                _expand_env_split_string(tokens, index, operand, consumed),
                False,
            )
        return index + consumed, option == "C"
    return index + 1, False


def _skip_env_options(tokens: list[str], index: int) -> tuple[int, bool]:
    changes_directory = False
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1, changes_directory
        if token == "-":
            index += 1
            continue
        if not token.startswith("-"):
            return index, changes_directory
        if not token.startswith("--"):
            index, short_changes_directory = _skip_env_short_options(
                tokens, index
            )
            changes_directory = (
                changes_directory or short_changes_directory
            )
            continue
        if token in {"--help", "--version"}:
            return len(tokens), changes_directory
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
                if option == "--chdir":
                    changes_directory = True
                index += consumed
            continue
        if option in ENV_LONG_OPTIONS_WITH_OPTIONAL_VALUE:
            index += 1
            continue
        raise ValueError(f"unsupported env option: {token}")
    return index, changes_directory


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


def _skip_leading_redirections(tokens: list[str], index: int) -> int:
    while index < len(tokens):
        match = SHELL_REDIRECTION.fullmatch(tokens[index])
        if match is None:
            break
        if match.group("target"):
            index += 1
            continue
        target_index = index + 1
        if (
            target_index >= len(tokens)
            or not tokens[target_index]
            or SHELL_REDIRECTION.fullmatch(tokens[target_index])
        ):
            raise ValueError("shell redirection requires a target")
        index = target_index + 1
    return index


def _command_context(tokens: list[str]) -> tuple[Optional[int], bool]:
    index = 0
    allow_assignments = True
    changes_directory = False
    while index < len(tokens):
        if allow_assignments:
            while index < len(tokens):
                if ASSIGNMENT.match(tokens[index]):
                    index += 1
                    continue
                redirected_index = _skip_leading_redirections(tokens, index)
                if redirected_index == index:
                    break
                index = redirected_index
        if index >= len(tokens):
            return None, changes_directory
        executable = _basename(tokens[index])
        if executable == "env":
            index, env_changes_directory = _skip_env_options(
                tokens, index + 1
            )
            changes_directory = changes_directory or env_changes_directory
            allow_assignments = True
            continue
        if executable == "command":
            command_index = _skip_command_options(tokens, index + 1)
            if command_index is None:
                return None, changes_directory
            index = command_index
            allow_assignments = False
            continue
        return index, changes_directory
    return None, changes_directory


def _command_index(tokens: list[str]) -> Optional[int]:
    return _command_context(tokens)[0]


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
            _require_static_token(module, "Python module operand")
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
    script = _require_static_token(tokens[index], "Python script operand")
    return _basename(script), tokens[index + 1 :]


def _pim_check_arguments_are_blocked(arguments: list[str]) -> bool:
    for argument in arguments:
        _require_static_token(argument, "pim-check argument")
    return any(
        argument.partition("=")[0] in PIM_CHECK_PLAN_OPTIONS
        for argument in arguments
    )


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


def _gnu_time_command_index(
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
        option, separator, operand = token.partition("=")
        if option in GNU_TIME_TERMINAL_LONG_OPTIONS:
            if separator:
                raise ValueError(f"unsupported time option: {token}")
            return None
        if option in GNU_TIME_LONG_OPTIONS:
            if separator:
                raise ValueError(f"unsupported time option: {token}")
            index += 1
            continue
        if option in GNU_TIME_LONG_OPTIONS_WITH_VALUE:
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
            raise ValueError(f"unsupported time option: {token}")

        cluster = token[1:]
        position = 0
        next_index = index + 1
        terminal = False
        while position < len(cluster):
            short_option = cluster[position]
            if short_option in GNU_TIME_SHORT_OPTIONS:
                position += 1
                continue
            if short_option in GNU_TIME_TERMINAL_SHORT_OPTIONS:
                terminal = True
                position += 1
                continue
            if short_option not in GNU_TIME_SHORT_OPTIONS_WITH_VALUE:
                raise ValueError(
                    f"unsupported time option: -{short_option}"
                )
            attached_operand = cluster[position + 1 :]
            if attached_operand:
                position = len(cluster)
                continue
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                raise ValueError(f"-{short_option} requires an operand")
            next_index = index + 2
            position = len(cluster)
        if terminal:
            return None
        index = next_index
    if index >= len(tokens):
        raise ValueError("time requires a command")
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


def _skip_xargs_short_options(
    tokens: list[str], index: int
) -> tuple[int, Optional[str]]:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in XARGS_SHORT_OPTIONS:
            position += 1
            continue
        if option in XARGS_SHORT_OPTIONS_WITH_VALUE:
            operand = cluster[position + 1 :]
            if operand:
                next_index = index + 1
            else:
                if index + 1 >= len(tokens):
                    raise ValueError(f"xargs -{option} requires an operand")
                operand = tokens[index + 1]
                next_index = index + 2
            if option == "I":
                if not operand:
                    raise ValueError("xargs -I replacement must not be empty")
                return next_index, operand
            return next_index, None
        if option in XARGS_SHORT_OPTIONS_WITH_OPTIONAL_VALUE:
            replacement = None
            if option == "i":
                replacement = cluster[position + 1 :] or "{}"
            return index + 1, replacement
        raise ValueError(f"unsupported xargs option: -{option}")
    return index + 1, None


def _record_xargs_replacement(
    current: Optional[str], replacement: str
) -> str:
    if not replacement:
        raise ValueError("xargs replacement must not be empty")
    if current is not None and current != replacement:
        raise ValueError("conflicting xargs replacement markers")
    return replacement


def _xargs_command_context(
    tokens: list[str], command_index: int
) -> tuple[Optional[int], Optional[str]]:
    index = command_index + 1
    replacement: Optional[str] = None
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in {"--help", "--version"}:
            return None, replacement
        if not token.startswith("--"):
            index, short_replacement = _skip_xargs_short_options(
                tokens, index
            )
            if short_replacement is not None:
                replacement = _record_xargs_replacement(
                    replacement, short_replacement
                )
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
            if option == "--replace":
                replacement = _record_xargs_replacement(
                    replacement, operand if separator else "{}"
                )
            index += 1
            continue
        raise ValueError(f"unsupported xargs option: {token}")
    if index >= len(tokens):
        return None, replacement
    return index, replacement


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


def _skip_flock_short_options(
    tokens: list[str], index: int
) -> tuple[int, Optional[str], bool]:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in FLOCK_TERMINAL_SHORT_OPTIONS:
            return index + 1, None, True
        if option in FLOCK_SHORT_OPTIONS:
            position += 1
            continue
        if option == "c":
            command = cluster[position + 1 :]
            if command:
                return index + 1, command, False
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                raise ValueError("flock -c requires a command")
            return index + 2, tokens[index + 1], False
        if option in FLOCK_SHORT_OPTIONS_WITH_VALUE:
            operand = cluster[position + 1 :]
            if operand:
                return index + 1, None, False
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                raise ValueError(f"flock -{option} requires an operand")
            return index + 2, None, False
        raise ValueError(f"unsupported flock option: -{option}")
    return index + 1, None, False


def _flock_child(
    tokens: list[str], command_index: int
) -> tuple[Optional[int], Optional[str]]:
    index = command_index + 1
    lock_operand: Optional[str] = None
    shell_command: Optional[str] = None
    options_enabled = True

    while index < len(tokens):
        token = tokens[index]
        if options_enabled and token == "--":
            options_enabled = False
            index += 1
            continue
        if options_enabled and token != "-" and token.startswith("-"):
            if not token.startswith("--"):
                index, command, terminal = _skip_flock_short_options(
                    tokens, index
                )
                if terminal:
                    return None, None
                if command is not None:
                    if shell_command is not None:
                        raise ValueError("flock accepts only one shell command")
                    shell_command = command
                continue
            option, separator, operand = token.partition("=")
            if option in FLOCK_TERMINAL_LONG_OPTIONS:
                if separator:
                    raise ValueError(f"unsupported flock option: {token}")
                return None, None
            if option in FLOCK_LONG_OPTIONS:
                if separator:
                    raise ValueError(f"unsupported flock option: {token}")
                index += 1
                continue
            if option == "--command":
                if separator:
                    command = operand
                    index += 1
                else:
                    if index + 1 >= len(tokens):
                        raise ValueError("flock --command requires a command")
                    command = tokens[index + 1]
                    index += 2
                if not command:
                    raise ValueError("flock --command requires a command")
                if shell_command is not None:
                    raise ValueError("flock accepts only one shell command")
                shell_command = command
                continue
            if option in FLOCK_LONG_OPTIONS_WITH_VALUE:
                if separator:
                    if not operand:
                        raise ValueError(f"{option} requires an operand")
                    index += 1
                else:
                    if index + 1 >= len(tokens) or not tokens[index + 1]:
                        raise ValueError(f"{option} requires an operand")
                    index += 2
                continue
            raise ValueError(f"unsupported flock option: {token}")
        if lock_operand is None:
            lock_operand = token
            index += 1
            continue
        if shell_command is not None:
            raise ValueError("flock -c does not accept command arguments")
        return index, None

    if lock_operand is None:
        raise ValueError("flock requires a lock operand")
    if shell_command is not None:
        return None, shell_command
    if re.fullmatch(r"\d+", lock_operand):
        return None, None
    raise ValueError("flock requires a command for a file or directory lock")


def _setarch_command_index(
    tokens: list[str], command_index: int, executable: str
) -> Optional[int]:
    index = command_index + 1
    if executable == "setarch" and index < len(tokens):
        architecture = tokens[index]
        if architecture == "-" or not architecture.startswith("-"):
            _require_static_token(architecture, "setarch architecture")
            index += 1

    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in SETARCH_TERMINAL_LONG_OPTIONS:
            return None
        if token in SETARCH_LONG_OPTIONS:
            index += 1
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported {executable} option: {token}")
        terminal = False
        for option in token[1:]:
            if option in SETARCH_SHORT_OPTIONS:
                continue
            if option in SETARCH_TERMINAL_SHORT_OPTIONS:
                terminal = True
                continue
            raise ValueError(f"unsupported {executable} option: -{option}")
        if terminal:
            return None
        index += 1

    if index >= len(tokens):
        raise ValueError(f"{executable} requires a program")
    return index


def _start_stop_daemon_child(
    tokens: list[str], command_index: int
) -> Optional[list[str]]:
    index = command_index + 1
    action: Optional[str] = None
    startas: Optional[str] = None
    executable: Optional[str] = None
    test_mode = False
    chroot_mode = False
    arguments: list[str] = []

    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            arguments = tokens[index + 1 :]
            break
        if token == "-" or not token.startswith("-"):
            raise ValueError(
                "start-stop-daemon has an unexpected operand before --"
            )
        if token in START_STOP_DAEMON_TERMINAL_LONG_OPTIONS:
            return None

        option, separator, operand = token.partition("=")
        if option in START_STOP_DAEMON_ACTION_LONG_OPTIONS:
            if separator:
                raise ValueError(f"unsupported start-stop-daemon option: {token}")
            if action is not None:
                raise ValueError("start-stop-daemon command is duplicated")
            action = START_STOP_DAEMON_ACTION_LONG_OPTIONS[option]
            index += 1
            continue
        if option in START_STOP_DAEMON_LONG_OPTIONS:
            if separator:
                raise ValueError(f"unsupported start-stop-daemon option: {token}")
            test_mode = test_mode or option == "--test"
            index += 1
            continue
        if option in START_STOP_DAEMON_LONG_OPTIONS_WITH_VALUE:
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"{option} requires an operand")
                operand = tokens[index + 1]
                index += 2
            if option == "--startas":
                if startas is not None:
                    raise ValueError("start-stop-daemon startas is duplicated")
                startas = operand
            elif option == "--exec":
                if executable is not None:
                    raise ValueError("start-stop-daemon exec is duplicated")
                executable = operand
            elif option == "--chroot":
                chroot_mode = True
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported start-stop-daemon option: {token}")

        cluster = token[1:]
        position = 0
        next_index = index + 1
        terminal = False
        while position < len(cluster):
            short_option = cluster[position]
            if short_option in START_STOP_DAEMON_ACTION_SHORT_OPTIONS:
                if action is not None:
                    raise ValueError("start-stop-daemon command is duplicated")
                action = START_STOP_DAEMON_ACTION_SHORT_OPTIONS[short_option]
                position += 1
                continue
            if short_option in START_STOP_DAEMON_TERMINAL_SHORT_OPTIONS:
                terminal = True
                position += 1
                continue
            if short_option in START_STOP_DAEMON_SHORT_OPTIONS:
                test_mode = test_mode or short_option == "t"
                position += 1
                continue
            if short_option not in START_STOP_DAEMON_SHORT_OPTIONS_WITH_VALUE:
                raise ValueError(
                    f"unsupported start-stop-daemon option: -{short_option}"
                )
            attached = cluster[position + 1 :]
            if attached:
                operand = attached
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(
                        f"start-stop-daemon -{short_option} requires an operand"
                    )
                operand = tokens[index + 1]
                next_index = index + 2
            if short_option == "a":
                if startas is not None:
                    raise ValueError("start-stop-daemon startas is duplicated")
                startas = operand
            elif short_option == "x":
                if executable is not None:
                    raise ValueError("start-stop-daemon exec is duplicated")
                executable = operand
            elif short_option == "r":
                chroot_mode = True
            position = len(cluster)
        if terminal:
            return None
        index = next_index

    if action is None:
        raise ValueError("start-stop-daemon requires a command")
    if action != "start":
        if startas is not None or arguments:
            raise ValueError(
                "start-stop-daemon non-start command cannot launch a program"
            )
        return None

    program = startas or executable
    if program is None:
        raise ValueError("start-stop-daemon --start requires a program")
    _require_static_token(program, "start-stop-daemon program")
    if test_mode:
        return None
    if chroot_mode:
        raise ValueError("start-stop-daemon chroot program path is ambiguous")
    return [program, *arguments]


def _chroot_child(
    tokens: list[str], command_index: int
) -> tuple[Optional[list[str]], bool]:
    index = command_index + 1
    skip_chdir = False

    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in CHROOT_TERMINAL_LONG_OPTIONS:
            return None, False
        option, separator, operand = token.partition("=")
        if option in CHROOT_LONG_OPTIONS_WITH_VALUE:
            if not separator or not operand:
                raise ValueError(f"{option} requires a non-empty =value")
            index += 1
            continue
        if option in CHROOT_LONG_OPTIONS:
            if separator:
                raise ValueError(f"unsupported chroot option: {token}")
            skip_chdir = True
            index += 1
            continue
        raise ValueError(f"unsupported chroot option: {token}")

    if index >= len(tokens):
        raise ValueError("chroot requires a new root")
    new_root = _require_static_token(tokens[index], "chroot new root")
    if new_root != "/":
        raise ValueError("chroot executable paths are trusted only for root /")
    index += 1
    if index >= len(tokens):
        raise ValueError("chroot without a command starts an interactive shell")
    return tokens[index:], skip_chdir


def _systemd_run_child(
    tokens: list[str], command_index: int
) -> tuple[Optional[list[str]], bool]:
    index = command_index + 1
    scope_mode = False
    working_directory_changed = False

    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-" or not token.startswith("-"):
            break
        if token in SYSTEMD_RUN_TERMINAL_LONG_OPTIONS:
            return None, False

        option, separator, operand = token.partition("=")
        if option in SYSTEMD_RUN_LONG_OPTIONS:
            if separator:
                raise ValueError(f"unsupported systemd-run option: {token}")
            scope_mode = scope_mode or option == "--scope"
            index += 1
            continue
        if option == "--shell":
            if separator:
                raise ValueError(f"unsupported systemd-run option: {token}")
            raise ValueError("systemd-run --shell starts an interactive shell")
        if option in (
            SYSTEMD_RUN_LONG_OPTIONS_WITH_VALUE
            | SYSTEMD_RUN_UNTRUSTED_LONG_OPTIONS_WITH_VALUE
        ):
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"{option} requires an operand")
                operand = tokens[index + 1]
                index += 2
            if option in SYSTEMD_RUN_UNTRUSTED_LONG_OPTIONS_WITH_VALUE:
                raise ValueError(
                    f"{option} makes systemd-run executable identity ambiguous"
                )
            working_directory_changed = (
                working_directory_changed or option == "--working-directory"
            )
            continue
        if token.startswith("--"):
            raise ValueError(f"unsupported systemd-run option: {token}")

        cluster = token[1:]
        position = 0
        next_index = index + 1
        terminal = False
        while position < len(cluster):
            short_option = cluster[position]
            if short_option in SYSTEMD_RUN_TERMINAL_SHORT_OPTIONS:
                terminal = True
                position += 1
                continue
            if short_option == "S":
                raise ValueError(
                    "systemd-run -S starts an interactive shell"
                )
            if short_option in SYSTEMD_RUN_SHORT_OPTIONS:
                position += 1
                continue
            if short_option not in (
                SYSTEMD_RUN_SHORT_OPTIONS_WITH_VALUE
                | SYSTEMD_RUN_UNTRUSTED_SHORT_OPTIONS_WITH_VALUE
            ):
                raise ValueError(
                    f"unsupported systemd-run option: -{short_option}"
                )
            attached = cluster[position + 1 :]
            if attached:
                operand = attached
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(
                        f"systemd-run -{short_option} requires an operand"
                    )
                operand = tokens[index + 1]
                next_index = index + 2
            if short_option in SYSTEMD_RUN_UNTRUSTED_SHORT_OPTIONS_WITH_VALUE:
                raise ValueError(
                    f"systemd-run -{short_option} makes executable identity ambiguous"
                )
            position = len(cluster)
        if terminal:
            return None, False
        index = next_index

    if index >= len(tokens):
        raise ValueError("systemd-run requires an explicit command")
    return tokens[index:], scope_mode and not working_directory_changed


def _skip_unshare_short_options(tokens: list[str], index: int) -> tuple[int, bool]:
    cluster = tokens[index][1:]
    position = 0
    while position < len(cluster):
        option = cluster[position]
        if option in UNSHARE_TERMINAL_SHORT_OPTIONS:
            return index + 1, True
        if option in UNSHARE_SHORT_OPTIONS:
            position += 1
            continue
        if option in UNSHARE_NAMESPACE_SHORT_OPTIONS:
            attached = cluster[position + 1 :]
            if attached.startswith("="):
                if len(attached) == 1:
                    raise ValueError(f"unshare -{option} requires a non-empty file")
                return index + 1, False
            position += 1
            continue
        if option in UNSHARE_SHORT_OPTIONS_WITH_VALUE:
            attached = cluster[position + 1 :]
            if attached:
                return index + 1, False
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                raise ValueError(f"unshare -{option} requires an operand")
            return index + 2, False
        raise ValueError(f"unsupported unshare option: -{option}")
    return index + 1, False


def _unshare_command_index(
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
        if not token.startswith("--"):
            index, terminal = _skip_unshare_short_options(tokens, index)
            if terminal:
                return None
            continue
        option, separator, operand = token.partition("=")
        if option in UNSHARE_TERMINAL_LONG_OPTIONS:
            if separator:
                raise ValueError(f"unsupported unshare option: {token}")
            return None
        if option in UNSHARE_LONG_OPTIONS:
            if separator:
                raise ValueError(f"unsupported unshare option: {token}")
            index += 1
            continue
        if option in (
            UNSHARE_NAMESPACE_LONG_OPTIONS
            | UNSHARE_LONG_OPTIONS_WITH_OPTIONAL_VALUE
        ):
            if separator and not operand:
                raise ValueError(f"{option} requires a non-empty operand")
            index += 1
            continue
        if option in UNSHARE_LONG_OPTIONS_WITH_VALUE:
            if separator:
                if not operand:
                    raise ValueError(f"{option} requires an operand")
                index += 1
            else:
                if index + 1 >= len(tokens) or not tokens[index + 1]:
                    raise ValueError(f"{option} requires an operand")
                index += 2
            continue
        raise ValueError(f"unsupported unshare option: {token}")
    if index >= len(tokens):
        raise ValueError("unshare requires a program")
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
            if index + 2 < len(tokens):
                raise ValueError(
                    f"{_basename(tokens[command_index])} -c positional operands "
                    "are unsupported"
                )
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
    if (
        script == "-"
        or _operand_reads_runtime_fd(script)
        or SHELL_REDIRECTION.match(script)
    ):
        raise ValueError(
            f"{_basename(tokens[command_index])} reads commands from runtime input"
        )
    script = _require_static_token(script, "shell script operand")
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


def _replacement_values_for_target(
    template: str, marker: str, target: str
) -> Iterator[str]:
    if marker not in template:
        return
    seen: set[str] = set()
    for start in range(len(target) + 1):
        for end in range(start, len(target) + 1):
            replacement = target[start:end]
            if replacement in seen:
                continue
            if template.replace(marker, replacement) == target:
                seen.add(replacement)
                yield replacement


def _xargs_replacement_values(
    child: list[str], marker: str
) -> Iterator[str]:
    seen: set[str] = set()
    for probe in XARGS_REPLACEMENT_PROBES:
        if probe not in seen:
            seen.add(probe)
            yield probe
    for token in child:
        for target in XARGS_REPLACEMENT_PROBES:
            for replacement in _replacement_values_for_target(
                token, marker, target
            ):
                if replacement not in seen:
                    seen.add(replacement)
                    yield replacement


def _xargs_child_is_blocked(
    tokens: list[str],
    command_index: int,
    depth: int,
    relative_wrapper_allowed: bool,
) -> bool:
    child_index, replacement = _xargs_command_context(tokens, command_index)
    if child_index is None:
        return False
    child = tokens[child_index:]
    if _segment_is_blocked(child, depth, relative_wrapper_allowed):
        return True
    if replacement is not None:
        if not any(replacement in token for token in child):
            return False
        return any(
            _segment_is_blocked(
                [token.replace(replacement, probe) for token in child],
                depth,
                relative_wrapper_allowed,
            )
            for probe in _xargs_replacement_values(child, replacement)
        )
    return any(
        _segment_is_blocked(
            child + list(probe), depth, relative_wrapper_allowed
        )
        for probe in XARGS_APPENDED_ARGUMENT_PROBES
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
    command_index, env_changes_directory = _command_context(tokens)
    if command_index is None:
        return False
    relative_wrapper_allowed = (
        relative_wrapper_allowed and not env_changes_directory
    )
    executable_token = _require_static_token(
        tokens[command_index], "command executable"
    )
    executable = _basename(executable_token)
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
        script = _require_static_token(
            tokens[script_index], "source script operand"
        )
        if _operand_reads_runtime_fd(script):
            raise ValueError("source operand reads from a runtime file descriptor")
        return _basename(script) in HARDWARE_RUNNERS
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
    if executable == "time":
        child_index = _gnu_time_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
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
        return _xargs_child_is_blocked(
            tokens,
            command_index,
            depth + 1,
            relative_wrapper_allowed,
        )
    if executable == "setsid":
        child_index = _setsid_command_index(tokens, command_index)
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "flock":
        child_index, child_command = _flock_child(tokens, command_index)
        if child_command is not None:
            return _command_is_blocked(
                child_command, depth + 1, relative_wrapper_allowed
            )
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable in SETARCH_EXECUTABLES:
        child_index = _setarch_command_index(
            tokens, command_index, executable
        )
        return child_index is not None and _segment_is_blocked(
            tokens[child_index:], depth + 1, relative_wrapper_allowed
        )
    if executable == "start-stop-daemon":
        child = _start_stop_daemon_child(tokens, command_index)
        return child is not None and _segment_is_blocked(
            child,
            depth + 1,
            relative_wrapper_allowed=False,
        )
    if executable == "chroot":
        child, skip_chdir = _chroot_child(tokens, command_index)
        return child is not None and _segment_is_blocked(
            child,
            depth + 1,
            relative_wrapper_allowed=(
                relative_wrapper_allowed if skip_chdir else False
            ),
        )
    if executable == "systemd-run":
        child, working_directory_inherited = _systemd_run_child(
            tokens, command_index
        )
        return child is not None and _segment_is_blocked(
            child,
            depth + 1,
            relative_wrapper_allowed=(
                relative_wrapper_allowed and working_directory_inherited
            ),
        )
    if executable == "unshare":
        child_index = _unshare_command_index(tokens, command_index)
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
        return _pim_check_arguments_are_blocked(tokens[command_index + 1 :])
    if not PYTHON.match(executable):
        return False
    script, arguments = _python_script(tokens, command_index)
    if script in HARDWARE_RUNNERS:
        return True
    return script == "pim_check.py" and _pim_check_arguments_are_blocked(
        arguments
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
