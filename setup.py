from __future__ import annotations
"""
setup.py - SetupManager: edgeconf 설정 변경 및 복원 엔진
"""
import base64
import json
import os
import re
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
DEFAULT_POLL_INTERVAL = 60     # 1분 (리부트 복구 폴링용 — 부팅은 느려 1분 간격이 적절)
# 안정화 readiness 폴링은 리부트 복구보다 훨씬 짧게 — 준비되면 거의 즉시 진행.
# 복구 폴링(60초)을 그대로 쓰면 debounce 때문에 단계당 ~60초가 들어 단축효과가 작다.
READINESS_POLL_INTERVAL = 5    # 5초 (단계별 readiness 디바운스 간격)

# 안정화 3차(영상파일 생성) readiness 탐지 경로 — 양쪽 모드를 포괄.
#  - SD 정상: json tmp_path→final_path (보드 설정값). 통상 마운트는 /mnt/sd_cam.
#  - SD 비정상(RAM fallback): /dev/shm(최초)→/dev/shm/recording(보관) 고정.
RECORDING_DIRS = ["/dev/shm", "/dev/shm/recording", "/mnt/sd_cam"]
RECORDING_PATTERNS = ["*.part", "*.srt", "*.mp4", "*.ts"]

# 안정화 카메라 init readiness — 보드가 스스로 유지하는 비로그 신호(cam_state)를
# 본다 (#85 C). 이전에는 kern.log 의 max9296_fsync fps 마커였는데, 로그 텍스트는
# CLEAR·wrap·severity·포맷 변경 네 경로로 조용히 깨진다 — 포맷 변경은 max9296
# 2.5 에서 실제 발생해 구형 고정 문자열이면 게이트가 영원히 열리지 않았다.
# cam_state 는 tmpfs 라 부팅마다 초기화되고(과거 부팅 오염 원천 차단 — 부팅
# 경계·dmesg 앵커 기제가 통째로 불필요), state=healthy 는 BG_Check 가 카메라
# 동작을 확인한 뒤에야 선다. 판정 상세는 _ready_cam_state docstring 참조.
CAM_STATE_DIR = "/tmp/cam_state"
# heartbeat(감시자 생존) 허용 나이(초) — checks.cam_state.heartbeat_max_age_sec
# 기본값과 같은 값을 쓴다 (다르면 "체크는 통과하는데 게이트는 안 열리는" 이면
# 상태가 생긴다).
CAM_READY_HEARTBEAT_MAX_AGE_SEC = 30

# 세션 앵커 — "이번 테스트 세션이 시작된 시각"의 단일 출처.
#
# 케이스 체크(custom_commands)는 이 앵커 이후의 local0.log 세션만 골라 fps/bitrate/
# duration 을 검증한다. 지금까지는 각 케이스가 `uptime -s`(부팅 시각)를 직접 읽었는데,
# 그건 "케이스 사이의 재시작 = 재부팅"이라는 전제에 의존한다. 하드리셋으로 재부팅을
# 대체하면(pim-package-jhw#46) `uptime -s` 가 바뀌지 않아 **직전 케이스의 녹화 세션이
# 그대로 매칭**된다 — 이전 케이스 영상으로 검증하는 오측.
#
# 그래서 앵커를 파일 하나로 외부화한다. 케이스는 이 파일을 읽고, 없거나 stale 이면
# `uptime -s` 로 폴백한다(오늘과 동일 동작). 하드리셋을 도입할 때 바꿀 곳은
# `_write_session_anchor` **한 곳**이고 케이스 38곳은 불변이다.
#
# 파일 형식(2줄): 1행 = 앵커 시각, 2행 = 그 앵커를 기록한 부팅의 `boot_id`.
#
# 2행이 `uptime -s` 가 아니라 boot_id 인 이유: `uptime -s` 는 현재시각 - uptime 으로
# 계산돼 **같은 부팅에서도 ±1초 흔들린다**(보드 실측: 같은 부팅이 15:41:58/15:41:59
# 로 읽힘). 문자열 대조로 쓰면 jitter 때마다 폴백해 앵커가 무시된다 — 오늘은 폴백값이
# 같아 무해하지만 하드리셋에서는 리셋 시각 대신 부팅 시각을 쓰게 된다.
# boot_id(`/proc/sys/kernel/random/boot_id`)는 부팅마다 고정된 UUID 라 흔들림이 없고,
# checks/cam_health.py 가 이미 같은 목적(이전 부팅 잔존 감지)으로 쓰는 선례가 있다.
#
# 2행(cross-boot 가드)에 대해 — 이 보드는 **재부팅 시 /tmp 를 비운다**(실측:
# 마커 파일·하위 디렉터리·앵커 모두 재부팅 후 소멸). /tmp 가 tmpfs 는 아니지만
# tmpfiles.d 의 `D /tmp 1777 root root -` 를 systemd-tmpfiles 가 부팅마다 적용한다.
# 따라서 이 보드에서 cross-boot 잔존은 발생하지 않고, 발생하더라도 세션 시작 때
# writer 가 덮어쓴다. 2행 대조는 **writer 가 이번 부팅에 돌지 않은 경우**
# (비카메라 케이스, 설정 일치로 setup 이 skip 된 경로)를 위한 방어이며, /tmp 를
# 비우지 않는 다른 보드/구성에서 의미를 갖는다. 비용이 사실상 없어 유지한다.
#
# 남는 위험(문서화): **같은 부팅 안에서** 이전 케이스의 앵커가 재사용되는 경우.
# 2행 대조로는 걸러지지 않는다. 구체 경로는 **setup-skip** 이다 — 보드 설정이 이미
# 케이스와 일치하면 run_setup 이 "Config already matches target" 로 조기 반환해
# 재부팅도 _stabilize 도 하지 않고, 따라서 session_anchor writer 가 돌지 않는다.
# 오늘은 앵커 = 부팅 시각이라 이전 케이스가 남긴 값도 현재 부팅 시각과 같아 무해하다.
#
# **하드리셋 전환 체크리스트**: 앵커를 리셋 시각으로 바꾸는 순간 이 경로가 실질
# 위험이 된다(직전 케이스의 리셋 시각을 그대로 씀). 전환 시 setup-skip 경로에서도
# 앵커를 갱신하도록 함께 처리할 것.
#
# 케이스가 뱉는 `FAIL:NEED_2_FINALIZES_AFTER_BOOT` 는 이제 의미상 "앵커 이후"지만
# 문구를 그대로 둔다 — checks/recording.py 가 이 문자열을 stabilization 신호 토큰으로
# 쓰고 있어(verify_retry 단일 출처 계약) 이름만 바꾸면 그 계약이 조용히 끊긴다.
SESSION_ANCHOR_PATH = "/tmp/pim_check_anchor"
# 부팅마다 고정된 UUID — 잔존 앵커 판별용 (cam_health 와 동일 출처).
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

