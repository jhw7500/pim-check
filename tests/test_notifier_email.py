"""notifier_email.py 테스트"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from notifier_email import send_email, send_fail_email


class TestSendEmail(unittest.TestCase):
    @patch("notifier_email.smtplib.SMTP")
    def test_tls_success(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        ok = send_email("smtp.test.com", 587, "a@b.com", "pass",
                        ["c@d.com"], "Subject", "Body", use_tls=True)
        self.assertTrue(ok)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("a@b.com", "pass")
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    @patch("notifier_email.smtplib.SMTP")
    def test_no_tls(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        ok = send_email("smtp.test.com", 25, "a@b.com", "pass",
                        ["c@d.com"], "Subject", "Body", use_tls=False)
        self.assertTrue(ok)
        mock_server.starttls.assert_not_called()

    @patch("notifier_email.smtplib.SMTP")
    def test_connection_error(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = OSError("connection refused")
        ok = send_email("bad.host", 587, "a@b.com", "pass",
                        ["c@d.com"], "Subject", "Body")
        self.assertFalse(ok)


class TestSendFailEmail(unittest.TestCase):
    @patch("notifier_email.send_email")
    def test_known_issue_excluded(self, mock_send):
        mock_send.return_value = True
        results = [
            {"name": "thermal", "passed": False, "reason": "too hot", "known_issue": "HW"},
            {"name": "process", "passed": False, "reason": "missing"},
        ]
        cfg = {"smtp_host": "smtp.test.com", "smtp_port": 587,
               "sender": "a@b.com", "password": "p", "recipients": ["c@d.com"]}
        send_fail_email(cfg, results, "test_case", "192.168.0.5")

        call_args = mock_send.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][5]
        self.assertIn("process", body)
        self.assertNotIn("thermal", body)

    @patch("notifier_email.send_email")
    def test_empty_recipients(self, mock_send):
        mock_send.return_value = True
        results = [{"name": "check", "passed": False, "reason": "fail"}]
        cfg = {"sender": "a@b.com", "password": "p", "recipients": []}
        send_fail_email(cfg, results, "test", "host")
        mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
