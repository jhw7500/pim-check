"""
ssh.py - SSH 래퍼 모듈 (pim-check)

iMX8MP 타겟 보드에 SSH로 명령을 실행하는 기반 클래스.
Python 3.9 호환. paramiko 기반으로 Windows/Linux 모두 지원.

sshpass 불필요. paramiko가 없으면 subprocess + sshpass 폴백.
"""
from __future__ import annotations

import os
import subprocess
import time


class SshTimeoutError(Exception):
    """SSH 명령 실행 시간 초과"""


class SshConnectionError(Exception):
    """SSH 접속 실패"""


def _has_paramiko() -> bool:
    try:
        import paramiko  # noqa: F401
        return True
    except ImportError:
        return False


class SshClient:
    """SSH 클라이언트. paramiko 우선, 없으면 sshpass 폴백."""

    def __init__(
        self,
        host: str,
        user: str = "root",
        password: str = "root",
        connect_timeout: int = 5,
        command_timeout: int = 10,
    ) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self._use_paramiko = _has_paramiko()

    def run(self, command: str, retries: int = 0) -> str | None:
        """타겟에서 명령을 실행하고 stdout을 반환한다.

        Args:
            command: 실행할 셸 명령
            retries: 접속 실패 시 재시도 횟수 (기본 0)

        Returns:
            str: 성공 시 stdout (strip 적용)
            None: 명령 exit code != 0

        Raises:
            SshTimeoutError: command_timeout 초과
            SshConnectionError: retries 소진 후에도 접속 실패
        """
        last_error = None
        for attempt in range(1 + retries):
            try:
                return self._run_once(command)
            except SshConnectionError as e:
                last_error = e
                if attempt < retries:
                    wait = min(2 ** attempt, 10)
                    time.sleep(wait)
        raise last_error

    def _run_once(self, command: str) -> str | None:
        """단일 SSH 명령 실행."""
        if self._use_paramiko:
            return self._run_paramiko(command)
        return self._run_subprocess(command)

    def _run_paramiko(self, command: str) -> str | None:
        """paramiko를 사용한 SSH 명령 실행."""
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client.connect(
                hostname=self.host,
                username=self.user,
                password=self.password,
                timeout=self.connect_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except paramiko.AuthenticationException:
            raise SshConnectionError(
                f"SSH authentication failed to {self.user}@{self.host}"
            )
        except Exception as e:
            err_str = str(e).lower()
            if "timed out" in err_str or "timeout" in err_str:
                raise SshTimeoutError(
                    f"SSH connect timed out after {self.connect_timeout}s to {self.user}@{self.host}"
                )
            raise SshConnectionError(
                f"SSH connection failed to {self.user}@{self.host}: {e}"
            )

        try:
            _stdin, stdout, stderr = client.exec_command(
                command, timeout=self.command_timeout
            )
            exit_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode("utf-8", errors="replace").strip()
        except Exception as e:
            err_str = str(e).lower()
            if "timed out" in err_str or "timeout" in err_str:
                raise SshTimeoutError(
                    f"Command timed out after {self.command_timeout}s: {command}"
                )
            raise SshConnectionError(
                f"SSH command failed on {self.user}@{self.host}: {e}"
            )
        finally:
            client.close()

        if exit_code != 0:
            return None
        return output

    def _run_subprocess(self, command: str) -> str | None:
        """sshpass + subprocess를 사용한 SSH 명령 실행 (Linux 폴백)."""
        cmd = [
            "sshpass", "-e",
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-o", "LogLevel=ERROR",
            f"{self.user}@{self.host}",
            command,
        ]
        env = {**os.environ, "SSHPASS": self.password}

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise SshTimeoutError(
                f"Command timed out after {self.command_timeout}s: {command}"
            )

        if result.returncode == 255:
            raise SshConnectionError(
                f"SSH connection failed to {self.user}@{self.host}"
            )

        if result.returncode != 0:
            return None

        return result.stdout.strip()

    def check_connectivity(self) -> bool:
        """타겟 접속 가능 여부를 확인한다."""
        try:
            result = self.run("echo ok")
            return result == "ok"
        except Exception:
            return False

    def preflight_check(self) -> list[str]:
        """타겟에서 필수 도구 존재 여부를 확인한다."""
        required_tools = ["jq", "journalctl"]
        missing = []
        for tool in required_tools:
            try:
                result = self.run(f"which {tool}")
                if result is None:
                    missing.append(tool)
            except (SshTimeoutError, SshConnectionError):
                missing.append(tool)
        return missing
