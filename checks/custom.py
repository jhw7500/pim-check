"""
checks/custom.py - YAML 정의 커스텀 명령 체크
"""
from __future__ import annotations

from checks.base_check import BaseCheck


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
                    val = int(output.strip()) if output else 0
                except ValueError:
                    val = 0
                if val < r["expected_min"]:
                    issues.append(f'{r["name"]}: {r["on_fail"]} ({val} < {r["expected_min"]})')
                continue

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")
