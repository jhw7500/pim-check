"""
reporter.py - pim-check 결과 리포터
"""
from __future__ import annotations

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
