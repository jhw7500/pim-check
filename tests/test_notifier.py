"""notifier.py 테스트"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import notifier
from notifier import send_webhook


class TestSendWebhook(unittest.TestCase):
    @patch("notifier.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        results = [
            {"name": "process", "passed": True, "reason": "OK"},
            {"name": "thermal", "passed": False, "reason": "too hot"},
        ]
        ok = send_webhook("https://hooks.example.com/test", results, "720p_2ch", "192.168.0.5")
        self.assertTrue(ok)

        # payload 검증
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertIn("FAIL", payload["text"])
        self.assertEqual(payload["case"], "720p_2ch")
        self.assertEqual(len(payload["failed_checks"]), 1)
        self.assertEqual(payload["failed_checks"][0]["name"], "thermal")

    @patch("notifier.urllib.request.urlopen")
    def test_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("connection refused")

        results = [{"name": "check", "passed": False, "reason": "fail"}]
        ok = send_webhook("https://hooks.example.com/bad", results, "test")
        self.assertFalse(ok)

    @patch("notifier.urllib.request.urlopen")
    def test_known_issue_excluded(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        results = [
            {"name": "thermal", "passed": False, "reason": "too hot", "known_issue": "HW issue"},
        ]
        send_webhook("https://hooks.example.com/test", results, "test")

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        # known_issue는 failed_checks에서 제외
        self.assertEqual(len(payload["failed_checks"]), 0)


class TestCiFailureNotification(unittest.TestCase):
    def test_comprehensive_results_preserve_failure_evidence(self):
        payload = [
            {
                "name": "pass_case",
                "result": "PASS",
                "expected_hex": "0x020x90",
                "actual": "0x020x90",
            },
            {
                "name": "fail_case",
                "result": "FAIL",
                "expected_hex": "0x020x90",
                "actual": "0x020x99",
            },
        ]
        with TemporaryDirectory() as tmpdir:
            result_file = Path(tmpdir) / "comprehensive_results.json"
            result_file.write_text(json.dumps(payload), encoding="utf-8")

            results = notifier.load_ci_failure_results(result_file)

        self.assertEqual(
            results,
            [
                {"name": "pass_case", "passed": True, "reason": ""},
                {
                    "name": "fail_case",
                    "passed": False,
                    "reason": "expected=0x020x90 actual=0x020x99",
                },
            ],
        )

    def test_comprehensive_results_preserve_runner_error(self):
        payload = [
            {
                "name": "timeout_case",
                "result": "EXCEPTION_TIMEOUT",
                "error": "Command timed out after 60 seconds",
            },
        ]
        with TemporaryDirectory() as tmpdir:
            result_file = Path(tmpdir) / "comprehensive_results.json"
            result_file.write_text(json.dumps(payload), encoding="utf-8")

            results = notifier.load_ci_failure_results(result_file)

        self.assertEqual(
            results,
            [
                {
                    "name": "timeout_case",
                    "passed": False,
                    "reason": "Command timed out after 60 seconds",
                },
            ],
        )

    def test_missing_results_become_infrastructure_failure(self):
        with TemporaryDirectory() as tmpdir:
            result_file = Path(tmpdir) / "comprehensive_results.json"

            results = notifier.load_ci_failure_results(result_file)

        self.assertEqual(
            results,
            [{"name": "workflow", "passed": False, "reason": "results file not found"}],
        )

    def test_invalid_json_becomes_infrastructure_failure(self):
        with TemporaryDirectory() as tmpdir:
            result_file = Path(tmpdir) / "comprehensive_results.json"
            result_file.write_text("{", encoding="utf-8")

            results = notifier.load_ci_failure_results(result_file)

        self.assertEqual(
            results,
            [{"name": "workflow", "passed": False, "reason": "results file is not valid JSON"}],
        )

    @patch("notifier.urllib.request.urlopen")
    def test_cli_posts_summary_and_actions_link(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with TemporaryDirectory() as tmpdir:
            result_file = Path(tmpdir) / "comprehensive_results.json"
            result_file.write_text(
                json.dumps([
                    {
                        "name": "fail_case",
                        "result": "FAIL",
                        "expected_hex": "0x020x90",
                        "actual": "0x020x99",
                    },
                ]),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"PIM_CHECK_WEBHOOK_URL": "https://hooks.example.com/ci"}):
                exit_code = notifier.main([
                    "--results", str(result_file),
                    "--case", "comprehensive",
                    "--host", "192.168.0.5",
                    "--run-url", "https://github.example/actions/runs/123",
                ])

        self.assertEqual(exit_code, 0)
        request = mock_urlopen.call_args.args[0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["failed_checks"], [
            {"name": "fail_case", "reason": "expected=0x020x90 actual=0x020x99"},
        ])
        self.assertEqual(sent["details_url"], "https://github.example/actions/runs/123")
        self.assertIn("https://github.example/actions/runs/123", sent["text"])

    def test_cli_fails_without_configured_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            exit_code = notifier.main([])

        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