# healthy 최초 관측 후 ISP 레지스터가 settle 됐다고 볼 때까지의 여유(초).
# 보드별 튜닝을 위해 환경변수 PIM_CAM_READY_SETTLE_SEC 로 override 가능
# (verify_retry 의 PIM_VERIFY_* 와 동일한 패턴).
try:
    CAM_READY_SETTLE_SEC = float(os.environ.get("PIM_CAM_READY_SETTLE_SEC", "2"))
except ValueError:
    CAM_READY_SETTLE_SEC = 2.0

# 안정화 AE 정착(settle) readiness — pim-check#61.
# 카메라 init(cam_state healthy) 이후에도 AP1302 의 AE 레지스터는 전이값을 거쳐 최종값에
# 도달한다. 콜드 기동 실측(2026-08-21): 정착 시점 = gstApp 기동 +16s(=boot+28s),
# 그 전 구간은 AE_CTRL 0x029c / AE_GAIN 0x0100 같은 전이값이 읽힌다. custom_commands
# 의 readback 단언이 이 창에 걸리면 오탐한다. 현행 마진(+20~35s)은 체크 실행 순서와
# readiness 통과 시각에 의존하는 우연적 배치라 명시 게이트로 고정한다.
# 판정: '케이스가 기대하는 값과 일치하는 읽기'가 AE_SETTLE_MATCH_GAP_SEC 이상
# 간격으로 2회. '연속 2회 안정'이 아니라 '기대값 일치 2회'인 이유는 전이값
# 0x0100 이 3초 이상 유지돼 안정 기준만으로는 조기 통과하기 때문이다.
#
# AP1302 i2c 주소는 **버스(디시리얼라이저)의 활성 채널 수**에 따라 갈린다.
# 드라이버(max9296.c)가 `dual ? AP1302_CH{0,1}_I2C_ADDR : AP1302_I2C_ADDR` 로
# 분기하기 때문이다 — 버스에 채널이 하나면 그 ISP 의 정식 주소는 0x3c 이고, 둘이면
# 채널별 별칭 0x11/0x12 를 쓴다. 버스 단위 분기라 "총 2채널이지만 버스당 1채널"인
# 구성(예: ch0+ch3)은 양쪽 다 0x3c 다.
# 근거: 프로파일 코퍼스 readback 249건 전건 일치(버스당 1채널 → 0x3c 129건,
# 2채널 → 0x11/0x12 120건) + 보드 실측(2026-08-21, 4ch dual 에서 0x11/0x12 가
# 네 채널 모두 edgeconf 와 일치, 같은 시점 0x3c 는 어느 채널과도 불일치).
# 주소를 고정하면 dual 에서 두 채널이 같은 값을 읽어 오탐하고, single 에서는
# 응답이 없어 게이트가 열리지 않는다.
ISP_SINGLE_ADDR = "0x3c"
ISP_DUAL_CH_ADDRS = ("0x11", "0x12")
AE_CTRL_REG = "0x50 0x02"      # AP1302 AE_CTRL — auto 0x0299 / manual 0x0290
AE_GAIN_REG = "0x50 0x06"      # AP1302 AE_GAIN — manual gain 2B big-endian
# 기대값 일치 읽기 2회 사이의 최소 간격(초).
try:
    AE_SETTLE_MATCH_GAP_SEC = float(os.environ.get("PIM_AE_SETTLE_GAP_SEC", "3"))
except ValueError:
    AE_SETTLE_MATCH_GAP_SEC = 3.0
# 정착 판정을 시작할 gstApp 경과시간 하한(초). 앵커가 boot 이 아니라 gstApp 기동인
# 이유: 하드리셋 등으로 부팅이 단축돼도 정착 소요는 gstApp 기준으로 유지된다.
# 이 값은 실측 정착점(gstApp+16~17s)과 **같은 값이라 그 자체로는 마진이 0** 이다 —
# 마진은 하한이 아니라 '기대값 일치 2회 + AE_SETTLE_MATCH_GAP_SEC 간격' 판정이
# 제공한다(실측 +4.4s). 마진을 늘리려고 이 하한을 키우지 말 것: 두 겹으로 쌓여
# readiness 예산만 먹고, 정착이 빨라진 FW 에서는 불필요한 대기가 된다.
try:
    AE_SETTLE_GSTAPP_ETIME_SEC = float(
        os.environ.get("PIM_AE_SETTLE_GSTAPP_ETIME_SEC", "16"))
except ValueError:
    AE_SETTLE_GSTAPP_ETIME_SEC = 16.0

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


# 카메라 채널 enable 키 — 예: ".VHL_CAM.i2c2.ch0.enable", ".VHL_CAM.i2c1.ch3.enable".
_CAM_CH_ENABLE_RE = re.compile(r"\.VHL_CAM\.[^.]+\.ch\d+\.enable$")


