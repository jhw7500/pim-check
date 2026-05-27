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

import json
import os
import tempfile
import threading
import time
from datetime import datetime

CURRENT_SYMLINK_NAME = "current.jsonl"
# multi-target 라우팅: events/<BY_TARGET_DIR>/<host_slug>/ 아래에 per-target 런 파일.
# 기존 단일 타겟(host=None) 코드 경로는 그대로 events/ 직속에 남는다 (backward compat).
BY_TARGET_DIR = "by-target"
# multi-target viewer 가 enumerate 할 활성 host 인덱스 파일.
ACTIVE_HOSTS_NAME = "active.json"

# active.json 의 read/modify/write 직렬화. ThreadPoolExecutor 로 같은 events_dir 에
# 여러 host 가 거의 동시에 start_run_file 을 부르는 multi-target 시나리오에서 마지막-쓰기-승자
# (race) 로 항목 누락이 발생하지 않도록 한다. lock 은 process-local 이라 멀티 프로세스
# 시나리오(별도 pim_check 인스턴스)는 update 의 atomic rename 으로 보호한다.
_ACTIVE_LOCK = threading.Lock()


def default_events_dir() -> str:
    """Absolute path of the project-local ``events/`` directory."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "events")


def host_slug(host: str | None) -> str:
    """Make a host string safe for path use.

    IP/hostname 의 ``.`` 와 path/공백 등 모호한 문자는 ``-``/``_`` 로 치환해
    디렉터리 이름으로 안전한 식별자를 만든다. 빈 값/None 은 안정 fallback.
    """
    if not host:
        return "_unknown"
    out = []
    for c in str(host):
        if c.isalnum() or c == "-":
            out.append(c)
        elif c in (".",):
            out.append("-")
        else:
            out.append("_")
    slug = "".join(out).strip("-_") or "_unknown"
    return slug


def target_events_dir(events_dir: str, host: str | None) -> str:
    """Resolve the per-target events subdirectory.

    ``host`` 가 None 이면 ``events_dir`` 그대로 — 기존 동작(backward compat).
    값이 있으면 ``events_dir/by-target/<slug>/`` — multi-target 라우팅.
    """
    if not host:
        return events_dir
    return os.path.join(events_dir, BY_TARGET_DIR, host_slug(host))


def _atomic_write_json(path: str, payload: dict) -> None:
    """active.json 을 임시 파일 → rename 으로 원자적으로 교체한다.

    동시에 읽는 viewer 가 half-written JSON 을 보지 못하도록 한다. tempfile 은
    같은 디렉터리에 만들어 cross-FS rename 을 피한다.
    """
    dirname = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=dirname,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        raise


def read_active_hosts(events_dir: str) -> dict:
    """Read ``events/active.json``. Returns ``{"hosts": []}`` if missing/invalid.

    File-level 에서만 보호 — 멀티 프로세스 동시 갱신 race 는 update 의 atomic
    rename 으로 차단된다.
    """
    path = os.path.join(events_dir, ACTIVE_HOSTS_NAME)
    if not os.path.exists(path):
        return {"hosts": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("hosts"), list):
            return {"hosts": []}
        return data
    except (OSError, ValueError):
        return {"hosts": []}


def register_active_host(
    events_dir: str, host: str, plan: str, board: str, run_basename: str,
) -> None:
    """Upsert ``host`` 항목을 ``events/active.json`` 에 기록한다.

    같은 host 의 재실행은 새 항목을 만들지 않고 기존 항목을 갱신한다 (slug 가
    같으므로 viewer 가 host 별 1 컬럼만 그리도록 한다). current 경로는
    ``events_dir`` 기준 상대 경로로 저장돼, 디렉터리를 옮겨도 그대로 해석된다.
    """
    slug = host_slug(host)
    current_rel = os.path.join(BY_TARGET_DIR, slug, CURRENT_SYMLINK_NAME)
    entry = {
        "host": host,
        "slug": slug,
        "plan": plan,
        "board": board,
        "current": current_rel,
        "run": os.path.join(BY_TARGET_DIR, slug, run_basename),
        "started_at": time.time(),
    }
    with _ACTIVE_LOCK:
        data = read_active_hosts(events_dir)
        hosts = [h for h in data.get("hosts", []) if h.get("host") != host]
        hosts.append(entry)
        _atomic_write_json(
            os.path.join(events_dir, ACTIVE_HOSTS_NAME), {"hosts": hosts},
        )


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
    host: str | None = None,
) -> str:
    """Create the run-scoped JSONL file and atomically update ``current.jsonl``.

    Called once at run start. Creates ``events/`` if needed, touches the
    run-scoped file so the symlink target exists, then atomically points
    ``events/current.jsonl`` at it.

    ``host`` 가 주어지면 per-target 라우팅:
      - 런 파일은 ``events_dir/by-target/<slug>/<basename>`` 에 생성
      - per-target ``events_dir/by-target/<slug>/current.jsonl`` 도 갱신
      - 기존 ``events_dir/current.jsonl`` 도 함께 갱신 (TUI viewer 호환 — last-started wins)
      - ``events_dir/active.json`` 의 host 항목을 upsert (multi-target viewer enumerate 용)
    ``host=None`` 이면 기존 동작 그대로 (events_dir/<basename> + events_dir/current.jsonl).

    Returns the absolute path of the run-scoped JSONL file.
    """
    if events_dir is None:
        events_dir = default_events_dir()
    os.makedirs(events_dir, exist_ok=True)
    basename = run_file_name(plan, board, ts=ts)

    if host:
        # multi-target 라우팅 — per-target 서브디렉터리에 파일 생성.
        per_target_dir = target_events_dir(events_dir, host)
        os.makedirs(per_target_dir, exist_ok=True)
        run_path = os.path.join(per_target_dir, basename)
        with open(run_path, "a", encoding="utf-8"):
            pass
        # per-target current.jsonl (web viewer 가 host 별로 본다)
        update_current_symlink(per_target_dir, basename)
        # legacy events/current.jsonl 도 함께 갱신 — TUI viewer 호환 유지.
        # 심링크 target 은 events_dir 기준 상대 경로 (서브디렉터리 포함 가능).
        legacy_target = os.path.join(BY_TARGET_DIR, host_slug(host), basename)
        update_current_symlink(events_dir, legacy_target)
        # multi-target viewer 가 활성 host 를 알아내도록 인덱스 갱신.
        register_active_host(events_dir, host, plan, board, basename)
        return run_path

    # 단일 타겟 (기존 동작) — events_dir 직속에 파일/심링크.
    run_path = os.path.join(events_dir, basename)
    with open(run_path, "a", encoding="utf-8"):
        pass
    update_current_symlink(events_dir, basename)
    return run_path
