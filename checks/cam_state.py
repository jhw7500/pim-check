"""
checks/cam_state.py - cam_state 디렉터리 상태 및 에러 스트릭 체크
"""
from __future__ import annotations

from checks.base_check import BaseCheck


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

        return {"states": states, "streaks": streaks, "channels": channels}

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

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")
