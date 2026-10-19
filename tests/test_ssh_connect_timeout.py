"""
tests/test_ssh_connect_timeout.py — 호출 단위 connect timeout (#178).

재부팅 대기 확인은 짧은 접속 제한 시간을 써야 하지만, 일반 명령 경로의
connect_timeout(기본 15s)은 그대로여야 한다. paramiko(persistent client)와
sshpass+subprocess 폴백 양쪽에서 실제로 쓰이는 접속 인자를 본다(네트워크 없음).
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from ssh import SshClient


class TestSubprocessConnectTimeout(unittest.TestCase):
    def setUp(self):
        self.client = SshClient(host="192.0.2.1")
        self.client._use_paramiko = False

    @patch("ssh.subprocess.run")
    def test_probe_uses_given_timeout_and_normal_run_keeps_default(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")

        self.assertTrue(self.client.check_connectivity(connect_timeout=3))
        probe_cmd = mock_run.call_args[0][0]
        self.client.run("uptime")
        normal_cmd = mock_run.call_args[0][0]

        self.assertIn("ConnectTimeout=3", probe_cmd)
        self.assertIn("ConnectTimeout=15", normal_cmd)
        self.assertEqual(self.client.connect_timeout, 15)


class TestParamikoConnectTimeout(unittest.TestCase):
    def setUp(self):
        self.paramiko = MagicMock()
        self.paramiko.AuthenticationException = type("AuthenticationException", (Exception,), {})
        self.ssh_instance = MagicMock()
        self.paramiko.SSHClient.return_value = self.ssh_instance
        transport = MagicMock()
        transport.is_active.return_value = True
        self.ssh_instance.get_transport.return_value = transport
        stdout = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b"ok"
        self.ssh_instance.exec_command.return_value = (MagicMock(), stdout, MagicMock())
        patcher = patch.dict(sys.modules, {"paramiko": self.paramiko})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_probe_connects_with_given_timeout_then_normal_run_uses_default(self):
        client = SshClient(host="192.0.2.1")
        client._use_paramiko = True

        self.assertTrue(client.check_connectivity(connect_timeout=3))
        self.assertEqual(self.ssh_instance.connect.call_args.kwargs["timeout"], 3)

        client.close()  # 다음 호출이 새로 connect 하게
        client.run("uptime")
        self.assertEqual(self.ssh_instance.connect.call_args.kwargs["timeout"], 15)
        self.assertEqual(self.ssh_instance.connect.call_count, 2)

    def test_probe_also_shortens_banner_wait_normal_run_keeps_30s(self):
        """sshd 기동 중 접속(banner 미전송)에서 probe 가 banner 30s 대기에 묶이지 않는다 (#178)."""
        client = SshClient(host="192.0.2.1")
        client._use_paramiko = True

        self.assertTrue(client.check_connectivity(connect_timeout=3))
        self.assertEqual(self.ssh_instance.connect.call_args.kwargs["banner_timeout"], 3)

        client.close()
        client.run("uptime")
        self.assertEqual(self.ssh_instance.connect.call_args.kwargs["banner_timeout"], 30)


if __name__ == "__main__":
    unittest.main()
