"""
checks/custom.py - YAML 정의 커스텀 명령 체크
"""
from __future__ import annotations

from checks.base_check import BaseCheck

# 타겟에서 실행 금지 패턴 (root로 실행되므로 주의)
_DANGEROUS_PATTERNS = ["rm -rf", "mkfs", "dd if=", "fdisk", "> /dev/sd", "> /dev/mmc"]


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

            # expected_min: 숫자 최소값
            if r["expected_min"] is not None:
                try:
                    val = int(output.strip()) if output else None
                except ValueError:
                    val = None
                if val is None:
                    issues.append(f'{r["name"]}: {r["on_fail"]} (non-numeric output: {output})')
                    continue
                if val < r["expected_min"]:
                    issues.append(f'{r["name"]}: {r["on_fail"]} ({val} < {r["expected_min"]})')
                continue

            # expected도 expected_min도 없으면 — 명령 실행 성공 여부만 확인
            if output is None:
                issues.append(f'{r["name"]}: {r["on_fail"]} (command returned no output)')
                continue

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")


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
        if expected is not None:
            # actual 은 위에서 정규화(문자열이면 strip, 아니면 원값) — 비문자열 output 도 안전.
            passed = actual is not None and str(actual) == str(expected).strip()
            exp_disp = str(expected)
        elif expected_min is not None:
            try:
                val = int(actual) if actual is not None else None
            except (ValueError, TypeError):
                val = None
            passed = val is not None and val >= expected_min
            exp_disp = f">= {expected_min}"
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