def profile_is_camera(profile: dict) -> bool:
    """카메라(녹화) 케이스인지 — reboot 후 카메라 init(cam_state) readiness 게이트가
    필요한 케이스인지 판정한다.

    판정 신호는 **setup 설정**을 본다(테스트 스텝 custom_commands 와 분리 — 테스트가
    무엇을 검사하는지와 부팅 게이트가 독립적이도록):
      1. `setup.camera_init_required` 가 명시돼 있으면 그 값을 그대로 사용(opt-in/out).
      2. 없으면 `setup.edgeconf_changes` 가 카메라 채널을 켜는지로 자동 추론
         (`.VHL_CAM.*.chN.enable: true` 가 하나라도 있으면 카메라).

    ISP 레지스터 검사(i2ctransfer)는 카메라 init 완료 후에야 유효하므로 카메라
    케이스만 게이트를 켠다. config/network 등 비카메라는 fsync 로그가 안 떠서
    게이트를 켜면 불필요하게 대기하게 된다."""
    setup = (profile or {}).get("setup") or {}
    if not isinstance(setup, dict):
        return False
    if "camera_init_required" in setup:
        return bool(setup["camera_init_required"])
    edge = setup.get("edgeconf_changes") or {}
    if not isinstance(edge, dict):
        return False
    return any(v is True and _CAM_CH_ENABLE_RE.search(k) for k, v in edge.items())


# 카메라 채널 AE 키 — 예: ".VHL_CAM.i2c2.ch0.ae_on", ".VHL_CAM.i2c1.ch3.ae_gain".
# 버스 번호는 키에서 읽는다(i2c2 → ch0/ch1, i2c1 → ch2/ch3 매핑을 하드코딩하지 않음).
_CAM_CH_AE_RE = re.compile(r"^\.VHL_CAM\.i2c(\d+)\.ch(\d+)\.(enable|ae_on|ae_gain)$")


def _isp_ch_addr(ch: int, bus_ch_count: int) -> str:
    """채널 번호 + 그 버스의 활성 채널 수 → AP1302 i2c 주소.

    버스에 채널이 하나면 0x3c(single), 둘이면 짝수 채널 0x11 / 홀수 채널 0x12(dual).
    """
    if bus_ch_count < 2:
        return ISP_SINGLE_ADDR
    return ISP_DUAL_CH_ADDRS[ch % 2]


def _ae_gain_hex(val: int) -> str:
    """ae_gain(2B) → i2ctransfer 출력 형식 문자열 (예: 512 → '0x020x00')."""
    return f"0x{(val >> 8) & 0xFF:02x}0x{val & 0xFF:02x}"


def _ae_ctrl_hex(ae_on: bool) -> str:
    """ae_on → AE_CTRL 최종값. auto 0x0299 / manual 0x0290 (보드 실측)."""
    return "0x020x99" if ae_on else "0x020x90"


def ae_settle_targets(profile: dict) -> list[dict]:
    """AE 정착 readiness 에서 일치를 확인할 레지스터 기대값 목록을 산출한다.

    출처는 케이스의 `setup.edgeconf_changes` 단일 소스 — 케이스가 **명시한** 값만
    단언한다. 명시하지 않은 채널/키는 보드 잔존값(config 드리프트)이라 기대값을
    만들 수 없다.

      - `chN.enable: true` 인 채널만 대상.
      - 읽기 주소는 그 채널이 속한 **버스의 활성 채널 수**로 정해진다
        (single 0x3c / dual 0x11·0x12) — `_isp_ch_addr` 참조.
        전제: 케이스가 채널 `enable` 을 **전부 명시**한다(코퍼스 전 케이스의 관행이며
        `TestAeSettleAddressMatchesCaseCorpus` 가 고정한다). 케이스가 enable 을
        생략하고 보드 잔존값에 기대면 버스 카운트가 어긋나 주소가 틀릴 수 있는데,
        그 경우 결과는 오탐 통과가 아니라 **읽기 불일치 → 게이트 타임아웃**
        (fail-safe) 이다.
      - `ae_on` 이 명시돼 있으면 AE_CTRL 기대값.
      - `ae_on: false`(manual) 이고 `ae_gain` 이 정수로 명시돼 있으면 AE_GAIN 기대값.
        auto 채널의 gain 은 FW 재량이라 기대값이 없다.

    Returns:
        [{"label", "bus", "addr", "reg", "expected"}, ...] — 채널 번호 오름차순,
        채널 안에서는 AE_CTRL → AE_GAIN 순(결정적 순서).
    """
    setup = (profile or {}).get("setup") or {}
    if not isinstance(setup, dict):
        return []
    edge = setup.get("edgeconf_changes") or {}
    if not isinstance(edge, dict):
        return []

    channels: dict[int, dict] = {}
    for key, value in edge.items():
        m = _CAM_CH_AE_RE.match(key) if isinstance(key, str) else None
        if not m:
            continue
        bus, ch, field = int(m.group(1)), int(m.group(2)), m.group(3)
        entry = channels.setdefault(ch, {"bus": bus})
        entry[field] = value

    # 버스별 활성 채널 수 — 읽기 주소가 single/dual 로 갈리므로 먼저 센다.
    bus_ch_count: dict[int, int] = {}
    for entry in channels.values():
        if entry.get("enable") is True:
            bus_ch_count[entry["bus"]] = bus_ch_count.get(entry["bus"], 0) + 1

    targets: list[dict] = []
    for ch in sorted(channels):
        entry = channels[ch]
        if entry.get("enable") is not True:
            continue
        ae_on = entry.get("ae_on")
        if not isinstance(ae_on, bool):
            continue
        bus = entry["bus"]
        addr = _isp_ch_addr(ch, bus_ch_count.get(bus, 0))
        targets.append({"label": f"ch{ch} AE_CTRL", "bus": bus, "addr": addr,
                        "reg": AE_CTRL_REG, "expected": _ae_ctrl_hex(ae_on)})
        gain = entry.get("ae_gain")
        # bool 은 int 의 서브클래스 — gain 자리에 True/False 가 오면 기대값이 아니다.
        if ae_on is False and isinstance(gain, int) and not isinstance(gain, bool):
            targets.append({"label": f"ch{ch} AE_GAIN", "bus": bus, "addr": addr,
                            "reg": AE_GAIN_REG, "expected": _ae_gain_hex(gain)})
    return targets


