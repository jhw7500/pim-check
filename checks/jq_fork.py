"""
checks/jq_fork.py - jq 프로세스 fork 수 체크
"""
from __future__ import annotations

from checks.base_check import BaseCheck


class JqForkCheck(BaseCheck):
    name = "jq_forks"

    def collect(self, ssh, config: dict) -> dict:
        result = ssh.run("pgrep -c jq")
        if result is None:
            return {"count": 0}
        return {"count": int(result)}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        max_forks = config.get("jq", {}).get("max_forks_per_sample", 2)
        count = data["count"]
        if count > max_forks:
            return (False, f"jq forks: {count} > max {max_forks}")
        return (True, "OK")
