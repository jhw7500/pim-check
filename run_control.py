"""run_control — 웹뷰어에서 plan 런을 시작/중지하기 위한 순수 로직.

뷰어(pim_web_viewer)는 이 모듈의 검증·상태파일 함수를 사용해 pim_check.py
서브프로세스를 안전하게 spawn/stop 한다. 여기에는 subprocess 실행이 없는
검증/상태 관리 로직만 둔다(테스트 용이 + 명령 주입 방지의 단일 지점).

보안:
  - plan 은 list_plans 화이트리스트에 있어야 한다(임의 --plan 차단).
  - host/user 는 엄격한 정규식으로 제한.
  - 실제 spawn 은 shell 없이 argv 리스트로 실행(뷰어 측). 여기서 값만 검증.
"""
from __future__ import annotations

import json
import os
import re

# 호스트: IPv4/호스트네임 문자만 (shell 미사용이지만 추가 방어).
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,253}$")
# 유저: 일반 유닉스 계정 문자.
_USER_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_CONTROL_FILE = ".control.json"


def validate_start_request(params: dict, available_plans: list[str]) -> tuple[bool, str | None, dict]:
    """start 요청 파라미터 검증. (ok, error, clean) 반환.

    clean 은 검증 통과 시 {plan, host, user, password} (양끝 공백 제거).
    password 는 형식 제한 없음(임의 비밀번호 허용) — 뷰어 spawn 시 argv 가 아니라
    env(PIM_PASSWORD)로 전달되어 ps/proc 노출과 주입을 모두 회피한다.
    """
    if not isinstance(params, dict):
        return False, "invalid request body", {}
    plan = str(params.get("plan", "")).strip()
    host = str(params.get("host", "")).strip()
    user = str(params.get("user", "")).strip()
    password = str(params.get("password", ""))
    if not plan:
        return False, "plan is required", {}
    if plan not in available_plans:
        return False, f"unknown plan: {plan}", {}
    if not _HOST_RE.match(host):
        return False, "invalid host", {}
    if not _USER_RE.match(user):
        return False, "invalid user", {}
    if password == "":
        return False, "password is required", {}
    return True, None, {"plan": plan, "host": host, "user": user, "password": password}


def control_state_path(events_dir: str) -> str:
    return os.path.join(events_dir, _CONTROL_FILE)


def write_control(events_dir: str, info: dict) -> None:
    """추적 정보({pid, plan, host, started_at}) 를 상태파일에 기록."""
    with open(control_state_path(events_dir), "w", encoding="utf-8") as f:
        json.dump(info, f)


def read_control(events_dir: str) -> dict | None:
    """상태파일을 읽어 dict 반환(없거나 깨졌으면 None)."""
    path = control_state_path(events_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def clear_control(events_dir: str) -> None:
    try:
        os.remove(control_state_path(events_dir))
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    """프로세스 생존 여부(POSIX, signal 0)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 존재하지만 권한 없음
    return True


def active_pid(events_dir: str) -> int | None:
    """현재 뷰어가 관리 중인 살아있는 런의 PID(없으면 None). 죽었으면 상태파일 정리."""
    info = read_control(events_dir)
    if not info:
        return None
    pid = info.get("pid")
    if isinstance(pid, int) and pid_alive(pid):
        return pid
    clear_control(events_dir)
    return None
