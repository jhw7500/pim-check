"""통합 테스트 — CLI 전체 흐름을 실제 profiles/로 테스트"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pim_check.py")


def _run_cli(*args) -> tuple:
    """CLI를 subprocess로 실행하고 (returncode, stdout, stderr)를 반환."""
    cmd = [sys.executable, SCRIPT] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.returncode, result.stdout, result.stderr


class TestCLIBasic(unittest.TestCase):
    def test_version(self):
        code, out, _ = _run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn("pim-check", out)

    def test_help(self):
        code, out, _ = _run_cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("--case", out)
        self.assertIn("--junit", out)

    def test_list_cases(self):
        code, out, _ = _run_cli("--list")
        self.assertEqual(code, 0)
        self.assertIn("720p_2ch", out)

    def test_list_include_generated(self):
        code, out, _ = _run_cli("--list", "--include-generated")
        self.assertEqual(code, 0)
        self.assertIn("gen_", out)

    def test_list_tag_filter(self):
        code, out, _ = _run_cli("--list", "--tag", "smoke")
        self.assertEqual(code, 0)
        self.assertIn("720p_2ch", out)
        self.assertNotIn("fault_", out)


class TestCLIGenerate(unittest.TestCase):
    def test_validate_schema(self):
        code, out, _ = _run_cli("--validate-schema")
        self.assertEqual(code, 0)
        self.assertIn("OK", out)

    def test_generate(self):
        code, out, _ = _run_cli("--generate")
        self.assertEqual(code, 0)
        self.assertIn("case(s) generated", out)


class TestCLIConfig(unittest.TestCase):
    def test_init_config(self):
        code, out, _ = _run_cli("--init-config")
        self.assertEqual(code, 0)
        self.assertIn("Config", out)


class TestCLICompare(unittest.TestCase):
    def test_compare(self):
        code, out, _ = _run_cli("--compare")
        self.assertEqual(code, 0)
        # 히스토리가 있든 없든 크래시하지 않아야 함


class TestCLIExport(unittest.TestCase):
    def test_export_csv(self):
        code, out, _ = _run_cli("--export-csv")
        self.assertEqual(code, 0)
        self.assertIn("CSV", out)

    def test_history_report(self):
        code, out, _ = _run_cli("--history-report")
        self.assertEqual(code, 0)
        self.assertIn("Dashboard", out)


if __name__ == "__main__":
    unittest.main()
