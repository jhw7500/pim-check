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
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from typing import IO, Any

_logger = logging.getLogger(__name__)

try:  # POSIX → fcntl.flock 으로 file-wide advisory lock.
    import fcntl as _fcntl
    _msvcrt = None  # type: ignore[assignment]
except ImportError:  # pragma: no cover — POSIX 외 환경 (Windows 등)
    _fcntl = None  # type: ignore[assignment]
    try:  # Windows → msvcrt.locking 으로 byte-range mandatory lock.
        import msvcrt as _msvcrt  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover — 비-POSIX, 비-Windows (희박)
        _msvcrt = None  # type: ignore[assignment]

CURRENT_SYMLINK_NAME = "current.jsonl"
# multi-target 라우팅: events/<BY_TARGET_DIR>/<host_slug>/ 아래에 per-target 런 파일.
# 기존 단일 타겟(host=None) 코드 경로는 그대로 events/ 직속에 남는다 (backward compat).
BY_TARGET_DIR = "by-target"
# multi-target viewer 가 enumerate 할 활성 host 인덱스 파일.
ACTIVE_HOSTS_NAME = "active.json"
# active.json read-modify-write 의 cross-process 직렬화에 쓰는 파일 락. 같은 프로세스 안의
# 스레드 race 는 _ACTIVE_LOCK 으로, 다른 프로세스(별도 pim_check 인스턴스 또는 web 의
# spawn) race 는 이 파일에 대한 fcntl.flock(LOCK_EX) 으로 막는다.
ACTIVE_HOSTS_LOCK_NAME = f".{ACTIVE_HOSTS_NAME}.lock"

# 같은 process 안의 thread 직렬화. flock 은 thread 간에는 보호를 주지 않으므로 별도 필요.
_ACTIVE_LOCK = threading.Lock()


