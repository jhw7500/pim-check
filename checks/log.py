"""
checks/log.py - 에러 로그 체크
"""
from __future__ import annotations

import re

from checks.base_check import BaseCheck


class LogCheck(BaseCheck):
    name = "logs"

    def collect(self, ssh, config: dict) -> dict:
        patterns = config.get("logs", {}).get("error_patterns", [])
        output = ssh.run("journalctl --no-pager --since '5 minutes ago' -p err 2>/dev/null")

        if output is None or "No entries" in output:
            return {"matches": []}

        matches = []
        for pattern in patterns:
            regex = re.compile(pattern, re.IGNORECASE)
            for line in output.splitlines():
                if regex.search(line):
                    matches.append({"pattern": pattern, "line": line.strip()})
                    break  # 패턴당 하나만

        return {"matches": matches}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        matches = data.get("matches", [])
        if matches:
            details = ", ".join(f"{m['pattern']}:{m['line'][:80]}" for m in matches)
            return (False, f"Error log matches: {details}")
        return (True, "OK")
