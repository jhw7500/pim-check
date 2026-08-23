"""tests/test_checks_cam_state_heartbeat.py — cam_state 살아있음은 heartbeat 로 본다 (#84 → #93 중앙화).

역사 (#84): `ch{N} cam_state last_ok freshness (<30s)` 체크 36건은 이름이 약속한
`<30s` 비교를 하지 않았고, 비교를 넣는 것도 답이 아니었다 — 보드 소스
(`/opt/pim/lib/cam_state.sh`)에서 `last_ok` 는 `cam_state_init`(초기값 `0`)과
`cam_channel_error()`(에러 시각 기록) 만 쓴다. 즉 정상 운영 중에는 영원히 `0` 이고
값이 갱신되는 유일한 순간이 에러 발생 시점이라, freshness 를 넣으면 정상일 때
FAIL / 에러 직후 PASS 로 뒤집힌다(보드 실측: 정상 상태에서 4채널 전부 `0`).

그래서 보드가 실제로 갱신하는 heartbeat 를 본다 — `cam_state_touch()` 가
`/tmp/cam_state/timestamp` 를 `date +%s` 로 쓴다. 이 신호는
`BG_Check_for_pim.sh` 의 1초 루프가 정상·에러·grace 모든 분기에서 touch 하는
**감시자 프로세스 생존 신호**이지 "카메라 정상" 이 아니다 — 카메라 상태는
`state`(healthy)와 `ch{N}_error` 가 본다.

중앙화 (#93): #84 는 이 판정을 16개 multi_*.yaml 에 셸 명령으로 복제했다 —
AGENTS.md 의 "체크 로직은 BaseCheck 서브클래스" 규약 위반이고, cam_state 를 쓰는
다른 프로파일에는 커버리지가 없으며, 임계값이 명령 문자열에 박혀 있었다. 지금은
`checks/cam_state.py` 가 수집·판정을 소유하고(`heartbeat_max_age_sec`, 기본 30),
cam_state 체크가 도는 모든 프로파일이 자동으로 이 검증을 받는다. 이 모듈은
판정표 승계 + "프로파일 셸이 다시 timestamp 를 읽지 않는다" 는 중앙화를 가드한다.

잔여 공백(의도적): 채널별 **생존** 신호는 여전히 없다. `ch{N}_error=false` 는
"에러가 기록되지 않았다" 이지 "최근에 확인됐다" 가 아니다. 이 구멍은 #91(FW)·#85-4
자리다.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import time
from unittest.mock import MagicMock

import yaml

from checks.cam_state import (
    DEFAULT_HEARTBEAT_MAX_AGE_SEC,
    CamStateCheck,
    heartbeat_command,
)

PROFILES_DIR = pathlib.Path(__file__).resolve().parent.parent / "profiles"
STATE_TS = "/tmp/cam_state/timestamp"

# 판정은 output 안의 (값, now) 쌍만 쓰므로 기준 시각은 임의 고정값이면 된다.
NOW = 1_755_850_000


def _profile_files():
    """로드 대상 프로파일 전체 — base + cases + generated (.deprecated 제외)."""
    files = [PROFILES_DIR / "base.yaml"]
    files += sorted((PROFILES_DIR / "cases").glob("*.yaml"))
    files += sorted((PROFILES_DIR / "generated").glob("*.yaml"))
    return files


def _custom_commands():
    out = []
    for path in _profile_files():
        prof = yaml.safe_load(path.read_text()) or {}
        if not isinstance(prof, dict):
            continue
        for chk in ((prof.get("checks") or {}).get("custom_commands") or []):
            out.append((path.name, chk.get("name"), chk.get("command", ""), chk))
    return out


def _validate(output, cam_cfg_extra=None, data_override=None):
    """healthy 상태 + 주어진 heartbeat output 으로 validate 를 돌린다."""
    check = CamStateCheck()
    cfg = {"cam_state": {"dir": "/tmp/cam_state", "expected_state": "healthy",
                         "valid_states": ["healthy"], "max_streak": 0}}
    if cam_cfg_extra:
        cfg["cam_state"].update(cam_cfg_extra)
    if data_override is not None:
        data = data_override
    else:
        data = {"states": {"state": "healthy"}, "streaks": {"streak": 0},
                "channels": {}, "heartbeat": {"output": output}}
    return check.validate(data, cfg)


class TestLastOkIsGone:
    """`last_ok` 는 freshness 소스가 될 수 없다 — 이름과 실체가 반대다."""

    def test_no_profile_reads_last_ok(self):
        offenders = [f"{f}: {n}" for f, n, cmd, _ in _custom_commands() if "last_ok" in cmd]
        assert not offenders, (
            "last_ok 는 에러 시각이라 freshness 로 쓸 수 없다:\n" + "\n".join(offenders))

    def test_channel_error_checks_survive(self):
        """채널 상태 판정은 `ch{N}_error` 가 계속 담당해야 한다 — 함께 지우면 안 된다."""
        rows = [r for r in _custom_commands() if "_error" in r[2] and "cam_state" in r[2]]
        assert len(rows) >= 30, f"채널 error 체크가 너무 적다 ({len(rows)})"


class TestHeartbeatIsCentralized:
    """#93 회귀 가드 — heartbeat 판정은 checks/cam_state.py 한 곳에만 있다."""

    def test_no_profile_shell_reads_timestamp(self):
        offenders = [f"{f}: {n}" for f, n, cmd, _ in _custom_commands()
                     if "cam_state/timestamp" in cmd or STATE_TS in cmd]
        assert not offenders, (
            "heartbeat 는 CamStateCheck 소유다 — YAML 셸 복제 금지 (#93):\n"
            + "\n".join(offenders))

    def test_base_exposes_threshold(self):
        """임계값이 명령 문자열이 아니라 프로파일 설정 표면에 있다."""
        base = yaml.safe_load((PROFILES_DIR / "base.yaml").read_text())
        assert base["checks"]["cam_state"]["heartbeat_max_age_sec"] == \
            DEFAULT_HEARTBEAT_MAX_AGE_SEC


