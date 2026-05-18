from __future__ import annotations
"""
setup.py - SetupManager: edgeconf 설정 변경 및 복원 엔진
"""
import json
import subprocess
import time

EDGECONF_PATH = "/root/shared_v/edgeconf_pim.json"
ORD_VCM_PATH = "/root/shared_v/ord_vcm_conf.json"

# 보드 fw의 config_guard.sh가 인식하는 backup 디렉토리.
# pim-check가 만든 .bak가 이 디렉토리에 있어야 보드 reboot 시 config_guard가
# 복원에 사용하고, 디폴트(/etc/defaultconf.json) reset을 막을 수 있다.
BACKUP_DIR = "/root/shared_v/backup"
EDGECONF_BACKUP = f"{BACKUP_DIR}/edgeconf_pim.json.bak"
ORD_VCM_BACKUP = f"{BACKUP_DIR}/ord_vcm_conf.json.bak"

DEFAULT_REBOOT_TIMEOUT = 600   # 10분
DEFAULT_POLL_INTERVAL = 60     # 1분

# Network 복구 명령
HOST_WLAN_RESET_SCRIPT = "/home/jhw/ai/opencode/scripts/wlan_reset.sh"
BOARD_NET_RECOVERY_CMD = "python3 /opt/cis/bin/update_network.py"
HOST_WLAN_IFACE = "wlan0"             # 호스트 측 보드 접속 인터페이스


