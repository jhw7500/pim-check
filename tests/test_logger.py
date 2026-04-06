"""logger.py 테스트"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

from logger import FileLogger


class TestFileLogger(unittest.TestCase):
    def test_writes_to_file_and_stdout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileLogger(log_dir=tmpdir)
            original_stdout = sys.stdout
            with logger:
                print("hello from test")
            # stdout 복원 확인
            self.assertIs(sys.stdout, original_stdout)
            # 파일에 기록 확인
            with open(logger.filepath) as f:
                content = f.read()
            self.assertIn("hello from test", content)

    def test_file_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileLogger(log_dir=tmpdir)
            with logger:
                print("test")
            self.assertTrue(os.path.exists(logger.filepath))
            self.assertTrue(logger.filepath.endswith(".log"))

    def test_stdout_restored_on_exception(self):
        original_stdout = sys.stdout
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileLogger(log_dir=tmpdir)
            try:
                with logger:
                    raise ValueError("test error")
            except ValueError:
                pass
            self.assertIs(sys.stdout, original_stdout)


if __name__ == "__main__":
    unittest.main()
