"""
ssh.py - SSH 래퍼 모듈 (pim-check)

iMX8MP 타겟 보드에 SSH로 명령을 실행하는 기반 클래스.
Python 3.9 호환.
"""
from __future__ import annotations

import os
import subprocess


class SshTimeoutError(Exception):
    """SSH 명령 실행 시간 초과"""


class SshConnectionError(Exception):
    """SSH 접속 실패 (returncode 255)"""


class SshClient:
    """sshpass를 이용한 SSH 클라이언트"""

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

    def run(self, command: str) -> str | None:
        """타겟에서 명령을 실행하고 stdout을 반환한다.

        Returns:
            str: 성공 시 stdout (strip 적용)
            None: returncode != 0 (255 제외)

        Raises:
            SshTimeoutError: command_timeout 초과
            SshConnectionError: returncode 255 (접속 실패)
        """
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
        """타겟 접속 가능 여부를 확인한다.

        Returns:
            True: 접속 및 응답 정상
            False: 접속 실패 또는 예외 발생
        """
        try:
            result = self.run("echo ok")
            return result == "ok"
        except Exception:
            return False

    def preflight_check(self) -> list[str]:
        """타겟에서 필수 도구 존재 여부를 확인한다.

        Returns:
            list: 누락된 도구 이름 목록 (모두 있으면 빈 리스트)
        """
        required_tools = ["jq", "journalctl"]
        missing = []
        for tool in required_tools:
            result = self.run(f"which {tool}")
            if result is None:
                missing.append(tool)
        return missing