def readiness_kwargs(profile: dict) -> dict:
    """reboot 후 안정화 readiness 단계 주입 인자를 profile 에서 산출한다
    (plan / run_case 공용 — 중복 제거).

    Returns dict(run_setup 키워드):
      - ready_processes: checks.processes.required (코어 프로세스 생존 단계)
      - ready_recording_paths: RECORDING_DIRS (영상파일 생성 단계 고정 인프라 경로)
      - ready_camera_init: 카메라 케이스만 True (카메라 init(cam_state) 게이트)
      - ready_ae_targets: 카메라 케이스의 AE 정착 기대값 목록 (pim-check#61).
        빈 목록이면 단계 자체가 붙지 않는다.
    """
    checks = (profile or {}).get("checks") or {}
    is_camera = profile_is_camera(profile)
    procs = []
    if isinstance(checks, dict):
        procs = ((checks.get("processes") or {}).get("required") or [])
    return {
        "ready_processes": list(procs),
        # recording 단계는 카메라/비카메라 구분 없이 항상 주입(기존 plan.py 동작 보존).
        # 비카메라 케이스는 녹화 파일이 안 생겨 stabilize_sec 까지 대기 후 진행(경고)하나,
        # '잘못된 통과'보다 안전하므로 의도된 동작이다 — 카메라 init 게이트는 ready_camera_init 로 분리.
        "ready_recording_paths": RECORDING_DIRS,
        "ready_camera_init": is_camera,
        # 게이트가 읽는 dir·신선도 임계를 **체크와 같은 프로파일 키**에서 가져온다
        # (#103 리뷰) — 프로파일이 checks.cam_state.dir / heartbeat_max_age_sec 를
        # 오버라이드하면 체크와 게이트가 갈라져 "체크는 통과하는데 게이트는 안
        # 열리는"(또는 그 반대) 이면 상태가 된다.
        "ready_cam_state_dir": (
            (checks.get("cam_state") or {}).get("dir", CAM_STATE_DIR)
            if isinstance(checks, dict) else CAM_STATE_DIR),
        "ready_cam_heartbeat_max_age_sec": (
            (checks.get("cam_state") or {}).get(
                "heartbeat_max_age_sec", CAM_READY_HEARTBEAT_MAX_AGE_SEC)
            if isinstance(checks, dict) else CAM_READY_HEARTBEAT_MAX_AGE_SEC),
        # AE 정착 게이트는 카메라 게이트와 같은 opt-in/out 을 따른다
        # (camera_init_required: false 면 AE 정착도 끈다 — 게이트 일관성).
        "ready_ae_targets": ae_settle_targets(profile) if is_camera else [],
    }


