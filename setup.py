from __future__ import annotations
"""
setup.py - SetupManager: edgeconf 설정 변경 및 복원 엔진
"""
import time

EDGECONF_PATH = "/root/shared_v/edgeconf_pim.json"
EDGECONF_BACKUP = f"{EDGECONF_PATH}.bak"

DEFAULT_REBOOT_TIMEOUT = 600   # 10분
DEFAULT_POLL_INTERVAL = 60     # 1분


class SetupManager:
    def __init__(self, ssh, reboot_timeout: int = DEFAULT_REBOOT_TIMEOUT,
                 poll_interval: int = DEFAULT_POLL_INTERVAL):
        self.ssh = ssh
        self.reboot_timeout = reboot_timeout
        self.poll_interval = poll_interval

    def backup(self) -> bool:
        """edgeconf 파일을 백업한다. 성공 시 True."""
        result = self.ssh.run(f"cp {EDGECONF_PATH} {EDGECONF_BACKUP} && echo OK")
        return result == "OK"

    def apply_changes(self, changes: dict) -> None:
        """jq --arg를 사용하여 edgeconf에 변경사항을 안전하게 적용한다."""
        for jq_path, value in changes.items():
            if isinstance(value, bool):
                jq_value = "true" if value else "false"
                self.ssh.run(
                    f"jq '{jq_path} = {jq_value}' {EDGECONF_PATH} "
                    f"> /tmp/_edgeconf_tmp.json && mv /tmp/_edgeconf_tmp.json {EDGECONF_PATH}"
                )
            elif isinstance(value, (int, float)):
                self.ssh.run(
                    f"jq --argjson v {value} '{jq_path} = $v' {EDGECONF_PATH} "
                    f"> /tmp/_edgeconf_tmp.json && mv /tmp/_edgeconf_tmp.json {EDGECONF_PATH}"
                )
            else:
                safe_value = str(value).replace("'", "'\\''")
                self.ssh.run(
                    f"jq --arg v '{safe_value}' '{jq_path} = $v' {EDGECONF_PATH} "
                    f"> /tmp/_edgeconf_tmp.json && mv /tmp/_edgeconf_tmp.json {EDGECONF_PATH}"
                )

    def restore(self) -> None:
        """백업에서 edgeconf 파일을 복원한다."""
        self.ssh.run(f"cp {EDGECONF_BACKUP} {EDGECONF_PATH}")

    def wait_for_boot(self, stabilize_sec: int = 30) -> None:
        """타겟이 온라인 복귀할 때까지 폴링한다.

        Args:
            stabilize_sec: 복귀 후 추가 안정화 대기 시간(초)

        Raises:
            TimeoutError: reboot_timeout 내에 복귀하지 않을 때
        """
        elapsed = 0
        while elapsed < self.reboot_timeout:
            if self.ssh.check_connectivity():
                print(f"Target back online (after {elapsed}s)")
                if stabilize_sec > 0:
                    print(f"Stabilizing for {stabilize_sec}s...")
                    time.sleep(stabilize_sec)
                return
            print(f"  waiting... ({elapsed}/{self.reboot_timeout}s)")
            time.sleep(self.poll_interval)
            elapsed += self.poll_interval

        raise TimeoutError(
            f"Target did not come back online within {self.reboot_timeout}s"
        )

    def reboot_and_wait(self, stabilize_sec: int = 30) -> None:
        """타겟을 재부팅하고 온라인 복귀를 기다린다."""
        print("Sending reboot...")
        try:
            self.ssh.run("reboot")
        except Exception:
            pass  # reboot 시 SSH 연결 끊김은 정상
        time.sleep(10)  # 셧다운 대기
        self.wait_for_boot(stabilize_sec=stabilize_sec)

    def check_current(self, changes: dict) -> bool:
        """현재 edgeconf 값이 변경 목표와 이미 일치하는지 확인한다."""
        for jq_path, expected in changes.items():
            current = self.ssh.run(f"jq '{jq_path}' {EDGECONF_PATH}")
            if current is None:
                return False
            current = current.strip()
            if isinstance(expected, bool):
                if current != ("true" if expected else "false"):
                    return False
            elif isinstance(expected, (int, float)):
                try:
                    if float(current) != float(expected):
                        return False
                except ValueError:
                    return False
            else:
                if current.strip('"') != str(expected):
                    return False
        return True

    def run_setup(self, setup_config: dict) -> bool:
        """현재 설정을 확인하고, 다를 경우에만 변경+재부팅한다.

        Returns:
            True: 설정이 변경되었음 (teardown 필요)
            False: 이미 일치하여 skip됨 (teardown 불필요)
        """
        changes = setup_config.get("edgeconf_changes", {})
        if not changes:
            return False

        if self.check_current(changes):
            print("Config already matches target — skipping setup/reboot")
            return False

        print(f"Config differs — applying {len(changes)} changes...")
        if not self.backup():
            print("ERROR: Failed to backup edgeconf — aborting setup")
            return False
        self.apply_changes(changes)

        if setup_config.get("reboot_after", False):
            stabilize_sec = setup_config.get("stabilize_sec", 30)
            self.reboot_and_wait(stabilize_sec=stabilize_sec)
        return True

    def run_teardown(self, setup_config: dict) -> None:
        """edgeconf를 원래 설정으로 복원하고 필요시 재부팅한다."""
        changes = setup_config.get("edgeconf_changes", {})
        if not changes:
            return

        self.restore()

        if setup_config.get("reboot_after", False):
            self.reboot_and_wait(stabilize_sec=20)
