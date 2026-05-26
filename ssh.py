"""
ssh.py - SSH 래퍼 모듈 (pim-check)

iMX8MP 타겟 보드에 SSH로 명령을 실행하는 기반 클래스.
Python 3.9 호환. paramiko 기반으로 Windows/Linux 모두 지원.

sshpass 불필요. paramiko가 없으면 subprocess + sshpass 폴백.
"""
from __future__ import annotations

import atexit
import os
import subprocess
import threading
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
    """SSH 클라이언트. paramiko 우선(persistent connection), 없으면 sshpass 폴백.

    paramiko 경로는 단일 SSHClient 를 캐싱해 run() 마다 TCP/auth 핸드셰이크를
    재사용한다. DUT 측 sshd auth fork 누적(체크당 수십~수백 ssh handshake)을
    한 connect 로 줄여 카메라 처리 jitter 와 wtmp/journald 부담을 완화한다.
    """

    def __init__(
        self,
        host: str,
        user: str = "root",
        password: str = "root",
        connect_timeout: int = 15,
        command_timeout: int = 600,
    ) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self._use_paramiko = _has_paramiko()
        # paramiko persistent client (None 이면 다음 호출에서 connect).
        self._client = None  # type: ignore[assignment]
        # _client 갱신/invalidate 직렬화 — 단일 스레드 caller 에서도 atexit 와의
        # 경합을 막아 transport 가 close 중 새로 만들어지는 race 를 차단한다.
        self._client_lock = threading.Lock()
        # 인터프리터 종료 시 paramiko transport thread/socket 정리.
        # close() 는 멱등이라 명시적 호출 / atexit 가 중복돼도 안전.
        atexit.register(self.close)

    def run(self, command: str, retries: int = 0, retry_wait: int | None = None) -> str | None:
        """타겟에서 명령을 실행하고 stdout을 반환한다.

        Args:
            command: 실행할 셸 명령
            retries: 접속 실패 시 재시도 횟수 (기본 0)
            retry_wait: 재시도 간 대기 초. None이면 기존 exponential backoff
                (min(2^attempt, 10)). 명시 시 일정 시간 대기.

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
                    wait = retry_wait if retry_wait is not None else min(2 ** attempt, 10)
                    time.sleep(wait)
        raise last_error

    def _run_once(self, command: str) -> str | None:
        """단일 SSH 명령 실행."""
        if self._use_paramiko:
            return self._run_paramiko(command)
        return self._run_subprocess(command)

    def _get_paramiko_client(self):
        """캐시된 paramiko client 반환. 없거나 transport 비활성이면 새로 connect.

        cache check + connect + assign 전체를 lock 으로 직렬화해 동시 호출이
        중복 connect 를 만들지 않도록 한다. lock 은 connect 동안 유지되므로
        connect_timeout 만큼 다른 caller 가 대기할 수 있다.
        """
        import paramiko

        with self._client_lock:
            if self._client is not None:
                transport = self._client.get_transport()
                if transport is not None and transport.is_active():
                    return self._client
                # 끊긴 client 정리 후 재연결
                try:
                    self._client.close()
                except Exception:  # noqa: BLE001 — close 실패는 무시하고 새로 생성
                    pass
                self._client = None

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

            # idle TCP drop / NAT timeout 방어 — 체크 사이 공백에서 sshd 가 끊지 않도록.
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(15)

            self._client = client
            return client

    def _run_paramiko(self, command: str) -> str | None:
        """paramiko persistent client 로 SSH 명령 실행.

        실패(timeout/connection error) 시 캐시된 client 를 invalidate 해
        다음 호출에서 자동 재연결되도록 한다.
        """
        client = self._get_paramiko_client()
        try:
            _stdin, stdout, stderr = client.exec_command(
                command, timeout=self.command_timeout
            )
            exit_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode("utf-8", errors="replace").strip()
        except Exception as e:
            # client 가 broken 일 수 있다 — 캐시 무효화 후 적절한 예외로 변환.
            # lock 안에서 invalidate 해 다른 스레드가 이 사이 새 client 를 만들었으면
            # 그것을 보존한다(우리 것만 close 하고 캐시를 비우지 않음).
            with self._client_lock:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                if self._client is client:
                    self._client = None
            err_str = str(e).lower()
            if "timed out" in err_str or "timeout" in err_str:
                raise SshTimeoutError(
                    f"Command timed out after {self.command_timeout}s: {command}"
                )
            raise SshConnectionError(
                f"SSH command failed on {self.user}@{self.host}: {e}"
            )

        if exit_code != 0:
            return None
        return output

    def close(self) -> None:
        """persistent client 명시적 종료. 같은 인스턴스로 이후 호출 시 재연결됨.

        atexit 와 명시적 호출 양쪽에서 안전(멱등). lock 안에서 self._client 를
        교체해 동시 호출/연결이 충돌하지 않도록 한다.
        """
        with self._client_lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:  # noqa: BLE001 — 정리 실패 무시
                    pass
                self._client = None

    def __enter__(self) -> "SshClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

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