class TestHeartbeatJudgement:
    """구 셸 판정표 승계 — 각 진단은 다른 원인을 가리키므로 뭉개지 않는다."""

    def test_fresh_passes(self):
        passed, reason = _validate(f"F;{NOW - 1};{NOW}")
        assert passed, reason
        assert reason == "OK"

    def test_missing_file_is_no_file(self):
        passed, reason = _validate(f"N;;{NOW}")
        assert not passed and "heartbeat NO_FILE" in reason

    def test_empty_value_is_bad_value(self):
        passed, reason = _validate(f"F;;{NOW}")
        assert not passed and "heartbeat BAD_VALUE" in reason

    def test_non_numeric_is_bad_value(self):
        passed, reason = _validate(f"F;not-a-number;{NOW}")
        assert not passed and "heartbeat BAD_VALUE" in reason

    def test_overflow_digits_is_bad_value(self):
        """자릿수>11 (구 셸 dash 정수 한계 가드) — 값 이상은 파이썬에서도 이상이다."""
        passed, reason = _validate(f"F;{'9' * 25};{NOW}")
        assert not passed and "heartbeat BAD_VALUE" in reason

    def test_zero_is_never_touched(self):
        """`0` 은 cam_state_init 초기값 — BG_Check 가 한 번도 touch 안 함."""
        passed, reason = _validate(f"F;0;{NOW}")
        assert not passed and "heartbeat NEVER_TOUCHED" in reason

    def test_future_is_not_healthy(self):
        """시계 역행 — 정지한 writer 가 healthy 로 보이면 안 된다."""
        passed, reason = _validate(f"F;{NOW + 3600};{NOW}")
        assert not passed and "heartbeat FUTURE=" in reason

    def test_stale_says_how_stale(self):
        passed, reason = _validate(f"F;{NOW - 300};{NOW}")
        assert not passed and "heartbeat STALE=300s" in reason

    def test_boundary_matches_old_shell(self):
        """구 셸과 동일 경계: age < threshold 만 통과, == 는 STALE."""
        passed, reason = _validate(f"F;{NOW - DEFAULT_HEARTBEAT_MAX_AGE_SEC};{NOW}")
        assert not passed and f"STALE={DEFAULT_HEARTBEAT_MAX_AGE_SEC}s" in reason
        passed, reason = _validate(f"F;{NOW - (DEFAULT_HEARTBEAT_MAX_AGE_SEC - 1)};{NOW}")
        assert passed, reason

    def test_dead_watcher_fails_even_when_state_healthy(self):
        """state=healthy 가 감시자 정지를 가리지 않는다 — 두 신호는 독립이다."""
        passed, reason = _validate(f"F;{NOW - 3000};{NOW}")
        assert not passed and "STALE" in reason


class TestThresholdIsConfig:
    """#93 의 목적 — 임계값이 프로파일 설정이다."""

    def test_custom_threshold_widens(self):
        passed, reason = _validate(f"F;{NOW - 45};{NOW}", {"heartbeat_max_age_sec": 60})
        assert passed, reason

    def test_custom_threshold_tightens(self):
        passed, reason = _validate(f"F;{NOW - 10};{NOW}", {"heartbeat_max_age_sec": 5})
        assert not passed and "STALE=10s" in reason

    def test_invalid_threshold_fails_loud(self):
        """설정 오타는 크래시도 침묵 통과도 아니고 체크 FAIL 로 표면화된다."""
        passed, reason = _validate(f"F;{NOW - 1};{NOW}", {"heartbeat_max_age_sec": "thirty"})
        assert not passed and "heartbeat config invalid" in reason

    def test_inf_threshold_fails_loud_not_crash(self):
        """YAML `.inf` 는 int() 에서 OverflowError — 엔진이 SSH 예외만 잡으므로
        여기서 삼켜 체크 FAIL 로 바꾸지 않으면 QA run 전체가 중단된다 (#98 Codex P2)."""
        passed, reason = _validate(f"F;{NOW - 1};{NOW}",
                                   {"heartbeat_max_age_sec": float("inf")})
        assert not passed and "heartbeat config invalid" in reason


