"""
history.py — 테스트 결과 히스토리 관리 (JSONL)

결과를 reports/history.jsonl에 한 줄씩 추가하여
시간 경과에 따른 추이를 추적한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime


def append_result(
    results: list,
    case_name: str | None,
    host: str = "",
    samples_collected: int = 1,
    samples_total: int = 1,
    history_dir: str = "reports",
) -> str:
    """결과를 history.jsonl에 추가한다. 파일 경로를 반환."""
    os.makedirs(history_dir, exist_ok=True)
    filepath = os.path.join(history_dir, "history.jsonl")

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    entry = {
        "timestamp": datetime.now().isoformat(),
        "host": host,
        "case": case_name,
        "result": "PASS" if passed == total else "FAIL",
        "passed": passed,
        "total": total,
        "samples_collected": samples_collected,
        "samples_total": samples_total,
        "checks": {r["name"]: r["passed"] for r in results},
    }

    with open(filepath, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return filepath


def read_history(history_dir: str = "reports", case_filter: str | None = None) -> list[dict]:
    """히스토리를 읽어 dict 리스트로 반환한다."""
    filepath = os.path.join(history_dir, "history.jsonl")
    if not os.path.exists(filepath):
        return []

    entries = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if case_filter and entry.get("case") != case_filter:
                continue
            entries.append(entry)
    return entries
