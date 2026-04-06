"""notifier.py 테스트"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch, MagicMock

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


if __name__ == "__main__":
    unittest.main()
