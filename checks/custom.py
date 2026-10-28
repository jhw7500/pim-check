"""
checks/custom.py - YAML 정의 커스텀 명령 체크
"""
from __future__ import annotations

import math
import re

from checks.base_check import BaseCheck

# 타겟에서 실행 금지 패턴 (root로 실행되므로 주의)
_DANGEROUS_PATTERNS = ["rm -rf", "mkfs", "dd if=", "fdisk", "> /dev/sd", "> /dev/mmc"]

# finalize(닫힌) 녹화 파일이 있어야 판정되는 항목의 명령 표지 — 단일 출처.
# plan execution.skip_finalize_checks 가 이 분류로 항목을 뺀다 (quick_verify).
# 표지가 늘거나 케이스 항목이 바뀌면 tests/test_plan_quick_verify.py 의 범주별
# 개수 고정이 깨진다 — 조용히 커버리지가 빠지지 않게 하는 장치다.
FINALIZE_MARKERS = (
    "ffprobe",                         # 닫힌 파일의 fps/bitrate/duration/codec/format 판독
    "NEED_2_FINALIZES",                # 부팅 후 두 번째 finalize 세션 대기
    "ONLY_ONE_FILE",                   # finalize 된 파일 2개(파일 간 delta) 필요
    "chk_cam_operate.sh.*completed",   # 세션 완료 로그로 finalize 세션 조회
)


def needs_finalized_recording(cmd_spec: dict) -> bool:
    """custom_commands 항목이 finalize 된 녹화 파일을 요구하는지 (FINALIZE_MARKERS)."""
    command = (cmd_spec or {}).get("command") or ""
    return any(marker in command for marker in FINALIZE_MARKERS)


# 녹화 파일이 놓이는 경로 (SD 정상 / RAM fallback 보관). 보드에는 finalize 된 파일만
# 보이므로 이 경로를 세어 expected_min ≥ 1 을 요구하는 항목은 첫 파일(≈1 세그먼트)
# 전에는 통과할 수 없다. 기대 0건(비활성 채널·srt off)은 첫 파일 전에도 참이라 제외.
RECORDING_FILE_MARKERS = ("/mnt/sd_cam", "/dev/shm/recordings")


def needs_recording_file(cmd_spec: dict) -> bool:
    """custom_commands 항목이 녹화 파일이 **하나 이상** 있어야 통과하는지.

    setup 의 recording readiness 단계를 끈 profile(readiness.recording: false)에서
    빼야 하는 항목 — 그 단계가 바로 첫 녹화 파일을 기다리는 단계다."""
    spec = cmd_spec or {}
    command = spec.get("command") or ""
    minimum = spec.get("expected_min")
    return (any(marker in command for marker in RECORDING_FILE_MARKERS)
            and isinstance(minimum, (int, float)) and not isinstance(minimum, bool)
            and minimum >= 1)


def _matches_expected_regex(pattern: object, output: object) -> bool:
    """A missing output or invalid/non-empty regex must never turn into PASS."""
    if not isinstance(pattern, str) or not pattern or not isinstance(output, str):
        return False
    try:
        return re.search(pattern, output.strip()) is not None
    except re.error:
        return False


