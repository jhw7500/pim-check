"""
notifier.py — FAIL 발생 시 외부 알림

Webhook(Slack, Discord 등) 또는 커스텀 URL로 결과를 POST한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path


WEBHOOK_ENV = "PIM_CHECK_WEBHOOK_URL"


def send_webhook(
    url: str,
    results: list,
    case_name: str | None,
    host: str = "",
    status: str = "FAIL",
    details_url: str = "",
) -> bool:
    """결과를 webhook URL로 POST한다. 성공 시 True."""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed_checks = [r for r in results if not r["passed"] and "known_issue" not in r]

    text = f"pim-check {status}: {case_name or 'healthcheck'} on {host} ({passed}/{total})"
    if details_url:
        text = f"{text} — {details_url}"
    payload = {
        "text": text,
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
    if details_url:
        payload["details_url"] = details_url

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


def load_ci_failure_results(path: str | Path) -> list[dict]:
    """comprehensive 결과를 notifier 공통 형식으로 변환한다.

    워크플로가 검증 전에 실패해 결과 파일이 없거나 손상된 경우에도 인프라 실패
    한 건을 만들어 webhook 자체는 보낼 수 있게 한다.
    """
    result_path = Path(path)
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [{"name": "workflow", "passed": False, "reason": "results file not found"}]
    except json.JSONDecodeError:
        return [{"name": "workflow", "passed": False, "reason": "results file is not valid JSON"}]
    except (OSError, UnicodeError):
        return [{"name": "workflow", "passed": False, "reason": "results file could not be read"}]

    if not isinstance(raw, list) or not raw:
        return [{"name": "workflow", "passed": False, "reason": "results file contains no scenarios"}]

    results = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            results.append({
                "name": f"scenario[{index}]",
                "passed": False,
                "reason": "result entry is not an object",
            })
            continue

        name = str(item.get("name", f"scenario[{index}]"))
        if "passed" in item:
            normalized = {
                "name": name,
                "passed": item.get("passed") is True,
                "reason": str(item.get("reason", "")),
            }
            if "known_issue" in item:
                normalized["known_issue"] = item["known_issue"]
            results.append(normalized)
            continue

        passed = str(item.get("result", "")).upper() == "PASS"
        reason = ""
        if not passed:
            reason = str(item.get("reason") or (
                f"expected={item.get('expected_hex', '?')} actual={item.get('actual', '?')}"
            ))
        results.append({"name": name, "passed": passed, "reason": reason})
    return results


def main(argv=None) -> int:
    """GitHub Actions 등에서 결과 파일을 읽어 실패 webhook을 전송한다."""
    parser = argparse.ArgumentParser(description="Send a pim-check failure webhook")
    parser.add_argument("--results", default="comprehensive_results.json")
    parser.add_argument("--case", default="comprehensive")
    parser.add_argument("--host", default=os.environ.get("TARGET_HOST", ""))
    parser.add_argument("--run-url", default="")
    args = parser.parse_args(argv)

    webhook_url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not webhook_url:
        print(f"ERROR: {WEBHOOK_ENV} is not configured", file=sys.stderr)
        return 2

    results = load_ci_failure_results(args.results)
    sent = send_webhook(
        webhook_url,
        results,
        args.case,
        args.host,
        "FAIL",
        details_url=args.run_url,
    )
    if not sent:
        print("ERROR: failure webhook could not be delivered", file=sys.stderr)
        return 1
    print("Failure webhook delivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