class SetupManager:
    def __init__(self, ssh, reboot_timeout: int = DEFAULT_REBOOT_TIMEOUT,
                 poll_interval: int = DEFAULT_POLL_INTERVAL):
        self.ssh = ssh
        self.reboot_timeout = reboot_timeout
        self.poll_interval = poll_interval

    def _backup_path(self, conf_path: str) -> str:
        """conf_path에 대응하는 backup 경로 (보드 fw config_guard.sh 인식)."""
        import os
        return f"{BACKUP_DIR}/{os.path.basename(conf_path)}.bak"

    def backup(self, conf_path: str = EDGECONF_PATH) -> bool:
        """conf 파일을 보드 fw가 인식하는 BACKUP_DIR에 백업한다.
        config_guard.sh가 이 백업으로 default reset을 방지한다."""
        backup_path = self._backup_path(conf_path)
        result = self.ssh.run(
            f"mkdir -p {BACKUP_DIR} && cp {conf_path} {backup_path} && sync && echo OK"
        )
        return result == "OK"

    def apply_changes(self, changes: dict, conf_path: str = EDGECONF_PATH) -> None:
        """jq --arg를 사용하여 conf 파일에 변경사항을 안전하게 적용한다.
        각 변경 후 read-back으로 실제 반영 확인. 불일치 시 RuntimeError."""
        tmp_path = "/tmp/_conf_tmp.json"
        for jq_path, value in changes.items():
            if isinstance(value, bool):
                jq_value = "true" if value else "false"
                self.ssh.run(
                    f"jq '{jq_path} = {jq_value}' {conf_path} "
                    f"> {tmp_path} && mv {tmp_path} {conf_path}"
                )
            elif isinstance(value, (int, float)):
                self.ssh.run(
                    f"jq --argjson v {value} '{jq_path} = $v' {conf_path} "
                    f"> {tmp_path} && mv {tmp_path} {conf_path}"
                )
            elif isinstance(value, (list, dict)):
                json_value = json.dumps(value).replace("'", "'\\''")
                self.ssh.run(
                    f"jq --argjson v '{json_value}' '{jq_path} = $v' {conf_path} "
                    f"> {tmp_path} && mv {tmp_path} {conf_path}"
                )
            else:
                safe_value = str(value).replace("'", "'\\''")
                self.ssh.run(
                    f"jq --arg v '{safe_value}' '{jq_path} = $v' {conf_path} "
                    f"> {tmp_path} && mv {tmp_path} {conf_path}"
                )
            # Read-back verify: 적용된 값이 기대와 일치하는지 확인
            actual = self.ssh.run(f"jq -c '{jq_path}' {conf_path}")
            if not self._values_match(actual, value):
                raise RuntimeError(
                    f"conf apply verify FAILED [{conf_path}]: {jq_path} expected {value!r} got {actual!r}"
                )

    def restore(self, conf_path: str = EDGECONF_PATH) -> None:
        """BACKUP_DIR의 백업에서 conf 파일을 복원한다."""
        backup_path = self._backup_path(conf_path)
        self.ssh.run(f"cp {backup_path} {conf_path} && sync")

    def _ping(self, ip: str, count: int = 1, timeout: int = 2) -> bool:
        """ICMP ping. True if reachable."""
        try:
            r = subprocess.run(
                ["ping", "-c", str(count), "-W", str(timeout), ip],
                capture_output=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    def _host_wlan_up(self) -> bool:
        """호스트 wlan 인터페이스가 UP + IP 할당 상태인지."""
        try:
            r = subprocess.run(
                ["ip", "-br", "addr", "show", HOST_WLAN_IFACE],
                capture_output=True, text=True, timeout=5,
            )
            line = r.stdout.strip()
            # 'wlan0  UP  192.168.0.2/24 ...' 형태
            return "UP" in line and "inet" in line.lower() or any(
                c.isdigit() for tok in line.split()[2:3] for c in tok
            )
        except Exception:
            return False

    def _diagnose_network(self) -> str:
        """SSH 실패 시 host wlan vs board 측 문제 구분.
        Returns: 'host_wlan', 'board', 'unknown'."""
        if not self._host_wlan_up():
            return "host_wlan"
        # wlan 살아있음. 보드 ping 시도
        if not self._ping(self.ssh.host):
            return "board"      # wlan OK인데 보드만 안 닿음
        # ping OK인데 SSH가 안 됐다면 보드 sshd 일시 문제
        return "board"

    def _recover_host_wlan(self) -> bool:
        """호스트 측 wlan reset 시도. True if exit 0."""
        print(f"  Host wlan recovery: {HOST_WLAN_RESET_SCRIPT}")
        try:
            r = subprocess.run(
                [HOST_WLAN_RESET_SCRIPT],
                capture_output=True, text=True, timeout=60,
            )
            ok = r.returncode == 0
            print(f"  Host wlan recovery {'OK' if ok else 'FAILED (exit ' + str(r.returncode) + ')'}")
            return ok
        except Exception as e:
            print(f"  Host wlan recovery error: {e}")
            return False

    def _recover_board_network(self) -> bool:
        """보드 측 update_network.py 시도. SSH가 깨졌으면 catch-22지만 간헐 단절 시 가능."""
        print(f"  Board network recovery: {BOARD_NET_RECOVERY_CMD}")
        try:
            r = self.ssh.run(BOARD_NET_RECOVERY_CMD)
            ok = r is not None
            print(f"  Board network recovery {'OK' if ok else 'FAILED (SSH not reachable)'}")
            return ok
        except Exception as e:
            print(f"  Board network recovery error: {e}")
            return False

    def wait_for_boot(self, stabilize_sec: int = 30) -> None:
        """타겟이 온라인 복귀할 때까지 폴링한다.
        timeout 발생 시 네트워크 진단 + 자동 복구 1회 시도 후 재폴링.

        Args:
            stabilize_sec: 복귀 후 추가 안정화 대기 시간(초)

        Raises:
            TimeoutError: 복구 후에도 reboot_timeout 내에 복귀하지 않을 때
        """
        recovery_attempted = False
        elapsed = 0
        while True:
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

            # Timeout — try recovery once
            if recovery_attempted:
                break
            recovery_attempted = True
            print(f"  Timeout — diagnosing network...")
            diag = self._diagnose_network()
            print(f"  Diagnosis: {diag}")
            if diag == "host_wlan":
                self._recover_host_wlan()
            elif diag == "board":
                self._recover_board_network()
            else:
                print("  unknown failure mode — skipping recovery")
            # Reset elapsed and re-poll
            elapsed = 0
            print(f"  Recovery attempted, re-polling...")

        raise TimeoutError(
            f"Target did not come back online within {self.reboot_timeout}s "
            f"(post-recovery)"
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

    def check_current(self, changes: dict, conf_path: str = EDGECONF_PATH) -> bool:
        """현재 conf 값이 변경 목표와 이미 일치하는지 확인한다."""
        for jq_path, expected in changes.items():
            current = self.ssh.run(f"jq -c '{jq_path}' {conf_path}")
            if current is None:
                return False
            if not self._values_match(current, expected):
                return False
        return True

    def _values_match(self, jq_output: str | None, expected) -> bool:
        """jq -c 출력을 expected 값과 정확히 비교. list/dict는 JSON 파싱 후 비교."""
        if jq_output is None:
            return False
        current = jq_output.strip()
        if isinstance(expected, bool):
            return current == ("true" if expected else "false")
        if isinstance(expected, (int, float)):
            try:
                return float(current) == float(expected)
            except ValueError:
                return False
        if isinstance(expected, (list, dict)):
            try:
                return json.loads(current) == expected
            except (ValueError, TypeError):
                return False
        # str
        return current.strip('"') == str(expected)

    def run_setup(self, setup_config: dict) -> bool:
        """현재 설정을 확인하고, 다를 경우에만 변경+재부팅한다.

        지원 키:
          - edgeconf_changes: /root/shared_v/edgeconf_pim.json 변경
          - ord_vcm_changes:  /root/shared_v/ord_vcm_conf.json 변경

        Returns:
            True: 설정이 변경되었음 (teardown 필요)
            False: 이미 일치하여 skip됨 (teardown 불필요)
        """
        edge_changes = setup_config.get("edgeconf_changes", {})
        ord_changes = setup_config.get("ord_vcm_changes", {})
        if not edge_changes and not ord_changes:
            return False

        edge_match = (not edge_changes) or self.check_current(edge_changes, EDGECONF_PATH)
        ord_match = (not ord_changes) or self.check_current(ord_changes, ORD_VCM_PATH)
        if edge_match and ord_match:
            print("Config already matches target — skipping setup/reboot")
            return False

        if edge_changes and not edge_match:
            print(f"edgeconf differs — applying {len(edge_changes)} changes...")
            if not self.backup(EDGECONF_PATH):
                print("ERROR: Failed to backup edgeconf — aborting setup")
                return False
            self.apply_changes(edge_changes, EDGECONF_PATH)

        if ord_changes and not ord_match:
            print(f"ord_vcm_conf differs — applying {len(ord_changes)} changes...")
            if not self.backup(ORD_VCM_PATH):
                print("ERROR: Failed to backup ord_vcm_conf — aborting setup")
                return False
            self.apply_changes(ord_changes, ORD_VCM_PATH)

        if setup_config.get("reboot_after", False):
            stabilize_sec = setup_config.get("stabilize_sec", 30)
            self.reboot_and_wait(stabilize_sec=stabilize_sec)
        return True

    def run_teardown(self, setup_config: dict) -> None:
        """edgeconf/ord_vcm_conf를 원래 설정으로 복원하고 필요시 재부팅한다."""
        edge_changes = setup_config.get("edgeconf_changes", {})
        ord_changes = setup_config.get("ord_vcm_changes", {})
        if not edge_changes and not ord_changes:
            return

        if edge_changes:
            self.restore(EDGECONF_PATH)
        if ord_changes:
            self.restore(ORD_VCM_PATH)

        if setup_config.get("reboot_after", False):
            self.reboot_and_wait(stabilize_sec=20)
