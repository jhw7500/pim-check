"""
junit_reporter.py — JUnit XML 리포트 생성

Jenkins/GitLab CI에서 테스트 결과를 인식하는 JUnit XML 형식.
외부 의존성 없음 (xml.etree 사용).
"""
from __future__ import annotations

import os
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring


def generate_junit_xml(
    results: list,
    case_name: str | None,
    host: str = "",
    samples_collected: int = 1,
    samples_total: int = 1,
) -> str:
    """결과를 JUnit XML 문자열로 변환."""
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failures = total - passed
    case_display = case_name or "healthcheck"
    timestamp = datetime.now().isoformat()

    testsuite = Element("testsuite", {
        "name": f"pim-check.{case_display}",
        "tests": str(total),
        "failures": str(failures),
        "errors": "0",
        "time": str(sum(r.get("duration_ms", 0) for r in results) / 1000),
        "timestamp": timestamp,
        "hostname": host,
    })

    for r in results:
        tc = SubElement(testsuite, "testcase", {
            "name": r["name"],
            "classname": f"pim-check.{case_display}",
            "time": str(r.get("duration_ms", 0) / 1000),
        })
        if not r["passed"]:
            if "known_issue" in r:
                SubElement(tc, "skipped", {
                    "message": f"Known issue: {r['known_issue']}",
                })
            else:
                failure = SubElement(tc, "failure", {
                    "message": r.get("reason", ""),
                    "type": "AssertionError",
                })
                failure.text = r.get("reason", "")

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(testsuite, encoding="unicode")


def save_junit_xml(
    results: list,
    case_name: str | None,
    host: str = "",
    samples_collected: int = 1,
    samples_total: int = 1,
    output_dir: str = "reports",
) -> str:
    """JUnit XML을 파일로 저장. 경로 반환."""
    os.makedirs(output_dir, exist_ok=True)
    xml = generate_junit_xml(results, case_name, host, samples_collected, samples_total)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    case_slug = case_name or "healthcheck"
    filename = f"{case_slug}_{ts}.xml"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w") as f:
        f.write(xml)
    return filepath
