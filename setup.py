from __future__ import annotations
"""
setup.py - SetupManager: edgeconf 설정 변경 및 복원 엔진
"""
import time

EDGECONF_PATH = "/root/shared_v/edgeconf_pim.json"
EDGECONF_BACKUP = f"{EDGECONF_PATH}.bak"


class SetupManager:
    def __init__(self, ssh, reboot_timeout: int = 120):
        self.ssh = ssh
        self.reboot_timeout = reboot_timeout

    def backup(self) -> None:
        """edgeconf 파일을 백업한다."""
        self.ssh.run(f"cp {EDGECONF_PATH} {EDGECONF_BACKUP}")

    def apply_changes(self, changes: dict) -> None:
        """jq --arg를 사용하여 edgeconf에 변경사항을 안전하게 적용한다."""
        for jq_path, value in changes.items():
            if isinstance(value, bool):
                # bool은 jq 리터럴로 직접 삽입 (--argjson)
                jq_value = "true" if value else "false"
                self.ssh.run(
                    f"jq '{jq_path} = {jq_value}' {EDGECONF_PATH} "
                    f"> /tmp/_edgeconf_tmp.json && mv /tmp/_edgeconf_tmp.json {EDGECONF_PATH}"
                )
            elif isinstance(value, (int, float)):
                # 숫자도 --argjson으로 안전 삽입
                self.ssh.run(
                    f"jq --argjson v {value} '{jq_path} = $v' {EDGECONF_PATH} "
                    f"> /tmp/_edgeconf_tmp.json && mv /tmp/_edgeconf_tmp.json {EDGECONF_PATH}"
                )
            else:
                # 문자열은 --arg로 안전 삽입 (셸 인젝션 방지)
                self.ssh.run(
                    f"jq --arg v '{value}' '{jq_path} = $v' {EDGECONF_PATH} "
                    f"> /tmp/_edgeconf_tmp.json && mv /tmp/_edgeconf_tmp.json {EDGECONF_PATH}"
                )

    def restore(self) -> None:
        """백업에서 edgeconf 파일을 복원한다."""
        self.ssh.run(f"cp {EDGECONF_BACKUP} {EDGECONF_PATH}")

    def reboot_and_wait(self, stabilize_sec: int = 30) -> None:
        """타겟을 재부팅하고 온라인 복귀를 기다린다."""
        self.ssh.run("reboot")
        time.sleep(5)  # 셧다운 대기

        elapsed = 5
        while elapsed < self.reboot_timeout:
            if self.ssh.check_connectivity():
                print("Target back online")
                print(f"Stabilizing for {stabilize_sec}s...")
                time.sleep(stabilize_sec)
                return
            time.sleep(5)
            elapsed += 5

        raise TimeoutError(
            f"Target did not come back online within {self.reboot_timeout}s"
        )

    def run_setup(self, setup_config: dict) -> None:
        """setup_config에 따라 edgeconf 변경을 적용하고 필요시 재부팅한다."""
        changes = setup_config.get("edgeconf_changes", {})
        if not changes:
            return

        self.backup()
        self.apply_changes(changes)

        if setup_config.get("reboot_after", False):
            stabilize_sec = setup_config.get("stabilize_sec", 30)
            self.reboot_and_wait(stabilize_sec=stabilize_sec)

    def run_teardown(self, setup_config: dict) -> None:
        """edgeconf를 원래 설정으로 복원하고 필요시 재부팅한다."""
        changes = setup_config.get("edgeconf_changes", {})
        if not changes:
            return

        self.restore()

        if setup_config.get("reboot_after", False):
            self.reboot_and_wait(stabilize_sec=20)
