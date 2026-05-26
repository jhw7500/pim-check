"""paramiko 경로 SSH 테스트 (mock 기반).

persistent client 모드: SshClient 인스턴스는 paramiko SSHClient 를 캐시해
여러 run() 호출이 한 connect 를 공유한다. close() 또는 컨텍스트 종료 시에만
명시적으로 닫는다. 실패 시 캐시 invalidate → 다음 호출에서 자동 재연결.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock

from ssh import SshClient, SshConnectionError, SshTimeoutError

_mock_paramiko = MagicMock()
_mock_paramiko.AuthenticationException = type("AuthenticationException", (Exception,), {})


class TestParamikoPath(unittest.TestCase):
    def _make_client(self):
        """paramiko 강제 활성화된 SshClient 생성."""
        client = SshClient(host="192.168.1.100", user="root", password="root")
        client._use_paramiko = True
        return client

    def _make_stdout(self, exit_code: int = 0, payload: bytes = b"") -> MagicMock:
        m = MagicMock()
        m.channel.recv_exit_status.return_value = exit_code
        m.read.return_value = payload
        return m

    def _wire_active_transport(self) -> None:
        """get_transport().is_active() 가 True 를 반환하도록 mock 구성."""
        transport = MagicMock()
        transport.is_active.return_value = True
        self.mock_ssh_instance.get_transport.return_value = transport

    def setUp(self):
        _mock_paramiko.reset_mock()
        self.mock_ssh_instance = MagicMock()
        _mock_paramiko.SSHClient.return_value = self.mock_ssh_instance
        _mock_paramiko.AutoAddPolicy.return_value = MagicMock()
        self._wire_active_transport()
        self._had_paramiko = "paramiko" in sys.modules
        self._orig_paramiko = sys.modules.get("paramiko")
        sys.modules["paramiko"] = _mock_paramiko

    def tearDown(self):
        if self._had_paramiko:
            sys.modules["paramiko"] = self._orig_paramiko
        else:
            sys.modules.pop("paramiko", None)

    def test_run_success(self):
        client = self._make_client()
        self.mock_ssh_instance.exec_command.return_value = (
            MagicMock(), self._make_stdout(0, b"hello\n"), MagicMock()
        )
        self.assertEqual(client.run("echo hello"), "hello")
        # persistent: run() 자체는 close 하지 않는다.
        self.mock_ssh_instance.close.assert_not_called()

    def test_run_nonzero_exit(self):
        client = self._make_client()
        self.mock_ssh_instance.exec_command.return_value = (
            MagicMock(), self._make_stdout(1, b""), MagicMock()
        )
        self.assertIsNone(client.run("false"))
        self.mock_ssh_instance.close.assert_not_called()

    def test_persistent_reuses_single_connect(self):
        """동일 클라이언트의 연속 호출은 connect() 를 한 번만 호출해야 한다."""
        client = self._make_client()
        self.mock_ssh_instance.exec_command.return_value = (
            MagicMock(), self._make_stdout(0, b"ok"), MagicMock()
        )
        for _ in range(5):
            client.run("echo ok")
        self.assertEqual(self.mock_ssh_instance.connect.call_count, 1)
        self.assertEqual(self.mock_ssh_instance.exec_command.call_count, 5)

    def test_reconnect_when_transport_inactive(self):
        """transport 가 비활성으로 바뀌면 다음 호출에서 재 connect 한다."""
        client = self._make_client()
        self.mock_ssh_instance.exec_command.return_value = (
            MagicMock(), self._make_stdout(0, b"ok"), MagicMock()
        )
        client.run("echo ok")
        # 첫 호출 후 transport 비활성으로 전환.
        self.mock_ssh_instance.get_transport.return_value.is_active.return_value = False
        client.run("echo ok")
        self.assertEqual(self.mock_ssh_instance.connect.call_count, 2)

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

    def test_exec_timeout_invalidates_cache(self):
        """exec 실패 시 cached client invalidate → 다음 호출에서 재 connect."""
        client = self._make_client()
        self.mock_ssh_instance.exec_command.side_effect = Exception("timed out")
        with self.assertRaises(SshTimeoutError):
            client.run("sleep 100")
        # 실패한 client 는 close 되어 캐시에서 비워진다.
        self.mock_ssh_instance.close.assert_called_once()
        # 다음 호출은 새 connect 를 트리거.
        self.mock_ssh_instance.exec_command.side_effect = None
        self.mock_ssh_instance.exec_command.return_value = (
            MagicMock(), self._make_stdout(0, b"ok"), MagicMock()
        )
        client.run("echo ok")
        self.assertEqual(self.mock_ssh_instance.connect.call_count, 2)

    def test_close_releases_client(self):
        """명시적 close() 호출 시 캐시가 비워지고 다음 호출은 재 connect."""
        client = self._make_client()
        self.mock_ssh_instance.exec_command.return_value = (
            MagicMock(), self._make_stdout(0, b"ok"), MagicMock()
        )
        client.run("echo ok")
        client.close()
        self.mock_ssh_instance.close.assert_called_once()
        # 재호출 시 새 connect.
        client.run("echo ok")
        self.assertEqual(self.mock_ssh_instance.connect.call_count, 2)

    def test_context_manager_closes(self):
        """with 블록 종료 시 close 호출."""
        with self._make_client() as client:
            self.mock_ssh_instance.exec_command.return_value = (
                MagicMock(), self._make_stdout(0, b"ok"), MagicMock()
            )
            client.run("echo ok")
        self.mock_ssh_instance.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
