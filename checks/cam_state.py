"""
checks/cam_state.py - cam_state 디렉터리 상태·에러 스트릭·감시자 heartbeat 체크

heartbeat 신호의 의미 (#84→#93 중앙화): {dir}/timestamp 는 BG_Check_for_pim.sh 의
1초 루프가 정상·에러·grace 모든 분기에서 date +%s 로 touch 하는 **감시자 프로세스
생존 신호**다 — "카메라 정상" 이 아니다. 카메라 상태는 state(healthy)와
channels/ch{N}_error 가 본다. 진단 구분(NO_FILE/BAD_VALUE/NEVER_TOUCHED/FUTURE/
STALE)은 16개 multi_*.yaml 에 복제돼 있던 셸 판정을 그대로 옮긴 것.
"""
from __future__ import annotations

from checks.base_check import BaseCheck

# 감시자 정지 판정 임계(초). 프로파일 checks.cam_state.heartbeat_max_age_sec 로 조정.
DEFAULT_HEARTBEAT_MAX_AGE_SEC = 30

# epoch 초는 11자리를 넘지 않는다 — 초과는 값 오염 (구 셸 체크의 오버플로 가드 승계).
_MAX_EPOCH_DIGITS = 11


def heartbeat_command(state_dir: str) -> str:
    """timestamp 값과 보드 현재 시각을 한 왕복으로 읽는 명령.

    delta 의 양변이 같은 시계(보드 date +%s)에서 나오도록 한 명령에 묶는다.
    어떤 파일 상태에서도 exit 0 + 출력("F;<값>;<now>" 또는 "N;;<now>")을 지킨다
    — exit≠0 이면 ssh.run 이 None 을 돌려줘 진단이 사라지기 때문.
    """
    return (
        f"T={state_dir}/timestamp; "
        "if [ -r \"$T\" ]; then printf 'F;%s' \"$(cat \"$T\" 2>/dev/null | tr -d ' \\n')\"; "
        "else printf 'N;'; fi; "
        "printf ';%s' \"$(date +%s)\""
    )


class CamStateCheck(BaseCheck):
    name = "cam_state"

    def collect(self, ssh, config: dict) -> dict:
        cam_config = config.get("cam_state", {})
        state_dir = cam_config.get("dir", "/tmp/cam_state")

        # Read top-level state and streak
        state_val = ssh.run(f"cat {state_dir}/state")
        if state_val is None:
            return {"states": {}, "streaks": {}, "channels": {}, "error": f"{state_dir}/state not found"}

        states: dict[str, str] = {"state": state_val.strip()}

        streak_val = ssh.run(f"cat {state_dir}/streak")
        streaks: dict[str, int] = {}
        if streak_val is not None:
            try:
                streaks["streak"] = int(streak_val.strip())
            except ValueError:
                streaks["streak"] = 0

        # Read per-channel error files from channels/ subdir
        channels: dict[str, str] = {}
        ch_ls = ssh.run(f"ls {state_dir}/channels/ 2>/dev/null")
        if ch_ls:
            for fname in ch_ls.splitlines():
                fname = fname.strip()
                if fname and "error" in fname:
                    val = ssh.run(f"cat {state_dir}/channels/{fname}")
                    channels[fname] = val.strip() if val else ""

        heartbeat = {"output": ssh.run(heartbeat_command(state_dir))}

        return {"states": states, "streaks": streaks, "channels": channels,
                "heartbeat": heartbeat}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if "error" in data:
            return (False, data["error"])

        cam_config = config.get("cam_state", {})
        valid_states = cam_config.get("valid_states", [])
        expected_state = cam_config.get("expected_state", "")
        max_streak = cam_config.get("max_streak", 0)

        issues: list[str] = []

        for name, value in data.get("states", {}).items():
            if valid_states and value not in valid_states:
                issues.append(f"{name}='{value}' is not a valid state")
            elif expected_state and value != expected_state:
                issues.append(f"{name}='{value}' (expected '{expected_state}')")

        for name, value in data.get("streaks", {}).items():
            if value > max_streak:
                issues.append(f"{name}={value} exceeds max_streak={max_streak}")

        hb_issue = self._heartbeat_issue(data.get("heartbeat"), cam_config)
        if hb_issue:
            issues.append(hb_issue)

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")

    @staticmethod
    def _heartbeat_issue(heartbeat: dict | None, cam_config: dict) -> str | None:
        """감시자 생존 판정 — 이상 없으면 None, 있으면 진단 1건.

        각 진단은 다른 원인을 가리킨다: NO_FILE(cam_state 자체 부재) /
        BAD_VALUE(형식 오염 또는 자릿수 초과) / NEVER_TOUCHED(init 후 touch 0회) /
        FUTURE(시계 역행 — 정지한 writer 가 healthy 로 보이는 것 차단) /
        STALE(감시자 정지) / NO_DATA(수집 실패 — collect 명령이 exit 0 을
        보장하므로 정상 경로에서는 나오지 않는다).
        """
        raw_cfg = cam_config.get("heartbeat_max_age_sec", DEFAULT_HEARTBEAT_MAX_AGE_SEC)
        try:
            max_age = int(raw_cfg)
        except (TypeError, ValueError, OverflowError):
            # OverflowError: YAML .inf → int(inf). 엔진은 SSH 예외만 잡으므로
            # 여기서 못 삼키면 QA run 전체가 중단된다 (#98 Codex P2).
            return f"heartbeat config invalid: heartbeat_max_age_sec={raw_cfg!r}"

        output = (heartbeat or {}).get("output")
        if output is None:
            return "heartbeat NO_DATA (collect failed)"
        parts = output.split(";")
        if len(parts) < 3 or parts[0] not in ("F", "N"):
            return f"heartbeat NO_DATA (malformed: {output!r})"
        flag = parts[0]
        now_raw = parts[-1]
        # 값 안에 ';' 가 있어도 마지막 필드(now)는 보존된다 — 그런 값은 아래에서
        # 숫자 검사로 BAD_VALUE 가 된다.
        raw = ";".join(parts[1:-1])
        if flag == "N":
            return "heartbeat NO_FILE"
        if not (raw.isascii() and raw.isdigit()):
            return "heartbeat BAD_VALUE"
        if len(raw) > _MAX_EPOCH_DIGITS:
            return "heartbeat BAD_VALUE"
        if not (now_raw.isascii() and now_raw.isdigit()):
            return f"heartbeat NO_DATA (board clock unreadable: {now_raw!r})"
        ts = int(raw)
        if ts == 0:
            return "heartbeat NEVER_TOUCHED"
        age = int(now_raw) - ts
        if age < 0:
            return f"heartbeat FUTURE={age}s"
        if age >= max_age:
            return f"heartbeat STALE={age}s"
        return None
