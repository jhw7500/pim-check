"""
checks/legacy.py - 레거시 파일 존재 여부 체크
"""
from __future__ import annotations

from checks.base_check import BaseCheck


class LegacyFileCheck(BaseCheck):
    name = "legacy_files"

    def collect(self, ssh, config: dict) -> dict:
        legacy_config = config.get("legacy_files", {})

        # must_not_exist: 있으면 FAIL
        found: list[str] = []
        for path in legacy_config.get("must_not_exist", []):
            result = ssh.run(f"test -e {path} && echo EXISTS")
            if result and "EXISTS" in result:
                found.append(path)

        # must_exist: 없으면 FAIL
        missing: list[str] = []
        for path in legacy_config.get("must_exist", []):
            result = ssh.run(f"test -e {path} && echo EXISTS")
            if not result or "EXISTS" not in result:
                missing.append(path)

        return {"found": found, "missing": missing}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        issues: list[str] = []

        found = data.get("found", [])
        if found:
            names = ", ".join(p.split("/")[-1] for p in found)
            issues.append(f"Should not exist: {names}")

        missing = data.get("missing", [])
        if missing:
            names = ", ".join(p.split("/")[-1] for p in missing)
            issues.append(f"Missing required: {names}")

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")