def default_events_dir() -> str:
    """Absolute path of the project-local ``events/`` directory."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "events")


def host_slug(host: str | None) -> str:
    """Make a host string safe for path use, normalized to lowercase.

    IP/hostname 의 ``.`` 와 path/공백 등 모호한 문자는 ``-``/``_`` 로 치환해
    디렉터리 이름으로 안전한 식별자를 만든다. 빈 값/None 은 안정 fallback.

    case-insensitive 정규화: macOS/Windows 의 기본 파일시스템(HFS+/APFS/NTFS)은
    대소문자 보존하지만 비교는 무시한다. ``Host-A`` 와 ``host-a`` 가 서로 다른
    슬러그를 만들면 by-target/ 안에서 두 자식이 같은 디렉터리를 두고 경합해
    control / current.jsonl 이 깨질 수 있다. ``.lower()`` 로 항상 같은 슬러그를
    돌려준다.
    """
    if not host:
        return "_unknown"
    out = []
    for c in str(host).lower():
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


def _acquire_active_lock(lock_f: IO[Any]) -> None:
    """active.json 의 cross-process exclusive lock 획득.

    플랫폼별 semantics:
      - **POSIX** ``fcntl.flock(LOCK_EX)``: file-wide *advisory* lock — 협조하지
        않는 reader 는 차단되지 않으므로 lock file 을 별도로 열어도 충돌 없음.
      - **Windows** ``msvcrt.locking(LK_LOCK, 1)``: byte-range *mandatory* lock —
        잠긴 1 byte 범위는 OS 가 강제하므로 같은 lock file 을 reading 모드로 여는
        외부 도구/테스트는 ``AccessDenied`` 를 받는다. ``.active.json.lock`` 은
        register 가 잡고 있는 동안 read-open 금지.
      - **LK_LOCK** 은 1초 간격 10번 재시도 후 OSError; deadlock 회피 위해 swallow
        하고 thread lock + atomic rename 만으로 graceful degradation (운영자가
        invisible degradation 을 감지하도록 ``_logger.warning`` 로 표면화).
      - 둘 다 없으면 no-op — 동시 갱신은 ``_atomic_write_json`` 의 rename
        atomicity 에만 의존 (lost-update 가능, 비-POSIX 비-Windows 한정).
    """
    if _fcntl is not None:
        _fcntl.flock(lock_f.fileno(), _fcntl.LOCK_EX)
    elif _msvcrt is not None:
        # seek 도 OSError 가능 (bad fd) — try 안에 넣어 finally 의 close() 가 항상 실행되게.
        try:
            lock_f.seek(0)
            _msvcrt.locking(lock_f.fileno(), _msvcrt.LK_LOCK, 1)
        except OSError:  # pragma: no cover — 10번 재시도 실패 (극히 드뭄) 또는 bad fd
            _logger.warning(
                "active.json msvcrt lock acquire 실패 — thread lock + atomic rename 만으로 진행"
            )


def _release_active_lock(lock_f: IO[Any]) -> None:
    """``_acquire_active_lock`` 으로 잡은 lock 해제. 양쪽 OS 모두 idempotent."""
    if _fcntl is not None:
        try:
            _fcntl.flock(lock_f.fileno(), _fcntl.LOCK_UN)
        except OSError:  # pragma: no cover — lock fd 가 이미 끊겼을 때
            pass
    elif _msvcrt is not None:
        try:
            lock_f.seek(0)
            _msvcrt.locking(lock_f.fileno(), _msvcrt.LK_UNLCK, 1)
        except OSError:  # pragma: no cover — 이미 unlock 됐거나 bad fd
            pass


def register_active_host(
    events_dir: str, host: str, plan: str, board: str, run_basename: str,
) -> None:
    """Upsert ``host`` 항목을 ``events/active.json`` 에 기록한다.

    같은 host 의 재실행은 새 항목을 만들지 않고 기존 항목을 갱신한다 (slug 가
    같으므로 viewer 가 host 별 1 컬럼만 그리도록 한다). current 경로는
    ``events_dir`` 기준 상대 경로로 저장돼, 디렉터리를 옮겨도 그대로 해석된다.

    동시성:
      - **thread 간**: ``_ACTIVE_LOCK`` (process-local threading.Lock)
      - **process 간**: ``ACTIVE_HOSTS_LOCK_NAME`` 파일에 OS 별 file lock —
        - POSIX: ``fcntl.flock(LOCK_EX)``
        - Windows: ``msvcrt.locking(LK_LOCK, 1)``
        별도 ``pim_check.py`` 인스턴스나 ``web.py`` 가 spawn 한 자식이 같은
        ``events_dir`` 의 ``active.json`` 을 동시에 갱신해도 lost-update 가
        발생하지 않는다. 비-POSIX 비-Windows 환경에서는 thread lock 만 적용.
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
    # events_dir 은 이미 caller (start_run_file) 가 makedirs 했지만, register 가
    # 단독 호출돼도 안전하도록 한 번 더 보장 — lock file 도 만들 위치가 필요하다.
    os.makedirs(events_dir, exist_ok=True)
    lock_path = os.path.join(events_dir, ACTIVE_HOSTS_LOCK_NAME)
    with _ACTIVE_LOCK:
        # POSIX → fcntl, Windows → msvcrt, 둘 다 없으면 thread lock 만 적용.
        # 바이너리 모드 — lock file 내용은 의미 없는 sentinel 만이라 text-mode 의
        # newline 변환/buffering 잡음 회피. msvcrt.locking 의 byte-range lock 은
        # 비어 있는 파일(EOF 너머)에서 OSError 가능 — 최초 1 byte sentinel 보장.
        has_file_lock = _fcntl is not None or _msvcrt is not None
        lock_f = open(lock_path, "a+b") if has_file_lock else None
        try:
            if lock_f is not None:
                # cold-start 시 두 프로세스가 모두 empty 를 보고 각자 sentinel 을 append
                # 할 수 있다 — 그 race 는 benign: msvcrt.locking 은 길이와 무관하게 byte
                # 0 만 잠그므로 lock semantics 영향 0. 결과는 multi-byte sentinel 파일
                # (의미 없는 0x00 들) 로, lock 동작은 정상 유지된다.
                lock_f.seek(0, os.SEEK_END)
                if lock_f.tell() == 0:
                    lock_f.write(b"\x00")
                    lock_f.flush()
                _acquire_active_lock(lock_f)
            data = read_active_hosts(events_dir)
            # malformed active.json (e.g. manually edited 후 non-dict 가 섞임) 에서도
            # AttributeError 로 죽지 않도록 dict 만 필터. 비정상 항목은 폐기 — register
            # 가 viewer 인프라를 멈추게 둘 가치가 없다.
            hosts = [
                h for h in data.get("hosts", [])
                if isinstance(h, dict) and h.get("host") != host
            ]
            hosts.append(entry)
            _atomic_write_json(
                os.path.join(events_dir, ACTIVE_HOSTS_NAME), {"hosts": hosts},
            )
        finally:
            if lock_f is not None:
                _release_active_lock(lock_f)
                lock_f.close()


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