class SetupManager:
    def __init__(self, ssh, reboot_timeout: int = DEFAULT_REBOOT_TIMEOUT,
                 poll_interval: int = DEFAULT_POLL_INTERVAL):
        self.ssh = ssh
        self.reboot_timeout = reboot_timeout
        self.poll_interval = poll_interval
        # 안정화 readiness 전용 폴링 간격 (리부트 복구 poll_interval 과 분리, 더 짧음).
        self.readiness_poll_interval = READINESS_POLL_INTERVAL
        # 안정화 2차(코어 프로세스) readiness 에 쓰일 required 프로세스 목록.
        # run_setup(ready_processes=...) 로 profile 의 checks.processes.required 가 주입된다.
        self._ready_processes_list: list[str] = []
        # 안정화 3차(영상파일 생성) readiness 에 쓰일 녹화 경로 목록.
        # run_setup(ready_recording_paths=...) 로 주입 (기본 OFF → 미주입 시 단계 skip).
        self._ready_recording_paths: list[str] = []
        # 안정화 카메라 init(cam_state healthy+heartbeat) readiness 활성 여부.
        # run_setup(ready_camera_init=True) 로 카메라 케이스에만 주입 (기본 OFF → 단계 skip).
        self._ready_camera_init: bool = False
        # healthy 최초 관측 시각(monotonic) — CAM_READY_SETTLE_SEC 경과 판정용.
        self._cam_ready_seen_at: float | None = None
        # 게이트가 읽는 cam_state 좌표 — 프로파일 오버라이드 시 체크와 같은 값을
        # 쓰도록 run_setup 이 주입한다 (#103 리뷰). 기본은 체크의 기본과 동일.
        self._cam_state_dir: str = CAM_STATE_DIR
        self._cam_heartbeat_max_age: int = CAM_READY_HEARTBEAT_MAX_AGE_SEC
        # 안정화 AE 정착 readiness 의 기대값 목록 (pim-check#61).
        # run_setup(ready_ae_targets=...) 로 주입 (기본 빈 목록 → 단계 skip).
        self._ready_ae_targets: list[dict] = []
        # AE 기대값과 처음 일치한 시각(monotonic) — AE_SETTLE_MATCH_GAP_SEC 판정용.
        self._ae_match_at: float | None = None
        # teardown 복원 원본 — {conf_path: base64 문자열} (pim-check#65).
        # 보드의 .bak 을 쓰지 않는 이유는 snapshot_config 주석 참조.
        self._config_snapshots: dict[str, str] = {}
        # 이번 setup 에서 스냅샷에 실패한 경로 (pim-check#82). 실패는 setup 을
        # 중단시키지 않지만, plan 캠페인 복원이 "이 파일의 기준선은 밀렸을 수
        # 있다" 를 판정하려면 실패 사실이 인스턴스에 남아야 한다.
        self._snapshot_failures: set[str] = set()

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

    def snapshot_config(self, conf_path: str) -> bool:
        """설정 파일 전문을 **호스트 메모리**에 스냅샷한다 (teardown 복원 원본).

        보드의 `BACKUP_DIR/*.bak` 을 복원 원본으로 쓸 수 없어서 필요하다. 그 슬롯은
        pim-check 전용이 아니라 보드 FW `config_guard.sh` 의 known-good 자리이고,
        guard 가 **부팅 시 valid 한 현재본을 그 자리로 복사**한다. 그래서
        "설정 적용 → 재부팅" 을 거치면 .bak 이 이미 케이스 설정으로 덮여 있어
        거기서 복원해봐야 no-op 이다 (pim-check#65, 보드 실측).

        호스트가 들고 있으면 보드 경로 계약이 없어 guard 와 구조적으로 경합하지
        않는다. `backup()` 의 .bak 쓰기는 그대로 둔다 — 그건 guard 가 디폴트
        리셋을 막는 데 쓰는 **별개 용도**다.

        전송은 base64 로 한다. 설정이 JSON 이라 따옴표를 포함하는데, 원문을 셸
        인용으로 넘기면 이스케이프 사고가 나기 쉽다.

        저장 전에 **실제로 디코드해서** 검증한다. 잡음이 섞이거나 절단된 출력이
        저장되면 복원 때 `base64 -d` 가 실패하고, 그러면 조용히 `.bak` 폴백으로
        떨어진다 — 이 기능이 없애려던 바로 그 경로다. 문자집합만 보는 검사는
        길이가 4 의 배수가 아닌 절단 출력을 통과시키므로 부족하다. 바이트가 이미
        호스트에 있으니 프록시 대신 실물로 확인하는 편이 싸고 정확하다.
        JSON 파싱까지 하는 것은 복원측의 `jq -e .` 와 대칭을 맞추기 위해서다
        (두 대상 파일 모두 JSON).

        공백/개행은 제거하고 본다. 다만 이 관용이 실제로 먹는 범위는 좁다 —
        `-w` 옵션을 **모르는** 구현은 옵션 오류로 exit≠0 이고 `ssh.run` 이 None 을
        돌려주므로(ssh.py) 여기까지 오지 않는다. `-w` 를 무시하고 접어서 출력하는
        구현에서만 의미가 있다.
        """
        try:
            out = self.ssh.run(f"base64 -w0 {conf_path} 2>/dev/null")
        except Exception:
            return False
        compact = re.sub(r"\s+", "", out or "")
        if not compact:
            return False
        try:
            json.loads(base64.b64decode(compact, validate=True))
        except Exception:
            return False
        self._config_snapshots[conf_path] = compact
        return True

    def restore_from_snapshot(self, conf_path: str) -> bool:
        """호스트 스냅샷에서 설정 파일을 되돌린다. 스냅샷이 없으면 False.

        임시 파일에 풀고 `jq -e .` 로 JSON 유효성을 확인한 뒤에만 원자적으로
        옮긴다. 깨진 설정을 제자리에 쓰면 다음 부팅에서 config_guard 가 디폴트
        리셋을 트리거해 보드를 더 나쁜 상태로 만든다.
        """
        b64 = self._config_snapshots.get(conf_path)
        if not b64:
            return False
        tmp = f"{conf_path}.pimtmp"
        try:
            out = self.ssh.run(
                f"printf '%s' '{b64}' | base64 -d > {tmp} "
                f"&& jq -e . {tmp} >/dev/null 2>&1 "
                f"&& mv {tmp} {conf_path} && sync && echo OK "
                f"|| {{ rm -f {tmp}; echo FAIL; }}"
            )
        except Exception:
            return False
        return bool(out) and out.strip().splitlines()[-1] == "OK"

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
            print("  Timeout — diagnosing network...")
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
            print("  Recovery attempted, re-polling...")

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

    def _ready_recording(self, paths: list, mmin: int = 2) -> bool:
        """3차: 최근(mmin분 내) 영상파일이 생성됐는지 — 녹화 파이프라인이 실제로
        쓰기 시작했는지 확인. .part/.srt(진행 중) 또는 .mp4/.ts(완료)가 하나라도
        최근 생성됐으면 True. 경로가 없거나 비면 False."""
        if not paths:
            return False
        name_expr = " -o ".join(f"-name '{pat}'" for pat in RECORDING_PATTERNS)
        dirs = " ".join(paths)
        cmd = (f"find {dirs} -type f \\( {name_expr} \\) -mmin -{mmin} "
               f"2>/dev/null | head -1")
        try:
            out = self.ssh.run(cmd)
        except Exception:
            return False
        return bool(out and out.strip())

    def _cam_state_ready_probe_command(self) -> str:
        """cam_state 의 state 와 heartbeat(값+보드 now)를 한 왕복으로 읽는 명령.

        출력: "<state>;F;<ts>;<now>" (timestamp 가 읽히면) 또는 "<state>;N;;<now>".
        어떤 파일 상태에서도 exit 0 + 출력을 지킨다 — exit≠0 이면 ssh.run 이
        None 을 돌려줘 판정 근거가 사라진다. delta 의 양변(ts, now)이 같은 보드
        시계에서 나오도록 한 명령에 묶는다 (checks/cam_state.py 와 같은 계약).
        """
        return (
            f"printf '%s;' \"$(cat {self._cam_state_dir}/state 2>/dev/null | tr -d ' \\n')\"; "
            f"T={self._cam_state_dir}/timestamp; "
            "if [ -r \"$T\" ]; then printf 'F;%s' \"$(cat \"$T\" 2>/dev/null | tr -d ' \\n')\"; "
            "else printf 'N;'; fi; "
            "printf ';%s' \"$(date +%s)\""
        )

    def _ready_cam_state(self, _clock=None) -> bool:
        """카메라 init readiness — cam_state(state=healthy + heartbeat 신선) + settle.

        fsync 로그 게이트의 대체다 (#85 C). 근거:
          - /tmp(tmpfs)라 부팅마다 초기화 — 과거 부팅 오염이 원천 차단된다
            (kern.log 시절의 부팅 경계·dmesg 앵커·폴백 경고 기제가 통째로 불필요).
          - state=healthy 는 BG_Check 1초 루프가 카메라 **동작을 확인한 뒤** 세우는
            값이라 "init 로그가 찍혔다"(fsync 마커)보다 강한 신호다.
          - heartbeat(timestamp) 신선도가 그 state 가 **지금** 판정임을 보장한다 —
            감시자가 죽은 채 남은 healthy 로는 게이트가 열리지 않는다. 0 은
            NEVER_TOUCHED(초기값)라 인정하지 않고, 음수(시계 역행)도 신선이 아니다.
        채널별 생존 신호는 여전히 없다(#91 — 쓸 수 있는 것은 state·ch_error·
        timestamp 셋뿐). ISP 레지스터 settle 의미는 승계 — healthy 최초 관측 후
        CAM_READY_SETTLE_SEC 경과해야 True. 조건 미충족·SSH 실패는 타이머 리셋.
        """
        clock = _clock or time.monotonic
        try:
            out = self.ssh.run(self._cam_state_ready_probe_command())
        except Exception:
            self._cam_ready_seen_at = None
            return False

        parts = (out or "").strip().split(";")
        ok = False
        if len(parts) >= 4 and parts[1] == "F":
            state, now_raw = parts[0], parts[-1]
            ts_raw = ";".join(parts[2:-1])
            if (state == "healthy"
                    and ts_raw.isascii() and ts_raw.isdigit()
                    and now_raw.isascii() and now_raw.isdigit()
                    and int(ts_raw) > 0):
                age = int(now_raw) - int(ts_raw)
                ok = 0 <= age < self._cam_heartbeat_max_age
        if not ok:
            self._cam_ready_seen_at = None
            return False
        now = clock()
        if self._cam_ready_seen_at is None:
            self._cam_ready_seen_at = now
        return (now - self._cam_ready_seen_at) >= CAM_READY_SETTLE_SEC

    def _write_session_anchor(self) -> bool:
        """세션 앵커 파일을 보드에 기록한다 (readiness 단계 — SSH 복구 후 재시도됨).

        1행 = 앵커 시각, 2행 = 기록한 부팅의 boot_id.

        **세션 시작마다 무조건 다시 쓴다.** "기존 값이 유효하면 건너뛴다"로 만들면
        하드리셋(같은 부팅 안에서 새 세션)일 때 이전 세션의 앵커가 그대로 남는다 —
        정확히 이 게이트가 막으려던 상황이다. 오늘은 앵커 = 부팅 시각이라 값이
        불변이므로 디바운스 재호출에서도 내용이 같다(멱등).

        무조건 기록이 미래에도 안전한 이유: 이 단계는 **세션 시작 시점에만** 돌고
        (리부트 후 _stabilize), 그 시점의 앵커는 정의상 "이번 세션의 시작"이다.
        하드리셋으로 바뀌어도 같은 성질이라 덮어쓰기가 잃을 정보가 없다.

        **오늘 앵커 = 부팅 시각**이라 케이스의 기존 `uptime -s` 동작과 완전히 같다.
        하드리셋으로 케이스 간 재시작을 대체하게 되면 여기서 리셋 시각을 쓰도록
        바꾸면 되고, 케이스 쪽은 손대지 않아도 된다 (SESSION_ANCHOR_PATH 주석 참조).
        """
        try:
            out = self.ssh.run(
                f"printf '%s\\n%s\\n' \"$(uptime -s)\" "
                f"\"$(cat {BOOT_ID_PATH} 2>/dev/null)\" "
                f"> {SESSION_ANCHOR_PATH}; sync; cat {SESSION_ANCHOR_PATH} 2>/dev/null"
            )
        except Exception:
            return False
        lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
        return len(lines) == 2 and bool(lines[0])

    def _ae_probe_command(self) -> str:
        """gstApp 경과초 + 타겟 레지스터 값을 한 번의 SSH 왕복으로 읽는 명령.

        각 값은 `e=`/`v=` 센티널 prefix 로 한 줄씩 출력한다. i2c 읽기가 실패하면
        빈 값(`v=`)이 되는데, prefix 가 없으면 그 줄이 통째로 사라져 뒤 타겟의 값이
        앞 타겟의 값으로 밀려 **오탐 통과**가 난다 (보드 실측으로 확인한 출력 형태).
        """
        parts = [
            "printf 'e=%s\\n' \"$(ps -o etimes= -C gstApp 2>/dev/null "
            "| head -1 | tr -d ' \\n')\""
        ]
        for t in self._ready_ae_targets:
            parts.append(
                "printf 'v=%s\\n' \"$(i2ctransfer -f -y {bus} w2@{addr} {reg} r2 "
                "2>/dev/null | tr -d ' \\n')\"".format(
                    bus=t["bus"], addr=t["addr"], reg=t["reg"])
            )
        return "; ".join(parts)

    def _ready_ae_settle(self, _clock=None) -> bool:
        """AE 정착 readiness — 케이스 기대값과 일치하는 읽기 2회(간격 >= gap).

        타겟이 없으면(케이스가 AE 를 단언하지 않으면) 통과시킨다 — 단언하지 않는
        케이스를 게이트로 붙잡을 이유가 없다.

        gstApp 경과시간이 하한 미만이면 값이 우연히 일치하더라도 정착으로 보지
        않는다(전이 구간에서 기대값을 스쳐 지나가는 경우 차단). 불일치·읽기 실패·
        SSH 예외는 모두 타이머를 리셋해, 재초기화 후 다시 처음부터 세도록 한다.
        """
        if not self._ready_ae_targets:
            return True
        clock = _clock or time.monotonic
        try:
            out = self.ssh.run(self._ae_probe_command())
        except Exception:
            self._ae_match_at = None
            return False

        lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
        etimes = [ln[2:] for ln in lines if ln.startswith("e=")]
        values = [ln[2:] for ln in lines if ln.startswith("v=")]
        # 줄 수가 타겟 수와 다르면 정렬을 신뢰할 수 없다 — 통과시키지 않는다.
        if len(etimes) != 1 or len(values) != len(self._ready_ae_targets):
            self._ae_match_at = None
            return False
        try:
            elapsed = float(etimes[0])
        except ValueError:
            self._ae_match_at = None
            return False
        if elapsed < AE_SETTLE_GSTAPP_ETIME_SEC:
            self._ae_match_at = None
            return False

        matched = all(
            got == t["expected"]
            for got, t in zip(values, self._ready_ae_targets)
        )
        if not matched:
            self._ae_match_at = None
            return False

        now = clock()
        if self._ae_match_at is None:
            self._ae_match_at = now
            return False
        return (now - self._ae_match_at) >= AE_SETTLE_MATCH_GAP_SEC

    def _stabilize_stages(self) -> list:
        """안정화 단계 목록 (1차→2차→3차 순서). 증분으로 확장.

        2차(코어 프로세스)·3차(영상파일 생성)는 run_setup 으로 각각 주입된 경우에만
        추가된다 (profile/인프라 단일 출처 — setup 에 하드코딩하지 않음)."""
        stages = [("ssh", self._ready_ssh)]
        # 세션 앵커는 SSH 복구 직후 — 케이스 체크가 "이 시각 이후" 로그만 보게 하는
        # 기준점이라 다른 준비 단계보다 먼저 찍혀야 한다. 카메라 케이스만 대상
        # (앵커를 읽는 custom_commands 가 카메라 케이스에만 있다).
        if self._ready_camera_init:
            stages.append(("session_anchor", self._write_session_anchor))
        procs = list(self._ready_processes_list)
        if procs:
            stages.append(("processes", lambda: self._ready_processes(procs)))
        # 카메라 init(cam_state)은 recording 보다 먼저 — 카메라가 init 돼야 녹화가
        # 시작되고 ISP 레지스터도 그 시점 이후에야 유효하다.
        if self._ready_camera_init:
            stages.append(("camera_init", self._ready_cam_state))
        # AE 정착은 카메라 init 다음 — AP1302 레지스터는 init 후에도 전이값을 거친다
        # (pim-check#61). 기대값을 단언하는 케이스에만 붙는다.
        if self._ready_ae_targets:
            stages.append(("ae_settle", self._ready_ae_settle))
        rec_paths = list(self._ready_recording_paths)
        if rec_paths:
            stages.append(("recording", lambda: self._ready_recording(rec_paths)))
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
        # readiness 전용 짧은 간격 사용 — 리부트 복구 poll_interval(60초)이 아니라
        # readiness_poll_interval(5초)로 디바운스해 준비되면 거의 즉시 진행한다.
        ready = self.wait_until_ready(
            stages, poll_interval=self.readiness_poll_interval, debounce=2,
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

    def run_setup(self, setup_config: dict, ready_processes=None,
                  ready_recording_paths=None, ready_camera_init: bool = False,
                  ready_cam_state_dir: str | None = None,
                  ready_cam_heartbeat_max_age_sec=None,
                  ready_ae_targets=None) -> bool:
        """현재 설정을 확인하고, 다를 경우에만 변경+재부팅한다.

        지원 키:
          - edgeconf_changes: /root/shared_v/edgeconf_pim.json 변경
          - ord_vcm_changes:  /root/shared_v/ord_vcm_conf.json 변경
          - inject_command:   reboot/stabilize 후 fault inject용 셸 명령 (str/list).
                              edgeconf/ord 변경 없이 inject만 있어도 동작.

        Args:
            ready_processes: 리부트 후 안정화 2차에서 생존을 확인할 코어 프로세스 목록
                (profile 의 checks.processes.required). None 이면 2차 단계 skip.
            ready_ae_targets: AE 정착 readiness 기대값 목록 (setup.ae_settle_targets).
                None/빈 목록이면 AE 정착 단계 skip.

        Returns:
            True: 변경 또는 inject가 적용됨 (teardown 필요)
            False: skip됨
        """
        # reboot_and_wait → _stabilize 가 참조하므로 reboot 전에 저장한다.
        self._ready_processes_list = list(ready_processes or [])
        self._ready_recording_paths = list(ready_recording_paths or [])
        self._ready_camera_init = bool(ready_camera_init)
        self._cam_ready_seen_at = None  # 이번 setup 의 settle 타이머 초기화
        self._cam_state_dir = ready_cam_state_dir or CAM_STATE_DIR
        try:
            self._cam_heartbeat_max_age = int(
                ready_cam_heartbeat_max_age_sec
                if ready_cam_heartbeat_max_age_sec is not None
                else CAM_READY_HEARTBEAT_MAX_AGE_SEC)
        except (TypeError, ValueError, OverflowError):
            # 임계 오염은 체크(checks.cam_state)가 FAIL 로 표면화한다 — 게이트는
            # 기본값으로 계속 간다(readiness 가 크래시로 run 을 죽이면 안 된다).
            self._cam_heartbeat_max_age = CAM_READY_HEARTBEAT_MAX_AGE_SEC
        self._ready_ae_targets = list(ready_ae_targets or [])
        self._ae_match_at = None    # 이번 setup 의 AE 정착 타이머 초기화
        self._config_snapshots = {}  # 이번 setup 의 복원 원본 (이전 케이스 잔존 제거)
        self._snapshot_failures = set()  # 이번 setup 의 스냅샷 실패 (#82)
        edge_changes = setup_config.get("edgeconf_changes", {})
        ord_changes = setup_config.get("ord_vcm_changes", {})
        inject = setup_config.get("inject_command")

        if not edge_changes and not ord_changes:
            # inject-only 모드: edgeconf 변경 없이 fault만 주입
            if inject:
                self._local0_log("setup INJECT-ONLY mode")
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
            # 복원 원본은 **변경 전에** 찍어야 한다. 스냅샷 실패는 '이번 케이스는
            # 복원 불가'일 뿐 오늘 동작과 같으므로 경고만 하고 진행한다 —
            # 케이스를 죽이는 편이 더 나쁘다.
            self._snapshot_or_warn(EDGECONF_PATH)
            if not self.backup(EDGECONF_PATH):
                print("ERROR: Failed to backup edgeconf — aborting setup")
                self._local0_log("setup ABORT — edgeconf backup failed")
                return False
            self.apply_changes(edge_changes, EDGECONF_PATH)

        if ord_changes and not ord_match:
            print(f"ord_vcm_conf differs — applying {len(ord_changes)} changes...")
            self._snapshot_or_warn(ORD_VCM_PATH)
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

    def _snapshot_or_warn(self, conf_path: str) -> None:
        """복원 원본 스냅샷. 실패해도 setup 을 중단하지 않고 경고만 남긴다.

        실패 경로는 `_snapshot_failures` 에 남긴다 — 이 경고는 "이 케이스는 복원
        불가" 까지만 말하고, 캠페인 기준선이 밀렸는지는 plan 이 채택 시점과
        대조해야 알 수 있다 (#82).
        """
        if self.snapshot_config(conf_path):
            return
        self._snapshot_failures.add(conf_path)
        print(f"WARNING: Failed to snapshot {conf_path} — teardown restore unavailable")
        self._local0_log(f"setup SNAPSHOT FAILED — {conf_path} (no teardown restore)")

    def _restore_conf(self, conf_path: str) -> None:
        """호스트 스냅샷으로 복원하고, 불가하면 보드 .bak 로 폴백한다.

        폴백에는 성격이 다른 두 경우가 섞인다. **스냅샷이 애초에 없는 것**은 정상이다
        (setup-skip 등 — 되돌릴 변경 자체가 없다). 반면 **스냅샷은 있는데 보드 복원이
        실패한 것**은 이상 징후이고, 폴백은 이 PR 이 없애려던 no-op 경로라 조용히
        끝난다. 둘을 같은 모양으로 남기면 후자가 정상 로그에 묻히므로 갈라서 찍는다.
        """
        had_snapshot = conf_path in self._config_snapshots
        if self.restore_from_snapshot(conf_path):
            self._local0_log(f"teardown RESTORE via host snapshot — {conf_path}")
            return
        if had_snapshot:
            print(f"WARNING: snapshot restore failed for {conf_path} — "
                  f"falling back to board .bak (likely a no-op)")
            self._local0_log(
                f"teardown RESTORE FAILED from snapshot — {conf_path} (.bak fallback)")
        else:
            self._local0_log(
                f"teardown RESTORE via board .bak (no snapshot) — {conf_path}")
        self.restore(conf_path)

    def adopt_snapshots(self, snapshots: dict) -> None:
        """다른 매니저가 찍은 복원 원본을 물려받는다.

        plan 은 케이스마다 teardown 하지 않고 캠페인 끝에서 한 번만 정리하므로,
        마지막 케이스의 매니저가 들고 있는 스냅샷(= 마지막 케이스 **직전** 상태)이
        아니라 **캠페인 시작 전** 상태로 되돌려야 한다 (pim-check#68).
        """
        self._config_snapshots = dict(snapshots)

    def _clear_readiness(self) -> None:
        """readiness 기대값을 비운다 — `_stabilize_stages()` 가 ssh 만 남게 된다.

        teardown 이 확인해야 할 것은 "보드가 살아 돌아왔는가" 뿐이다. 이 값들은
        **방금 끝난 케이스**의 프로필에서 유도된 것이라, 설정을 복원한 뒤의 보드를
        복원 전 기대값으로 게이팅하게 된다(pim-check#70). AE 정착은 gstApp 기동
        +16s 가 필요한데 teardown 예산은 20초라 들어갈 수도 없어, 매 실행 끝에
        20초를 버리고 경고만 찍혔다.

        `_config_snapshots` 는 **건드리지 않는다** — teardown 복원의 원본이다.

        부수 효과 하나: `_ready_camera_init=False` 라 teardown 재부팅에서는
        `session_anchor` 단계도 빠져 앵커를 다시 쓰지 않는다. 오늘은 무해하다 — 다음 실행이 setup-skip
        이면 케이스가 `uptime -s` 로 폴백하는데 지금은 앵커 == 부팅 시각이라 값이 같다.
        **하드리셋(앵커 = 리셋 시각 ≠ 부팅 시각)이 들어오면 달라지므로** 그때 이
        단계를 teardown 에도 남길지 재검토해야 한다.
        """
        self._ready_processes_list = []
        self._ready_recording_paths = []
        self._ready_camera_init = False
        self._cam_ready_seen_at = None
        self._cam_state_dir = CAM_STATE_DIR
        self._cam_heartbeat_max_age = CAM_READY_HEARTBEAT_MAX_AGE_SEC
        self._ready_ae_targets = []
        self._ae_match_at = None

    def run_teardown(self, setup_config: dict,
                     teardown_config: dict | None = None) -> None:
        """fault recovery + edgeconf/ord_vcm_conf 복원 + 필요시 재부팅.

        `recovery_command` 의 정본 자리는 케이스의 **`teardown:` 섹션**이다. 예전에는
        `setup:` 에서만 읽었는데 이 키를 쓰는 케이스 2건이 모두 `teardown:` 아래 두고
        있어 **복구가 한 번도 실행되지 않았다**(pim-check#75). `setup:` 쪽도 계속
        읽어 하위 호환을 지키되, 둘 다 있으면 teardown 쪽만 쓴다(중복 실행 금지).
        """
        self._clear_readiness()
        teardown_config = teardown_config or {}
        edge_changes = setup_config.get("edgeconf_changes", {})
        ord_changes = setup_config.get("ord_vcm_changes", {})
        recovery = (teardown_config.get("recovery_command")
                    or setup_config.get("recovery_command"))
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

        # 호스트 스냅샷 우선 — 보드 .bak 은 재부팅 때 config_guard 가 케이스 설정으로
        # 덮어써서 복원이 no-op 이 된다 (pim-check#65). 스냅샷이 없을 때만 .bak 로
        # 폴백한다(스냅샷 실패·설정 미적용 경로 — 오늘과 동일한 동작).
        if edge_changes:
            self._restore_conf(EDGECONF_PATH)
        if ord_changes:
            self._restore_conf(ORD_VCM_PATH)

        self._local0_log("teardown RESTORED — issuing reboot")

        if setup_config.get("reboot_after", False):
            self.reboot_and_wait(stabilize_sec=20)
            self._local0_log("teardown DONE — back online")
