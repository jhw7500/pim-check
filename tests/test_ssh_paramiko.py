"""paramiko 경로 SSH 테스트 (mock 기반)"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch, MagicMock

from ssh import SshClient, SshTimeoutError, SshConnectionError

# paramiko mock: 테스트 클래스 내에서만 사용
_mock_paramiko = MagicMock()
_mock_paramiko.AuthenticationException = type("AuthenticationException", (Exception,), {})


class TestParamikoPath(unittest.TestCase):
    def _make_client(self):
        """paramiko 강제 활성화된 SshClient 생성."""
        client = SshClient(host="192.168.1.100", user="root", password="root")
        client._use_paramiko = True
        return client

    def setUp(self):
        _mock_paramiko.reset_mock()
        self.mock_ssh_instance = MagicMock()
        _mock_paramiko.SSHClient.return_value = self.mock_ssh_instance
        _mock_paramiko.AutoAddPolicy.return_value = MagicMock()
        # paramiko mock을 sys.modules에 주입
        self._had_paramiko = "paramiko" in sys.modules
        self._orig_paramiko = sys.modules.get("paramiko")
        sys.modules["paramiko"] = _mock_paramiko

    def tearDown(self):
        # 원래 상태로 복원
        if self._had_paramiko:
            sys.modules["paramiko"] = self._orig_paramiko
        else:
            sys.modules.pop("paramiko", None)

    def test_run_success(self):
        client = self._make_client()
        mock_stdout = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stdout.read.return_value = b"hello\n"
        self.mock_ssh_instance.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

        result = client.run("echo hello")
        self.assertEqual(result, "hello")
        self.mock_ssh_instance.close.assert_called_once()

    def test_run_nonzero_exit(self):
        client = self._make_client()
        mock_stdout = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 1
        mock_stdout.read.return_value = b""
        self.mock_ssh_instance.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

        result = client.run("false")
        self.assertIsNone(result)
        self.mock_ssh_instance.close.assert_called_once()

    def test_auth_failure(self):
        client = self._make_client()
        self.mock_ssh_instance.connect.side_effect = _mock_paramiko.AuthenticationException("auth failed")

        with self.assertRaises(SshConnectionError):
            client.run("echo ok")

    def test_connect_timeout(self):
        client = self._make_client()
        self.mock_ssh_instance.connect.side_effect = OSError("timed out")

        with self.assertRaises(SshTimeoutError):
            client.run("echo ok")

    def test_exec_timeout(self):
        client = self._make_client()
        self.mock_ssh_instance.exec_command.side_effect = Exception("timed out")

        with self.assertRaises(SshTimeoutError):
            client.run("sleep 100")
        self.mock_ssh_instance.close.assert_called_once()

    def test_close_always_called(self):
        client = self._make_client()
        mock_stdout = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stdout.read.return_value = b"ok"
        self.mock_ssh_instance.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

        client.run("echo ok")
        self.mock_ssh_instance.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
