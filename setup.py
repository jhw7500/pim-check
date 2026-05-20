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

# Setup 단계 SSH retry — 정책은 verify_retry 중앙 모듈에서 가져와
# verify_retry 환경변수(PIM_VERIFY_MAX_ATTEMPTS / PIM_VERIFY_RETRY_WAIT)
# 한 곳에서 setup/verify 양쪽 retry 정책을 조정한다.
# verify_retry.MAX_ATTEMPTS는 첫 시도 포함, ssh.run(retries=N)은 추가 시도
# 횟수이므로 -1 보정한다.
from verify_retry import MAX_ATTEMPTS as _VERIFY_MAX_ATTEMPTS
from verify_retry import RETRY_WAIT_SEC as _VERIFY_RETRY_WAIT
SETUP_SSH_RETRIES = max(_VERIFY_MAX_ATTEMPTS - 1, 0)
SETUP_SSH_RETRY_WAIT = _VERIFY_RETRY_WAIT

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
        # 안정화 2차(코어 프로세스) readiness 에 쓰일 required 프로세스 목록.
        # run_setup(ready_processes=...) 로 profile 의 checks.processes.required 가 주입된다.
        self._ready_processes_list: list[str] = []

    def _backup_path(self, conf_path: str) -> str:
        """conf_path에 대응하는 backup 경로 (보드 fw config_guard.sh 인식)."""
        import os
        return f"{BACKUP_DIR}/{os.path.basename(conf_path)}.bak"

    def _setup_run(self, command: str):
        """setup 단계 SSH 명령 wrapper — verify_retry 중앙 정책 적용
        (PIM_VERIFY_MAX_ATTEMPTS / PIM_VERIFY_RETRY_WAIT). 일시 SSH 끊김 시
        자동 retry하여 SETUP_EXCEPTION 발생률을 낮춘다."""
        return self.ssh.run(
            command,
            retries=SETUP_SSH_RETRIES,
            retry_wait=SETUP_SSH_RETRY_WAIT,
        )

    def _local0_log(self, message: str) -> None:
        """보드 /var/log/cantops/local0.log에 [PIM_CHECK] marker entry를 남긴다.
        setup/teardown lifecycle을 보드 로그에서 추적하여 reboot 트리거 디버깅에 활용.
        SSH 실패 시 silent skip (fatal 아님)."""
        try:
            # shell 안전을 위해 message에서 따옴표/백슬래시 escape
            safe = message.replace('\\', '\\\\').replace('"', '\\"')
            self.ssh.run(
                f'logger -p local0.notice -t PIM_CHECK "{safe}"',
                retries=0,  # logger는 best-effort, retry 의미 없음
            )
        except Exception:
            pass

    def backup(self, conf_path: str = EDGECONF_PATH) -> bool:
        """conf 파일을 보드 fw가 인식하는 BACKUP_DIR에 백업한다.
        config_guard.sh가 이 백업으로 default reset을 방지한다."""
        backup_path = self._backup_path(conf_path)
        result = self._setup_run(
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
                self._setup_run(
                    f"jq '{jq_path} = {jq_value}' {conf_path} "
                    f"> {tmp_path} && mv {tmp_path} {conf_path}"
                )
            elif isinstance(value, (int, float)):
                self._setup_run(
                    f"jq --argjson v {value} '{jq_path} = $v' {conf_path} "
                    f"> {tmp_path} && mv {tmp_path} {conf_path}"
                )
            elif isinstance(value, (list, dict)):
                json_value = json.dumps(value).replace("'", "'\\''")
                self._setup_run(
                    f"jq --argjson v '{json_value}' '{jq_path} = $v' {conf_path} "
                    f"> {tmp_path} && mv {tmp_path} {conf_path}"
                )
            else:
                safe_value = str(value).replace("'", "'\\''")
                self._setup_run(
                    f"jq --arg v '{safe_value}' '{jq_path} = $v' {conf_path} "
                    f"> {tmp_path} && mv {tmp_path} {conf_path}"
                )
            # Read-back verify: 적용된 값이 기대와 일치하는지 확인
            actual = self._setup_run(f"jq -c '{jq_path}' {conf_path}")
            if not self._values_match(actual, value):
                raise RuntimeError(
                    f"conf apply verify FAILED [{conf_path}]: {jq_path} expected {value!r} got {actual!r}"
                )

    def restore(self, conf_path: str = EDGECONF_PATH) -> None:
        """BACKUP_DIR의 백업에서 conf 파일을 복원한다."""
        backup_path = self._backup_path(conf_path)
        self._setup_run(f"cp {backup_path} {conf_path} && sync")

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
                        self._stabilize(stabilize_sec)
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

    # === 단계별 readiness 기반 안정화 (고정 sleep 대체) ===
    # 리부트 후 "고정 stabilize_sec 블라인드 대기" 대신, 단계별 조건을 폴링해
    # 준비되면 즉시 진행한다. best-case 큰 단축, worst-case 기존 안전마진(=timeout) 유지.
    # 단계 순서(증분 확장): 1차 SSH → 2차 코어 프로세스 → 3차 영상파일 생성 → (4차 보관 이동)

    def _ready_ssh(self) -> bool:
        """1차: SSH 접속 가능 — 이게 돼야 이후 단계 확인이 가능하다."""
        try:
            return self.ssh.check_connectivity()
        except Exception:
            return False

    def _ready_processes(self, procs: list) -> bool:
        """2차: 코어 프로세스가 모두 떠 있는지 — pgrep -x(정확) → pgrep -f(폴백).

        하나라도 없으면 False. procs 가 비면 (주입 안 됨) 항상 True (단계 skip 효과)."""
        for proc in procs:
            try:
                hit = self.ssh.run(f"pgrep -x {proc}") or self.ssh.run(f"pgrep -f {proc}")
            except Exception:
                hit = None
            if not hit:
                return False
        return True

    def _stabilize_stages(self) -> list:
        """안정화 단계 목록 (1차→2차→3차 순서). 증분으로 확장.

        2차(코어 프로세스)는 run_setup 으로 required 프로세스가 주입된 경우에만 추가된다
        (profile 의 checks.processes.required 단일 출처 — setup 에 하드코딩하지 않음)."""
        stages = [("ssh", self._ready_ssh)]
        procs = list(self._ready_processes_list)
        if procs:
            stages.append(("processes", lambda: self._ready_processes(procs)))
        return stages

    def wait_until_ready(self, stages, *, poll_interval: int = 10,
                         debounce: int = 2, timeout: int = 260,
                         _sleep=None, _clock=None) -> bool:
        """단계별 readiness 게이트.

        stages 를 순서대로 평가하고, 각 단계가 ``debounce`` 회 연속 충족되면 다음
        단계로 넘어간다. 전체 경과가 ``timeout`` 을 넘으면 False 를 반환한다(미준비).
        시간 의존을 주입(_sleep/_clock)할 수 있어 단위 테스트가 가능하다.

        Args:
            stages: (이름, predicate) 튜플 목록. predicate 는 bool 반환.
            poll_interval: 폴링 간격(초).
            debounce: 단계 충족으로 인정할 연속 성공 횟수(흔들림 방지).
            timeout: 전체 readiness 예산(초).

        Returns:
            모든 단계가 시간 내 충족되면 True, 아니면 False.
        """
        sleep = _sleep or time.sleep
        clock = _clock or time.monotonic
        start = clock()
        for name, predicate in stages:
            hits = 0
            while True:
                ok = False
                try:
                    ok = bool(predicate())
                except Exception:
                    ok = False
                if ok:
                    hits += 1
                    if hits >= debounce:
                        print(f"  [ready] {name}")
                        break
                else:
                    hits = 0
                if clock() - start >= timeout:
                    print(f"  [timeout] {name} 미준비 ({timeout}s 초과)")
                    return False
                sleep(poll_interval)
        return True

    def _stabilize(self, stabilize_sec: int) -> None:
        """리부트 후 단계별 readiness 폴링으로 안정화 대기 (고정 sleep 대체).

        준비되면 즉시 진행하고, stabilize_sec 내에 미준비면 경고 후 진행한다
        (이후 monitor 단계가 실제 안정성을 최종 검증하므로 여기서 fail 시키지 않음)."""
        stages = self._stabilize_stages()
        names = ", ".join(n for n, _ in stages)
        print(f"Stabilizing (staged readiness, up to {stabilize_sec}s): {names}")
        ready = self.wait_until_ready(
            stages, poll_interval=self.poll_interval, debounce=2,
            timeout=stabilize_sec,
        )
        if ready:
            print("  readiness confirmed — proceeding")
        else:
            print(f"  readiness not confirmed within {stabilize_sec}s — "
                  f"proceeding (monitor will validate)")

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
            current = self._setup_run(f"jq -c '{jq_path}' {conf_path}")
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

    def _exec_commands(self, commands, label: str) -> None:
        """inject_command / recovery_command 처리. str 또는 list 허용."""
        if not commands:
            return
        if isinstance(commands, str):
            commands = [commands]
        for cmd in commands:
            print(f"  [{label}] {cmd}")
            out = self._setup_run(cmd)
            preview = (out or "")[:120]
            self._local0_log(f"{label} cmd '{cmd[:80]}' → '{preview}'")

    def run_setup(self, setup_config: dict, ready_processes=None) -> bool:
        """현재 설정을 확인하고, 다를 경우에만 변경+재부팅한다.

        지원 키:
          - edgeconf_changes: /root/shared_v/edgeconf_pim.json 변경
          - ord_vcm_changes:  /root/shared_v/ord_vcm_conf.json 변경
          - inject_command:   reboot/stabilize 후 fault inject용 셸 명령 (str/list).
                              edgeconf/ord 변경 없이 inject만 있어도 동작.

        Args:
            ready_processes: 리부트 후 안정화 2차에서 생존을 확인할 코어 프로세스 목록
                (profile 의 checks.processes.required). None 이면 2차 단계 skip.

        Returns:
            True: 변경 또는 inject가 적용됨 (teardown 필요)
            False: skip됨
        """
        # reboot_and_wait → _stabilize 가 참조하므로 reboot 전에 저장한다.
        self._ready_processes_list = list(ready_processes or [])
        edge_changes = setup_config.get("edgeconf_changes", {})
        ord_changes = setup_config.get("ord_vcm_changes", {})
        inject = setup_config.get("inject_command")

        if not edge_changes and not ord_changes:
            # inject-only 모드: edgeconf 변경 없이 fault만 주입
            if inject:
                self._local0_log(f"setup INJECT-ONLY mode")
                self._exec_commands(inject, "INJECT")
                return True  # teardown에서 recovery 필요
            return False

        edge_match = (not edge_changes) or self.check_current(edge_changes, EDGECONF_PATH)
        ord_match = (not ord_changes) or self.check_current(ord_changes, ORD_VCM_PATH)
        if edge_match and ord_match:
            print("Config already matches target — skipping setup/reboot")
            self._local0_log(
                f"setup SKIP — config already matches (edge={len(edge_changes)} ord={len(ord_changes)})"
            )
            return False

        self._local0_log(
            f"setup START — edge_changes={len(edge_changes)} ord_changes={len(ord_changes)}"
        )

        if edge_changes and not edge_match:
            print(f"edgeconf differs — applying {len(edge_changes)} changes...")
            if not self.backup(EDGECONF_PATH):
                print("ERROR: Failed to backup edgeconf — aborting setup")
                self._local0_log("setup ABORT — edgeconf backup failed")
                return False
            self.apply_changes(edge_changes, EDGECONF_PATH)

        if ord_changes and not ord_match:
            print(f"ord_vcm_conf differs — applying {len(ord_changes)} changes...")
            if not self.backup(ORD_VCM_PATH):
                print("ERROR: Failed to backup ord_vcm_conf — aborting setup")
                self._local0_log("setup ABORT — ord_vcm backup failed")
                return False
            self.apply_changes(ord_changes, ORD_VCM_PATH)

        self._local0_log("setup APPLIED — issuing reboot")

        if setup_config.get("reboot_after", False):
            stabilize_sec = setup_config.get("stabilize_sec", 30)
            self.reboot_and_wait(stabilize_sec=stabilize_sec)
            self._local0_log(f"setup DONE — back online + stabilize {stabilize_sec}s passed")

        # inject_command: reboot/stabilize 후 fault 주입 (실제 검증 직전)
        if inject:
            self._local0_log("setup INJECT — applying fault")
            self._exec_commands(inject, "INJECT")
        return True

    def run_teardown(self, setup_config: dict) -> None:
        """fault recovery + edgeconf/ord_vcm_conf 복원 + 필요시 재부팅."""
        edge_changes = setup_config.get("edgeconf_changes", {})
        ord_changes = setup_config.get("ord_vcm_changes", {})
        recovery = setup_config.get("recovery_command")
        inject = setup_config.get("inject_command")

        if not edge_changes and not ord_changes and not (recovery or inject):
            return

        # 1) recovery_command 우선 실행 (fault 해제)
        if recovery:
            self._local0_log("teardown RECOVERY — clearing fault")
            self._exec_commands(recovery, "RECOVERY")

        # 2) inject-only 모드 (edge/ord 변경 없음)이면 여기서 종료
        if not edge_changes and not ord_changes:
            self._local0_log("teardown DONE — inject-only recovery")
            return

        self._local0_log(
            f"teardown START — restore edge={bool(edge_changes)} ord={bool(ord_changes)}"
        )

        if edge_changes:
            self.restore(EDGECONF_PATH)
        if ord_changes:
            self.restore(ORD_VCM_PATH)

        self._local0_log("teardown RESTORED — issuing reboot")

        if setup_config.get("reboot_after", False):
            self.reboot_and_wait(stabilize_sec=20)
            self._local0_log("teardown DONE — back online")
