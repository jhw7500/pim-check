"""
logger.py — 실행 로그 파일 관리

테스트 실행 로그를 reports/logs/에 저장한다.
stdout에 출력되는 것과 동일한 내용 + 타임스탬프.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime


class FileLogger:
    """stdout에 출력하면서 동시에 파일에도 기록하는 래퍼."""

    def __init__(self, log_dir: str = "reports/logs"):
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filepath = os.path.join(log_dir, f"run_{ts}.log")
        self._file = open(self.filepath, "w")
        self._stdout = sys.stdout

    def write(self, text: str) -> int:
        self._stdout.write(text)
        self._file.write(text)
        self._file.flush()
        return len(text)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        self._file.close()
        sys.stdout = self._stdout

    def __enter__(self):
        sys.stdout = self
        return self

    def __exit__(self, *args):
        self.close()