class TestFailClosed:
    """관측 불가는 통과가 아니다 — 수집 실패·키 누락·형식 붕괴 모두 FAIL."""

    def test_collect_none_fails(self):
        passed, reason = _validate(None)
        assert not passed and "heartbeat NO_DATA" in reason

    def test_missing_heartbeat_key_fails(self):
        data = {"states": {"state": "healthy"}, "streaks": {}, "channels": {}}
        passed, reason = _validate("unused", data_override=data)
        assert not passed and "heartbeat NO_DATA" in reason

    def test_malformed_output_fails(self):
        passed, reason = _validate("gibberish")
        assert not passed and "heartbeat NO_DATA" in reason


class TestCommandShellContract:
    """생성 명령을 **실제 셸에서 돌려** exit 0 + 출력 계약과 판정 파이프를 확인한다."""

    def _issue(self, content: str | None, tmp_path):
        if content is not None:
            (tmp_path / "timestamp").write_text(content)
        r = subprocess.run(["sh", "-c", heartbeat_command(str(tmp_path))],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"exit {r.returncode} — ssh.run 이 None 을 받는다: {r.stderr.strip()[:80]}")
        assert r.stdout.strip(), "무출력 — ssh.run strip 후 빈 문자열"
        return CamStateCheck._heartbeat_issue({"output": r.stdout.strip()}, {})

    def test_missing_file(self, tmp_path):
        assert self._issue(None, tmp_path) == "heartbeat NO_FILE"

    def test_fresh_passes(self, tmp_path):
        assert self._issue(str(int(time.time())), tmp_path) is None

    def test_stale_says_how_stale(self, tmp_path):
        issue = self._issue(str(int(time.time()) - 300), tmp_path)
        assert issue and issue.startswith("heartbeat STALE=")
        assert re.search(r"\d+", issue), f"얼마나 오래됐는지 알려주지 않는다: {issue!r}"

    def test_zero(self, tmp_path):
        assert self._issue("0", tmp_path) == "heartbeat NEVER_TOUCHED"

    def test_non_numeric(self, tmp_path):
        assert self._issue("not-a-number", tmp_path) == "heartbeat BAD_VALUE"

    def test_empty_file(self, tmp_path):
        assert self._issue("", tmp_path) == "heartbeat BAD_VALUE"

    def test_future(self, tmp_path):
        issue = self._issue(str(int(time.time()) + 3600), tmp_path)
        assert issue and issue.startswith("heartbeat FUTURE=")

    def test_overflow_digits_keep_exit_contract(self, tmp_path):
        """자릿수 초과에도 exit 0 + 출력 (구 셸에서 dash 가 죽던 지점)."""
        assert self._issue("9" * 25, tmp_path) == "heartbeat BAD_VALUE"

    def test_whitespace_around_value_is_trimmed(self, tmp_path):
        """구 셸의 tr -d ' \\n' 승계 — 공백/개행 낀 정상 값은 통과."""
        assert self._issue(f"  {int(time.time())}\n", tmp_path) is None


class TestCollectWiring:
    """collect 는 값과 보드 now 를 **한 왕복**으로 읽고 결과를 data 에 싣는다."""

    def _ssh(self, hb_response):
        ssh = MagicMock()

        def side_effect(cmd):
            if cmd.endswith("/state"):
                return "healthy"
            if cmd.endswith("/streak"):
                return "0"
            if "channels/" in cmd:
                return ""
            if "timestamp" in cmd:
                return hb_response
            return None

        ssh.run.side_effect = side_effect
        return ssh

    def test_single_roundtrip_carries_both_clock_ends(self):
        """값과 now 가 같은 명령(=같은 보드 시계)에서 나온다 — 호스트 시계 배제."""
        ssh = self._ssh(f"F;{NOW - 1};{NOW}")
        CamStateCheck().collect(ssh, {"cam_state": {"dir": "/tmp/cam_state"}})
        hb_cmds = [c.args[0] for c in ssh.run.call_args_list if "timestamp" in c.args[0]]
        assert len(hb_cmds) == 1, hb_cmds
        assert "date +%s" in hb_cmds[0]

    def test_output_lands_in_data(self):
        ssh = self._ssh(f"F;{NOW - 1};{NOW}")
        data = CamStateCheck().collect(ssh, {"cam_state": {}})
        assert data["heartbeat"]["output"] == f"F;{NOW - 1};{NOW}"

    def test_configured_dir_is_used(self):
        ssh = self._ssh(f"F;{NOW - 1};{NOW}")
        CamStateCheck().collect(ssh, {"cam_state": {"dir": "/run/cs"}})
        hb_cmds = [c.args[0] for c in ssh.run.call_args_list if "timestamp" in c.args[0]]
        assert hb_cmds and "/run/cs/timestamp" in hb_cmds[0]

    def test_state_missing_keeps_error_contract(self):
        ssh = MagicMock()
        ssh.run.return_value = None
        data = CamStateCheck().collect(ssh, {"cam_state": {}})
        assert "error" in data
