#!/usr/bin/env python3
"""Run-scoped JSONL stream file layout + ``events/current.jsonl`` symlink.

This module owns two file-lifecycle concerns and *only* these two:

1. The run-scoped file name layout ``events/<ts>_<plan>_<board>.jsonl`` — one
   file per ``pim_check.py`` execution.
2. The ``events/current.jsonl`` discovery symlink that the standalone viewer
   (``pim_viewer``) defaults to. The symlink is updated *atomically* at run
   start so a viewer already tailing ``current.jsonl`` never observes a missing
   or half-written link during the swap.

Event payload *serialization* (the JSONL record bodies) lives in
``event_stream.py`` and is intentionally **out of scope** here — this module is
purely about where the run file lives and how ``current.jsonl`` points at it.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

CURRENT_SYMLINK_NAME = "current.jsonl"


def default_events_dir() -> str:
    """Absolute path of the project-local ``events/`` directory."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "events")


def _sanitize(token: str) -> str:
    """Make a token safe + parseable inside a run file name.

    Keeps alphanumerics plus ``-`` and ``.``; everything else (including the
    ``_`` field separator and path separators) collapses to ``_`` so the run
    file name stays splittable on ``_``.
    """
    safe = "".join(c if (c.isalnum() or c in "-.") else "_" for c in str(token))
    return safe or "unknown"


def run_file_name(plan: str, board: str, ts: str | None = None) -> str:
    """Build the run-scoped basename ``<ts>_<plan>_<board>.jsonl``.

    ``ts`` defaults to a compact local timestamp with microseconds
    (``YYYYmmddTHHMMSSffffff``). Microsecond resolution keeps the basename
    collision-resistant when two runs of the same plan/board start within the
    same second (rapid reruns / CI) — otherwise they would share one file and
    interleave events, corrupting both runs' viewer state.
    """
    if ts is None:
        ts = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    return f"{_sanitize(ts)}_{_sanitize(plan)}_{_sanitize(board)}.jsonl"


def update_current_symlink(events_dir: str, run_basename: str) -> str:
    """Atomically (re)point ``events/current.jsonl`` at ``run_basename``.

    Implementation: create a uniquely-named temporary symlink, then
    ``os.replace`` it onto ``current.jsonl``. ``os.replace`` is atomic on POSIX
    and overwrites any existing file/symlink, so a concurrent reader either sees
    the old target or the new target — never a gap.

    The symlink target is the *relative* basename (not an absolute path) so the
    link resolves correctly within ``events_dir`` regardless of process cwd.

    Returns the absolute path of the ``current.jsonl`` symlink.
    """
    current_path = os.path.join(events_dir, CURRENT_SYMLINK_NAME)
    tmp_path = os.path.join(
        events_dir,
        f".{CURRENT_SYMLINK_NAME}.{os.getpid()}.{time.time_ns()}.tmp",
    )
    # Best-effort cleanup of a stale temp link from a crashed prior attempt.
    try:
        os.remove(tmp_path)
    except FileNotFoundError:
        pass
    os.symlink(run_basename, tmp_path)
    try:
        os.replace(tmp_path, current_path)
    except OSError:
        # Leave no dangling temp link behind if the atomic swap fails.
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        raise
    return current_path


def start_run_file(
    plan: str,
    board: str,
    events_dir: str | None = None,
    ts: str | None = None,
) -> str:
    """Create the run-scoped JSONL file and atomically update ``current.jsonl``.

    Called once at run start. Creates ``events/`` if needed, touches the
    run-scoped file so the symlink target exists, then atomically points
    ``events/current.jsonl`` at it.

    Returns the absolute path of the run-scoped JSONL file.
    """
    if events_dir is None:
        events_dir = default_events_dir()
    os.makedirs(events_dir, exist_ok=True)
    basename = run_file_name(plan, board, ts=ts)
    run_path = os.path.join(events_dir, basename)
    # Touch the run file so current.jsonl never points at a missing target.
    with open(run_path, "a", encoding="utf-8"):
        pass
    update_current_symlink(events_dir, basename)
    return run_path
