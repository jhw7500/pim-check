"""
tests/test_ssh.py - SshClient 단위 테스트
"""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssh import SshClient, SshTimeoutError, SshConnectionError


class TestSshClientRun(unittest.TestCase):
    def setUp(self):
        self.client = SshClient(host="192.168.1.100", user="root", password="root")

    @patch("ssh.subprocess.run")
    def test_run_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="hello\n")
        result = self.client.run("echo hello")
        self.assertEqual(result, "hello")

    @patch("ssh.subprocess.run")
    def test_run_strips_trailing_whitespace(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  output  \n")
        result = self.client.run("echo output")
        self.assertEqual(result, "output")

    @patch("ssh.subprocess.run")
    def test_run_timeout_raises(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sshpass", timeout=10)
        with self.assertRaises(SshTimeoutError):
            self.client.run("sleep 100")

    @patch("ssh.subprocess.run")
    def test_run_nonzero_exit_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = self.client.run("false")
        self.assertIsNone(result)

    @patch("ssh.subprocess.run")
    def test_run_builds_correct_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        self.client.run("echo ok")
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        self.assertEqual(cmd[0], "sshpass")
        self.assertIn("-e", cmd)
        self.assertNotIn("-p", cmd)

    @patch("ssh.subprocess.run")
    def test_run_uses_sshpass_env(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        self.client.run("echo ok")
        call_kwargs = mock_run.call_args[1]
        self.assertIn("env", call_kwargs)
        self.assertIn("SSHPASS", call_kwargs["env"])
        self.assertEqual(call_kwargs["env"]["SSHPASS"], self.client.password)

    @patch("ssh.subprocess.run")
    def test_run_connection_refused_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=255, stdout="")
        with self.assertRaises(SshConnectionError):
            self.client.run("echo ok")


class TestSshClientCheckConnectivity(unittest.TestCase):
    def setUp(self):
        self.client = SshClient(host="192.168.1.100")

    @patch("ssh.subprocess.run")
    def test_check_connectivity_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
        result = self.client.check_connectivity()
        self.assertTrue(result)

    @patch("ssh.subprocess.run")
    def test_check_connectivity_false(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sshpass", timeout=5)
        result = self.client.check_connectivity()
        self.assertFalse(result)


class TestSshClientPreflightCheck(unittest.TestCase):
    def setUp(self):
        self.client = SshClient(host="192.168.1.100")

    @patch("ssh.subprocess.run")
    def test_preflight_check_all_present(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="/usr/bin/jq\n")
        missing = self.client.preflight_check()
        self.assertEqual(missing, [])

    @patch("ssh.subprocess.run")
    def test_preflight_check_missing_jq(self, mock_run):
        def side_effect(cmd, **kwargs):
            # cmd[-1] 은 전체 command 문자열, cmd[-2] 는 "which"
            command_str = cmd[-1]
            if "jq" in command_str:
                return MagicMock(returncode=1, stdout="")
            return MagicMock(returncode=0, stdout="/usr/bin/journalctl\n")

        mock_run.side_effect = side_effect
        missing = self.client.preflight_check()
        self.assertIn("jq", missing)
        self.assertNotIn("journalctl", missing)


if __name__ == "__main__":
    unittest.main()
