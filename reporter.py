"""
reporter.py - pim-check 결과 리포터
"""
from __future__ import annotations

import json
import os
from datetime import datetime


class Reporter:
    def format(
        self,
        results: list,
        case_name: str | None,
        samples_collected: int = 1,
        samples_total: int = 1,
    ) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Header
        header = f"=== pim-check Report ({timestamp})"
        if case_name is not None:
            header += f" — Case: {case_name}"
        header += " ==="

        # Summary
        total = len(results)
        passed = sum(1 for r in results if r["passed"])
        status = "PASS" if passed == total else "FAIL"
        summary = f"Result: {status} ({passed}/{total} checks passed)"
        samples_line = f"Samples: {samples_collected}/{samples_total}"

        # Per-result lines
        detail_lines = []
        for r in results:
            if r["passed"]:
                line = f"[+] {r['name']}: PASS"
            else:
                line = f"[X] {r['name']}: FAIL"
            if r.get("reason", "OK") != "OK":
                line += f" — {r['reason']}"
            detail_lines.append(line)

        lines = [header, "", summary, samples_line, ""] + detail_lines + [""]
        return "\n".join(lines)

    def to_json(
        self,
        results: list,
        case_name: str | None,
        host: str = "",
        samples_collected: int = 1,
        samples_total: int = 1,
    ) -> dict:
        """결과를 JSON-serializable dict로 변환."""
        total = len(results)
        passed = sum(1 for r in results if r["passed"])
        return {
            "timestamp": datetime.now().isoformat(),
            "host": host,
            "case": case_name,
            "result": "PASS" if passed == total else "FAIL",
            "passed": passed,
            "total": total,
            "samples_collected": samples_collected,
            "samples_total": samples_total,
            "checks": [
                {
                    "name": r["name"],
                    "passed": r["passed"],
                    "reason": r.get("reason", ""),
                }
                for r in results
            ],
        }

    def save_json(
        self,
        results: list,
        case_name: str | None,
        host: str = "",
        samples_collected: int = 1,
        samples_total: int = 1,
        output_dir: str = "reports",
    ) -> str:
        """결과를 JSON 파일로 저장. 파일 경로를 반환."""
        os.makedirs(output_dir, exist_ok=True)
        data = self.to_json(results, case_name, host, samples_collected, samples_total)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        case_slug = case_name or "healthcheck"
        filename = f"{case_slug}_{ts}.json"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return filepath
