"""
notifier.py — FAIL 발생 시 외부 알림

Webhook(Slack, Discord 등) 또는 커스텀 URL로 결과를 POST한다.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error


def send_webhook(
    url: str,
    results: list,
    case_name: str | None,
    host: str = "",
    status: str = "FAIL",
) -> bool:
    """결과를 webhook URL로 POST한다. 성공 시 True."""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed_checks = [r for r in results if not r["passed"] and "known_issue" not in r]

    payload = {
        "text": f"pim-check {status}: {case_name or 'healthcheck'} on {host} ({passed}/{total})",
        "case": case_name,
        "host": host,
        "status": status,
        "passed": passed,
        "total": total,
        "failed_checks": [
            {"name": r["name"], "reason": r.get("reason", "")}
            for r in failed_checks
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False
