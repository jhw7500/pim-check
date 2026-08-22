"""tests/test_cam_state_heartbeat.py — cam_state 살아있음은 heartbeat 로 본다 (pim-check#84).

`ch{N} cam_state last_ok freshness (<30s)` 체크 36건은 이름이 약속한 `<30s` 비교를
**하지 않았다** — 파일이 있고 비어 있지 않으면 OK 였다. 그런데 시간 비교를 넣는 것이
답이 아니다. 보드 소스를 보면 그 신호의 의미가 이름과 반대다:

    cam_channel_error() {                                   # /opt/pim/lib/cam_state.sh:250
        _cs_write "channels/ch${ch}_error" true
        _cs_write "channels/ch${ch}_last_ok" "$(date +%s)"  # ← 에러 함수 안에서 기록
    }
    cam_channel_clear() { _cs_write "channels/ch${ch}_error" false }   # last_ok 안 건드림

`last_ok` 를 쓰는 곳은 여기와 `cam_state_init`(초기값 `0`) 둘뿐이다. 즉 **정상 운영
중에는 영원히 `0`** 이고, 값이 갱신되는 유일한 순간이 **에러 발생 시점**이다.
`now - L < 30` 을 넣으면 정상일 때 FAIL / 에러 직후 PASS 로 **뒤집힌다**(보드 실측:
정상 상태에서 4채널 전부 `0`).

그래서 채널별 36건을 걷어내고, 보드가 실제로 갱신하는 heartbeat 를 본다 —
`cam_state_touch()`(:160)가 `/tmp/cam_state/timestamp` 를 `date +%s` 로 쓴다
(실측: 현재보다 3초 전). 채널 상태는 이미 `ch{N} cam_state error count` 가 담당한다.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import tempfile

import yaml

CASES_DIR = pathlib.Path(__file__).resolve().parent.parent / "profiles" / "cases"
STATE_TS = "/tmp/cam_state/timestamp"


def _custom_commands():
    out = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        prof = yaml.safe_load(path.read_text()) or {}
        if not isinstance(prof, dict):
            continue
        for chk in ((prof.get("checks") or {}).get("custom_commands") or []):
            out.append((path.name, chk.get("name"), chk.get("command", ""), chk))
    return out


def _heartbeat_checks():
    return [r for r in _custom_commands() if STATE_TS in r[2]]


class TestLastOkIsGone:
    """`last_ok` 는 freshness 소스가 될 수 없다 — 이름과 실체가 반대다."""

    def test_no_case_reads_last_ok(self):
        offenders = [f"{f}: {n}" for f, n, cmd, _ in _custom_commands() if "last_ok" in cmd]
        assert not offenders, (
            "last_ok 는 에러 시각이라 freshness 로 쓸 수 없다:\n" + "\n".join(offenders))

    def test_channel_error_checks_survive(self):
        """채널 상태 판정은 `ch{N}_error` 가 계속 담당해야 한다 — 함께 지우면 안 된다."""
        rows = [r for r in _custom_commands() if "_error" in r[2] and "cam_state" in r[2]]
        assert len(rows) >= 30, f"채널 error 체크가 너무 적다 ({len(rows)})"


class TestHeartbeatCheckExists:
    def test_every_file_that_had_last_ok_now_has_a_heartbeat_check(self):
        """36건을 걷어낸 16개 파일이 heartbeat 체크 1건씩을 갖는다."""
        files = {f for f, _, _, _ in _heartbeat_checks()}
        assert len(files) >= 16, f"heartbeat 체크를 가진 파일이 너무 적다 ({len(files)})"
        # 파일당 정확히 1건 — 채널별로 늘리면 같은 전역 신호를 중복해서 본다.
        per_file: dict[str, int] = {}
        for f, _, _, _ in _heartbeat_checks():
            per_file[f] = per_file.get(f, 0) + 1
        dupes = {f: c for f, c in per_file.items() if c != 1}
        assert not dupes, f"파일당 1건이어야 한다: {dupes}"

    def test_expectation_is_not_vacuous(self):
        for fname, name, _, spec in _heartbeat_checks():
            assert spec.get("expected") == "OK", f"{fname}: {name}"
            assert spec.get("on_fail"), f"{fname}: {name} — on_fail 없음"


class TestHeartbeatCommandBehaviour:
    """명령을 **실제 셸에서 돌려** 판정과 exit 규약을 확인한다."""

    def _run(self, cmd: str, content: str | None):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "timestamp"
            if content is not None:
                path.write_text(content)
            return subprocess.run(
                ["sh", "-c", cmd.replace(STATE_TS, str(path))],
                capture_output=True, text=True)

    def _one(self):
        rows = _heartbeat_checks()
        assert rows, "heartbeat 체크가 없다"
        return rows[0][2]

    def test_missing_file_reports_without_dying(self):
        r = self._run(self._one(), None)
        assert r.returncode == 0, f"exit {r.returncode} — ssh.run 이 None 을 받는다"
        assert r.stdout.strip(), "무출력"
        assert "OK" != r.stdout.strip()

    def test_fresh_timestamp_passes(self):
        import time
        r = self._run(self._one(), str(int(time.time())))
        assert r.returncode == 0
        assert r.stdout.strip() == "OK", r.stdout

    def test_stale_timestamp_fails_and_says_how_stale(self):
        import time
        r = self._run(self._one(), str(int(time.time()) - 300))
        assert r.returncode == 0
        assert r.stdout.strip() != "OK"
        assert re.search(r"\d+", r.stdout), f"얼마나 오래됐는지 알려주지 않는다: {r.stdout!r}"

    def test_zero_is_not_treated_as_a_timestamp(self):
        """`0` 은 `cam_state_init` 의 초기값이다 — 한 번도 갱신되지 않았다는 뜻."""
        r = self._run(self._one(), "0")
        assert r.returncode == 0
        assert r.stdout.strip() != "OK", "초기값 0 을 통과시켰다"

    def test_non_numeric_is_rejected(self):
        r = self._run(self._one(), "not-a-number")
        assert r.returncode == 0
        assert r.stdout.strip() != "OK"

    def test_empty_file_is_rejected(self):
        r = self._run(self._one(), "")
        assert r.returncode == 0
        assert r.stdout.strip() != "OK"
