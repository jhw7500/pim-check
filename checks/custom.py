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

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")
