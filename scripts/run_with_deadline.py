#!/usr/bin/env python3
"""Run one command inside a hard lease deadline and reap its process group."""
from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time
from typing import Optional, Sequence


TIMEOUT_EXIT = 124
SUPERVISOR_ERROR_EXIT = 125
PR_SET_CHILD_SUBREAPER = 36
POLL_SECONDS = 0.1


def _parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    try:
        separator = argv.index("--")
    except ValueError:
        separator = -1
    if separator < 0 or separator == len(argv) - 1:
        raise ValueError("-- <command> is required")

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--deadline-epoch", required=True, type=float)
    parser.add_argument("--cleanup-margin-seconds", required=True, type=float)
    parser.add_argument("--term-grace-seconds", required=True, type=float)
    options = parser.parse_args(argv[:separator])
    if options.deadline_epoch <= 0:
        raise ValueError("deadline epoch must be positive")
    if options.cleanup_margin_seconds <= 0:
        raise ValueError("cleanup margin must be positive")
    if options.term_grace_seconds < 0:
        raise ValueError("TERM grace must not be negative")
    if options.term_grace_seconds >= options.cleanup_margin_seconds:
        raise ValueError("TERM grace must leave time to KILL and reap")
    return options, list(argv[separator + 1 :])


def _enable_subreaper() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("deadline supervision requires Linux process subreaping")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _signal_group(process_group: int, signum: int) -> None:
    try:
        os.killpg(process_group, signum)
    except ProcessLookupError:
        pass


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap_adopted_children() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _wait_for_group(
    child: subprocess.Popen[bytes],
    process_group: int,
    until: float,
) -> bool:
    while True:
        child.poll()
        if child.returncode is not None:
            _reap_adopted_children()
        if not _group_exists(process_group):
            return True
        remaining = until - time.time()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_SECONDS, remaining))


def _stop_and_reap(
    child: subprocess.Popen[bytes],
    process_group: int,
    term_until: float,
    reap_until: float,
    initial_signal: int = signal.SIGTERM,
) -> bool:
    _signal_group(process_group, initial_signal)
    if _wait_for_group(child, process_group, term_until):
        return True
    _signal_group(process_group, signal.SIGKILL)
    return _wait_for_group(child, process_group, reap_until)


def _normalize_child_exit(returncode: int) -> int:
    return 128 + (-returncode) if returncode < 0 else returncode


def supervise(options: argparse.Namespace, command: list[str]) -> int:
    execution_deadline = options.deadline_epoch - options.cleanup_margin_seconds
    if time.time() >= execution_deadline:
        print(
            "deadline supervisor: insufficient lease budget to start child safely",
            file=sys.stderr,
        )
        return TIMEOUT_EXIT

    try:
        _enable_subreaper()
    except (OSError, RuntimeError) as exc:
        print(f"deadline supervisor: cannot enable process reaping: {exc}", file=sys.stderr)
        return SUPERVISOR_ERROR_EXIT

    received_signal: Optional[int] = None
    def forward(signum: int, _frame: object) -> None:
        nonlocal received_signal
        if received_signal is None:
            received_signal = signum

    previous_handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    }
    for signum in previous_handlers:
        signal.signal(signum, forward)

    try:
        try:
            child = subprocess.Popen(command, start_new_session=True)
        except OSError as exc:
            print(f"deadline supervisor: failed to start child: {exc}", file=sys.stderr)
            return SUPERVISOR_ERROR_EXIT
        process_group = child.pid

        while received_signal is None:
            remaining = execution_deadline - time.time()
            if remaining <= 0:
                break
            try:
                returncode = child.wait(timeout=min(remaining, POLL_SECONDS))
            except subprocess.TimeoutExpired:
                continue
            if not _group_exists(process_group):
                return _normalize_child_exit(returncode)
            print(
                "deadline supervisor: child exited with descendants still running; "
                "cleaning process group",
                file=sys.stderr,
            )
            term_until = min(
                time.time() + options.term_grace_seconds,
                options.deadline_epoch,
            )
            clean = _stop_and_reap(
                child,
                process_group,
                term_until,
                options.deadline_epoch,
            )
            return _normalize_child_exit(returncode) if clean else SUPERVISOR_ERROR_EXIT

        if received_signal is None:
            print(
                "deadline supervisor: execution deadline reached; "
                "terminating child for teardown",
                file=sys.stderr,
            )
            exit_code = TIMEOUT_EXIT
            term_until = min(
                time.time() + options.term_grace_seconds,
                options.deadline_epoch,
            )
        else:
            print(
                f"deadline supervisor: received signal {received_signal}; "
                "terminating child process group",
                file=sys.stderr,
            )
            exit_code = 128 + received_signal
            term_until = min(
                time.time() + options.term_grace_seconds,
                options.deadline_epoch,
            )

        child_signal = (
            signal.SIGTERM
            if received_signal in (None, signal.SIGHUP)
            else received_signal
        )
        if not _stop_and_reap(
            child,
            process_group,
            term_until,
            options.deadline_epoch,
            initial_signal=child_signal,
        ):
            print(
                "deadline supervisor: process group survived KILL through lease deadline",
                file=sys.stderr,
            )
            return SUPERVISOR_ERROR_EXIT
        return exit_code
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        options, command = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    except (ValueError, SystemExit) as exc:
        print(f"deadline supervisor: invalid arguments: {exc}", file=sys.stderr)
        return SUPERVISOR_ERROR_EXIT
    return supervise(options, command)


if __name__ == "__main__":
    raise SystemExit(main())
