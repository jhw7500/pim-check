"""stream.py 테스트"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from stream import StreamRunner, format_sse


class TestFormatSse(unittest.TestCase):
    def test_basic_format(self):
        result = format_sse("check_result", {"check": "thermal", "passed": True})
        self.assertTrue(result.startswith("event: check_result\n"))
        self.assertIn("data: ", result)
        self.assertTrue(result.endswith("\n\n"))

    def test_unicode(self):
        result = format_sse("phase", {"message": "연결 중..."})
        self.assertIn("연결 중...", result)


class TestStreamRunner(unittest.TestCase):
    def test_emit_puts_to_queue(self):
        runner = StreamRunner("test", "192.168.0.5", profiles_dir="profiles")
        runner._emit("test_event", {"key": "value"})
        event = runner.events.get(timeout=1)
        self.assertEqual(event["event"], "test_event")
        self.assertEqual(event["data"]["key"], "value")
        self.assertIn("timestamp", event["data"])

    @patch("stream.load_profile")
    @patch("stream.SshClient")
    def test_unreachable_emits_error_done(self, mock_ssh_cls, mock_load):
        mock_load.return_value = {
            "target": {"host": "192.168.99.99", "user": "root", "password": "root"},
            "monitor": {"duration_sec": 0},
        }
        mock_ssh = MagicMock()
        mock_ssh.check_connectivity.return_value = False
        mock_ssh_cls.return_value = mock_ssh

        runner = StreamRunner("test", "192.168.99.99", profiles_dir="profiles")
        runner._run()

        events = []
        while not runner.events.empty():
            events.append(runner.events.get())

        event_types = [e["event"] for e in events]
        self.assertIn("start", event_types)
        self.assertIn("error", event_types)
        self.assertIn("done", event_types)
        done = next(e for e in events if e["event"] == "done")
        self.assertEqual(done["data"]["status"], "ERROR")

    @patch("stream.load_profile")
    @patch("stream.SshClient")
    @patch("stream.Engine")
    @patch("stream.SetupManager")
    @patch("stream.append_result")
    @patch("stream.save_dashboard")
    def test_success_emits_all_phases(self, mock_save, mock_append,
                                      mock_setup_cls, mock_engine_cls,
                                      mock_ssh_cls, mock_load):
        mock_load.return_value = {
            "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
            "monitor": {"duration_sec": 0},
            "checks": {},
        }
        mock_ssh = MagicMock()
        mock_ssh.check_connectivity.return_value = True
        mock_ssh.preflight_check.return_value = []
        mock_ssh_cls.return_value = mock_ssh

        mock_engine = MagicMock()
        mock_check = MagicMock()
        mock_check.name = "test_check"
        mock_check.collect.return_value = {}
        mock_check.validate.return_value = (True, "OK")
        mock_engine.checks = [mock_check]
        mock_engine_cls.return_value = mock_engine

        runner = StreamRunner("test", "192.168.0.5",
                              profiles_dir="profiles", reports_dir="/tmp")
        runner._run()

        events = []
        while not runner.events.empty():
            events.append(runner.events.get())

        event_types = [e["event"] for e in events]
        self.assertIn("start", event_types)
        self.assertIn("phase", event_types)
        self.assertIn("check_start", event_types)
        self.assertIn("check_result", event_types)
        self.assertIn("done", event_types)

        done = next(e for e in events if e["event"] == "done")
        self.assertEqual(done["data"]["status"], "PASS")

        check_result = next(e for e in events if e["event"] == "check_result")
        self.assertTrue(check_result["data"]["passed"])
        self.assertIn("duration_ms", check_result["data"])


if __name__ == "__main__":
    unittest.main()