def _finite_number(value: object) -> float | None:
    """Reject malformed, NaN and infinite observations in numeric gates."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value.strip() if isinstance(value, str) else value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


class CustomCommandCheck(BaseCheck):
    name = "custom_commands"

    def collect(self, ssh, config: dict) -> dict:
        commands = config.get("custom_commands", [])
        if not commands:
            return {"results": [], "skipped": True}

        results = []
        for cmd_spec in commands:
            name = cmd_spec.get("name", "unnamed")
            command = cmd_spec.get("command", "")

            # 위험 명령 차단
            cmd_lower = command.lower()
            if any(p in cmd_lower for p in _DANGEROUS_PATTERNS):
                results.append({
                    "name": name, "command": command, "output": None,
                    "expected": cmd_spec.get("expected"),
                    "expected_min": cmd_spec.get("expected_min"),
                    "expected_max": cmd_spec.get("expected_max"),
                    "expected_regex": cmd_spec.get("expected_regex"),
                    "on_fail": f"BLOCKED: dangerous command pattern in '{command}'",
                })
                continue

            output = ssh.run(command)

            results.append({
                "name": name,
                "command": command,
                "output": output,
                "expected": cmd_spec.get("expected"),
                "expected_min": cmd_spec.get("expected_min"),
                "expected_max": cmd_spec.get("expected_max"),
                "expected_regex": cmd_spec.get("expected_regex"),
                "on_fail": cmd_spec.get("on_fail", f"{name} failed"),
            })

        return {"results": results, "skipped": False}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if data.get("skipped"):
            return (True, "Skipped (no custom commands configured)")

        issues = []
        for r in data["results"]:
            output = r["output"]

            # expected: 정확 일치
            if r["expected"] is not None:
                if output is None or output.strip() != str(r["expected"]).strip():
                    issues.append(f'{r["name"]}: {r["on_fail"]} (got: {output})')
                continue

            # Numeric upper/lower bounds must reject invalid observations.
            low = r.get("expected_min")
            high = r.get("expected_max")
            if low is not None or high is not None:
                val = _finite_number(output)
                low_n = _finite_number(low) if low is not None else None
                high_n = _finite_number(high) if high is not None else None
                if val is None or (low is not None and low_n is None) or (high is not None and high_n is None):
                    issues.append(f'{r["name"]}: {r["on_fail"]} (non-numeric output/bound: {output})')
                    continue
                if low_n is not None and val < low_n:
                    issues.append(f'{r["name"]}: {r["on_fail"]} ({val:g} < {low})')
                if high_n is not None and val > high_n:
                    issues.append(f'{r["name"]}: {r["on_fail"]} ({val:g} > {high})')
                continue

            # expected_regex: recovery/respawn proof must match actual output.
            if r.get("expected_regex") is not None:
                pattern = r["expected_regex"]
                if not _matches_expected_regex(pattern, output):
                    issues.append(
                        f'{r["name"]}: {r["on_fail"]} '
                        f'(regex: {pattern!r}, got: {output})')
                continue

            # expected/expected_min/expected_max/expected_regex 없으면 응답 유무만 확인
            if output is None:
                issues.append(f'{r["name"]}: {r["on_fail"]} (command returned no output)')
                continue

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")


def failing_items(data: dict) -> list[tuple[str, str]]:
    """custom_commands collect() data → 실패 항목 [(항목 이름, validate 의 issue 문자열)].

    항목 하나씩 validate 를 다시 돌려 체크 reason 과 같은 문자열을 얻는다 — 안정화 지표
    분류(verify_retry.is_stabilization_reason)를 체크가 아니라 항목 단위로 하기 위함(#170).
    """
    if not isinstance(data, dict) or data.get("skipped"):
        return []
    check = CustomCommandCheck()
    out: list[tuple[str, str]] = []
    for r in data.get("results") or []:
        passed, issue = check.validate({"results": [r], "skipped": False}, {})
        if not passed:
            out.append((r.get("name", "unnamed"), issue))
    return out


def item_results(data: dict) -> list[dict]:
    """custom_commands collect() data → 항목별 [{name, expected, actual, passed}].

    validate() 와 동일한 통과 판정을 항목 단위로 노출한다(뷰어 '측정/기대' 표시용).
    expected_min 케이스는 expected 를 '>= N' 으로 표시해 case_start checklist 와 맞춘다.
    """
    if data.get("skipped"):
        return []
    out: list[dict] = []
    for r in data.get("results", []):
        output = r.get("output")
        actual = output.strip() if isinstance(output, str) else output
        expected = r.get("expected")
        expected_min = r.get("expected_min")
        expected_max = r.get("expected_max")
        expected_regex = r.get("expected_regex")
        if expected is not None:
            # actual 은 위에서 정규화(문자열이면 strip, 아니면 원값) — 비문자열 output 도 안전.
            passed = actual is not None and str(actual) == str(expected).strip()
            exp_disp = str(expected)
        elif expected_min is not None or expected_max is not None:
            val = _finite_number(actual)
            low = _finite_number(expected_min) if expected_min is not None else None
            high = _finite_number(expected_max) if expected_max is not None else None
            passed = (val is not None
                      and (expected_min is None or low is not None and val >= low)
                      and (expected_max is None or high is not None and val <= high))
            labels = []
            if expected_min is not None:
                labels.append(f">= {expected_min}")
            if expected_max is not None:
                labels.append(f"<= {expected_max}")
            exp_disp = ", ".join(labels)
        elif expected_regex is not None:
            passed = _matches_expected_regex(expected_regex, output)
            exp_disp = f"regex: {expected_regex}"
        else:
            passed = output is not None
            exp_disp = None
        out.append({
            "name": r.get("name", "unnamed"),
            "expected": exp_disp,
            "actual": actual,
            "passed": passed,
        })
    return out
