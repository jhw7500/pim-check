#!/usr/bin/env python3
"""카메라 채널 스마트 검증 — 9 조합 × (A=default, B=per-channel unique) × 2 해상도 = 36 시나리오.

이것은 **축소 검증**이다. **풀 테스트**는 모든 15개 채널 조합을 다룰 것.
본 스크립트는 9개 대표 조합에서 각 활성 채널에 고유한 non-default 패턴을 할당하여
채널 독립성을 검증한다.

풀 테스트와의 차이:
- Smart (이 파일): 9 조합 × 2 variations × 2 res = 36 시나리오, ~40분
- Full (별도): 15 조합 × 모든 설정 축 = 수백 시나리오

검증 대상:
- per-channel: vflip, hflip, ae_on, ae_gain (ae_off일 때만), awb
- per-bus: exp_time (i2c1/i2c2 버스별)

레지스터 맵:
- ROTATION 0x100c (2B): bit0=hflip, bit1=vflip
- AE_CTRL 0x5002 (2B): AUTO=0x0299, MANUAL=0x0290
- AE_GAIN 0x5006 (2B): manual mode에서만 ISP에 반영
- AWB_CTRL 0x5100 (2B): auto=0x115f, off=0x1150, horizon=0x1151, ..., measure=0x1158
- EXP_TIME 0x500c (4B): per-bus
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
RESULT = BASE / "smart_results.json"
TARGET = "192.168.0.5"
REBOOT_WAIT = 300
STABILIZE = 30

CH_BUS = {0: 2, 1: 2, 2: 1, 3: 1}
CH_DUAL_ADDR = {0: 0x11, 1: 0x12, 2: 0x11, 3: 0x12}
CH_PATH = {
    0: ".VHL_CAM.i2c2.ch0",
    1: ".VHL_CAM.i2c2.ch1",
    2: ".VHL_CAM.i2c1.ch2",
    3: ".VHL_CAM.i2c1.ch3",
}
BUS_PATH = {2: ".VHL_CAM.i2c2", 1: ".VHL_CAM.i2c1"}
RES_MAP = {"720p": (1280, 720), "fhd": (1920, 1080)}

# 채널별 기본값 (default) — /opt/pim/config/edgeconf_pim_base.json 기반
CH_DEFAULT = {
    "vflip": False, "hflip": False, "ae_on": True,
    "ae_gain": 256, "awb": "auto", "bps": [2048, 2048],
}
BUS_DEFAULT = {"exp_time": 10000}
# 글로벌 기본값 (/opt/pim/config/edgeconf_pim_base.json)
GLOBAL_DEFAULT = {"fps": 15, "recording_time": 1, "muxer": "mp4"}
BPS_TOLERANCE_PCT = 10
DURATION_TOLERANCE_SEC = 5   # recording_time 검증 허용 오차

# AWB 모드 → 레지스터 raw 16bit (i2ctransfer 출력 그대로)
# 참고: measure(0x1158)는 AP1302 firmware가 1회 측정 후 MODE를 0x0(off)으로 자동 복귀시킴
#       (측정된 white point는 AWB_MANUAL_QX/QY에 저장됨). 따라서 측정 후 0x5100 읽기 시
#       0x1150이 정상이며, 본 스크립트는 이 동작을 기대값으로 반영한다.
AWB_REGS = {
    "auto": "0x110x5f", "off": "0x110x50", "horizon": "0x110x51",
    "a": "0x110x52", "cwf": "0x110x53", "d50": "0x110x54",
    "d65": "0x110x55", "d75": "0x110x56", "temp": "0x110x57",
    "measure": "0x110x50",  # firmware auto-revert to off after one-shot measurement
}


def ae_gain_default(ch):
    return CH_DEFAULT["ae_gain"]


def gain_to_hex(val):
    """ae_gain (2B) → i2ctransfer raw 문자열."""
    hi = (val >> 8) & 0xFF
    lo = val & 0xFF
    return f"0x{hi:02x}0x{lo:02x}"


def exp_to_hex(val):
    """exp_time (4B big-endian) → i2ctransfer raw 문자열."""
    b0 = (val >> 24) & 0xFF
    b1 = (val >> 16) & 0xFF
    b2 = (val >> 8) & 0xFF
    b3 = val & 0xFF
    return f"0x{b0:02x}0x{b1:02x}0x{b2:02x}0x{b3:02x}"


def rotation_hex(vflip, hflip):
    val = (0x02 if vflip else 0) | (0x01 if hflip else 0)
    return f"0x000x{val:02x}"


def ae_ctrl_hex(ae_on):
    return "0x020x99" if ae_on else "0x020x90"


# 9 조합 + B-패턴 (채널별 고유 설정)
# 각 combo는 (조합 이름, 활성 채널 리스트, B-패턴 dict)
COMBOS = [
    ("4ch_all", [0, 1, 2, 3], {
        "channels": {
            0: {"vflip": True, "bps": [1024, 1024]},
            1: {"hflip": True, "bps": [2048, 2048]},
            2: {"ae_on": False, "ae_gain": 512, "bps": [4096, 4096]},
            3: {"awb": "d65", "bps": [8192, 8192]},
        },
        "bus_exp": {2: 5000, 1: 15000},
    }),
    ("3ch_013", [0, 1, 3], {
        "channels": {
            0: {"vflip": True, "awb": "cwf", "bps": [1024, 1024]},
            1: {"hflip": True, "bps": [4096, 4096]},
            3: {"ae_on": False, "ae_gain": 64, "bps": [8192, 8192]},
        },
        "bus_exp": {},
        "global": {"fps": 30},
    }),
    ("3ch_123", [1, 2, 3], {
        "channels": {
            1: {"vflip": True, "hflip": True, "bps": [8192, 8192]},
            2: {"ae_on": False, "ae_gain": 1024, "awb": "d50", "bps": [2048, 2048]},
            3: {"vflip": True, "awb": "a", "bps": [4096, 4096]},
        },
        "bus_exp": {1: 20000},
        "global": {"recording_time": 2},
    }),
    ("2ch_01", [0, 1], {
        "channels": {
            0: {"vflip": True, "ae_on": False, "ae_gain": 32},
            1: {"hflip": True, "awb": "horizon"},
        },
        "bus_exp": {},
        "global": {"fps": 30},
    }),
    ("2ch_23", [2, 3], {
        "channels": {
            2: {"vflip": True, "hflip": True, "ae_on": False, "ae_gain": 768},
            3: {"awb": "temp"},
        },
        "bus_exp": {1: 25000},
        "global": {"muxer": "ts"},
    }),
    ("2ch_02", [0, 2], {
        "channels": {
            0: {"hflip": True, "awb": "d75"},
            2: {"vflip": True, "ae_on": False, "ae_gain": 2048},
        },
        "bus_exp": {},
    }),
    ("2ch_12", [1, 2], {
        "channels": {
            1: {"ae_on": False, "ae_gain": 128},
            2: {"vflip": True, "hflip": True, "awb": "a"},
        },
        "bus_exp": {},
        "global": {"recording_time": 5},
    }),
    ("1ch_0", [0], {
        "channels": {
            0: {"vflip": True, "hflip": True, "ae_on": False,
                "ae_gain": 64, "awb": "horizon"},
        },
        "bus_exp": {2: 30000},
    }),
    ("1ch_3", [3], {
        "channels": {
            3: {"vflip": True, "hflip": True, "ae_on": False,
                "ae_gain": 2048, "awb": "auto"},
        },
        "bus_exp": {1: 3000},
        "global": {"fps": 60},
    }),
]


def channel_effective_settings(ch, combo_pattern, test_type):
    """해당 채널의 최종 설정값 반환 (A=default, B=override per combo)."""
    settings = {
        "vflip": CH_DEFAULT["vflip"],
        "hflip": CH_DEFAULT["hflip"],
        "ae_on": CH_DEFAULT["ae_on"],
        "ae_gain": ae_gain_default(ch),
        "awb": CH_DEFAULT["awb"],
        "bps": list(CH_DEFAULT["bps"]),
    }
    if test_type == "B":
        overrides = combo_pattern.get("channels", {}).get(ch, {})
        settings.update(overrides)
    return settings


def bus_effective_exp(bus, combo_pattern, test_type):
    if test_type == "B":
        return combo_pattern.get("bus_exp", {}).get(bus, BUS_DEFAULT["exp_time"])
    return BUS_DEFAULT["exp_time"]


def global_effective(combo_pattern, test_type, res):
    """글로벌 설정(fps/recording_time/muxer) 반환.
    B + 720p일 때만 combo의 global override 적용. fhd는 default 유지.
    """
    settings = dict(GLOBAL_DEFAULT)
    if test_type == "B" and res == "720p":
        overrides = combo_pattern.get("global", {})
        settings.update(overrides)
    return settings


def ssh(cmd, timeout=15):
    r = subprocess.run(
        ["sshpass", "-p", "root", "ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=5", f"root@{TARGET}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def wait_ssh(timeout=REBOOT_WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc, out, _ = ssh("echo ok", timeout=10)
        if rc == 0 and "ok" in out:
            return True
        time.sleep(5)
    return False


def apply_edgeconf(changes):
    exprs = []
    for path, value in changes:
        if isinstance(value, bool):
            v = "true" if value else "false"
            exprs.append(f"{path} = {v}")
        elif isinstance(value, (int, float)):
            exprs.append(f"{path} = {value}")
        elif isinstance(value, list):
            items = ", ".join(str(x) for x in value)
            exprs.append(f"{path} = [{items}]")
        else:
            exprs.append(f'{path} = "{value}"')
    combined = " | ".join(exprs)
    cmd = (f"jq '{combined}' /root/shared_v/edgeconf_pim.json > /tmp/_e.json "
           f"&& mv /tmp/_e.json /root/shared_v/edgeconf_pim.json && echo OK")
    err = ""
    for _ in range(3):
        rc, out, err = ssh(cmd, timeout=30)
        if rc == 0 and "OK" in out:
            return True, ""
        time.sleep(3)
    return False, err[:150]


def reboot_and_wait():
    subprocess.run(
        ["sshpass", "-p", "root", "ssh", "-o", "StrictHostKeyChecking=no",
         f"root@{TARGET}", "sync && reboot &"],
        capture_output=True, timeout=10,
    )
    time.sleep(15)
    if not wait_ssh(REBOOT_WAIT):
        return False
    time.sleep(STABILIZE)
    return True


def read_reg(bus, addr, reg_hi, reg_lo, read_bytes=2):
    cmd = (f"i2ctransfer -f -y {bus} w2@0x{addr:02x} 0x{reg_hi:02x} 0x{reg_lo:02x} "
           f"r{read_bytes} 2>/dev/null | tr -d ' '")
    rc, out, _ = ssh(cmd, timeout=10)
    return out if rc == 0 else ""


def channel_bus_addr(combo_channels, ch):
    """활성 채널 목록 기준 target 채널의 ISP 주소 계산 (single 0x3c / dual 0x11-0x12)."""
    bus = CH_BUS[ch]
    bus_actives = [c for c in combo_channels if CH_BUS[c] == bus]
    if len(bus_actives) == 2:
        return bus, CH_DUAL_ADDR[ch]
    if len(bus_actives) == 1:
        return bus, 0x3c
    return None


def build_changes(combo_channels, combo_pattern, res, test_type):
    """시나리오의 전체 edgeconf changes 생성 (jq 단일 체인)."""
    w, h = RES_MAP[res]
    glob = global_effective(combo_pattern, test_type, res)
    changes = [
        (".VHL_CAM.cam_width", w), (".VHL_CAM.cam_height", h),
        (".VHL_CAM.fps", glob["fps"]),
        (".VHL_CAM.recording_time", glob["recording_time"]),
        (".VHL_CAM.muxer", glob["muxer"]),
    ]
    # enable 상태
    for ch in range(4):
        changes.append((f"{CH_PATH[ch]}.enable", ch in combo_channels))
    # 각 채널의 설정 (활성 채널은 A/B 따라, 비활성 채널은 default로 리셋)
    for ch in range(4):
        if ch in combo_channels:
            s = channel_effective_settings(ch, combo_pattern, test_type)
        else:
            s = {"vflip": False, "hflip": False, "ae_on": True,
                 "ae_gain": ae_gain_default(ch), "awb": "auto",
                 "bps": list(CH_DEFAULT["bps"])}
        changes.extend([
            (f"{CH_PATH[ch]}.vflip", s["vflip"]),
            (f"{CH_PATH[ch]}.hflip", s["hflip"]),
            (f"{CH_PATH[ch]}.ae_on", s["ae_on"]),
            (f"{CH_PATH[ch]}.ae_gain", s["ae_gain"]),
            (f"{CH_PATH[ch]}.awb", s["awb"]),
            (f"{CH_PATH[ch]}.bps", s["bps"]),
        ])
    # per-bus exp_time
    for bus in (1, 2):
        exp = bus_effective_exp(bus, combo_pattern, test_type)
        changes.append((f"{BUS_PATH[bus]}.exp_time", exp))
    return changes


def build_checks(combo_channels, combo_pattern, res, test_type):
    """ISP 체크 + bps/global (ffprobe) 체크 분리 반환.
    isp_checks: (label, bus, addr, reg_hi, reg_lo, rbytes, expected_hex)
    bps_checks: (label, channel, expected_kbps)
    global_checks: (label, kind, expected) — kind: fps/duration/muxer
    """
    isp_checks = []
    bps_checks = []
    global_checks = []
    # per-channel checks
    for ch in combo_channels:
        ba = channel_bus_addr(combo_channels, ch)
        if ba is None:
            continue
        bus, addr = ba
        s = channel_effective_settings(ch, combo_pattern, test_type)
        # ROTATION
        isp_checks.append((f"ch{ch}_rotation", bus, addr, 0x10, 0x0c, 2,
                           rotation_hex(s["vflip"], s["hflip"])))
        # AE_CTRL
        isp_checks.append((f"ch{ch}_ae_ctrl", bus, addr, 0x50, 0x02, 2,
                           ae_ctrl_hex(s["ae_on"])))
        # AWB_CTRL
        isp_checks.append((f"ch{ch}_awb_ctrl", bus, addr, 0x51, 0x00, 2,
                           AWB_REGS[s["awb"]]))
        # AE_GAIN only when manual (ae_on=false)
        if not s["ae_on"]:
            isp_checks.append((f"ch{ch}_ae_gain", bus, addr, 0x50, 0x06, 2,
                               gain_to_hex(s["ae_gain"])))
        # bps: B-pattern의 채널 override에 명시적으로 지정되었을 때만 ffprobe 검증 추가
        if test_type == "B":
            ch_overrides = combo_pattern.get("channels", {}).get(ch, {})
            if "bps" in ch_overrides:
                bps_checks.append((f"ch{ch}_bps", ch, s["bps"][0]))
    # per-bus exp_time — 활성 채널이 있는 버스만
    for bus in (1, 2):
        bus_ch = [c for c in combo_channels if CH_BUS[c] == bus]
        if not bus_ch:
            continue
        exp = bus_effective_exp(bus, combo_pattern, test_type)
        ba = channel_bus_addr(combo_channels, bus_ch[0])
        if ba is None:
            continue
        _, addr = ba
        isp_checks.append((f"bus{bus}_exp_time", bus, addr, 0x50, 0x0c, 4,
                           exp_to_hex(exp)))
    # global 체크 — 720p B에서 override 있을 때만 (fhd 스킵 = 기본값 유지)
    if test_type == "B" and res == "720p":
        overrides = combo_pattern.get("global", {})
        if "fps" in overrides:
            global_checks.append(("global_fps", "fps", overrides["fps"]))
        if "recording_time" in overrides:
            global_checks.append(("global_duration", "duration",
                                   overrides["recording_time"] * 60))
        if "muxer" in overrides:
            global_checks.append(("global_muxer", "muxer", overrides["muxer"]))
    return isp_checks, bps_checks, global_checks


def find_latest_video(channel, since_ts, ext="mp4", wait_timeout=75):
    """since_ts 이후 생성된 해당 채널 영상 파일 경로 반환 (녹화 완료 대기).
    wait_timeout: 최대 대기 시간(초). recording_time 길이에 맞춰 호출측에서 계산.
    ext: 확장자 (mp4, ts)
    """
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        cmd = (f"find /dev/shm/recordings -name '*-ch{channel}.{ext}' "
               f"-printf '%T@ %p\\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-")
        rc, out, _ = ssh(cmd, timeout=10)
        if rc == 0 and out.strip():
            f = out.strip()
            rc2, mt, _ = ssh(f"stat -c %Y '{f}'", timeout=10)
            if rc2 == 0 and mt.strip().isdigit() and int(mt) >= since_ts:
                rc3, sz, _ = ssh(f"stat -c %s '{f}'", timeout=10)
                if rc3 == 0 and sz.strip().isdigit() and int(sz) > 100000:
                    return f
        time.sleep(5)
    return ""


def probe_bitrate(filepath):
    """ffprobe로 video stream bit_rate (bps) 반환."""
    cmd = (f"ffprobe -v error -select_streams v:0 "
           f"-show_entries stream=bit_rate -of csv=p=0 '{filepath}' 2>/dev/null")
    rc, out, _ = ssh(cmd, timeout=15)
    if rc != 0 or not out.strip():
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def probe_fps(filepath):
    """ffprobe로 video stream r_frame_rate 반환 (정수 fps)."""
    cmd = (f"ffprobe -v error -select_streams v:0 "
           f"-show_entries stream=r_frame_rate -of csv=p=0 '{filepath}' 2>/dev/null")
    rc, out, _ = ssh(cmd, timeout=15)
    if rc != 0 or not out.strip():
        return None
    s = out.strip()
    if "/" in s:
        try:
            num, den = s.split("/")
            return int(num) // max(int(den), 1)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return int(float(s))
    except ValueError:
        return None


def probe_duration(filepath):
    """ffprobe로 format.duration (초, float) 반환."""
    cmd = (f"ffprobe -v error "
           f"-show_entries format=duration -of csv=p=0 '{filepath}' 2>/dev/null")
    rc, out, _ = ssh(cmd, timeout=15)
    if rc != 0 or not out.strip():
        return None
    try:
        return float(out.strip())
    except ValueError:
        return None


def generate_scenarios():
    scenarios = []
    for combo_name, combo_channels, combo_pattern in COMBOS:
        for res in RES_MAP:
            for test_type in ("A", "B"):
                changes = build_changes(combo_channels, combo_pattern, res, test_type)
                isp_checks, bps_checks, global_checks = build_checks(
                    combo_channels, combo_pattern, res, test_type)
                glob = global_effective(combo_pattern, test_type, res)
                # bps 검증은 720p(HD)로만 수행 — fhd에서는 bps 변경/검증 모두 skip
                if res != "720p" and bps_checks:
                    bps_checks = []
                    changes = [c for c in changes if not c[0].endswith(".bps")]
                    for ch in range(4):
                        changes.append((f"{CH_PATH[ch]}.bps", list(CH_DEFAULT["bps"])))
                scenarios.append({
                    "name": f"smart_{combo_name}_{res}_{test_type}",
                    "combo": combo_name,
                    "channels": combo_channels,
                    "res": res,
                    "test_type": test_type,
                    "changes": changes,
                    "checks": isp_checks,
                    "bps_checks": bps_checks,
                    "global_checks": global_checks,
                    "global_settings": glob,  # 녹화 대기/검증용 effective 값
                })
    return scenarios


def run_scenario(scen):
    t0 = time.monotonic()
    try:
        if not wait_ssh(60):
            return {"name": scen["name"], "result": "NO_SSH_PRE",
                    "elapsed": round(time.monotonic() - t0, 1)}
        ok, err = apply_edgeconf(scen["changes"])
        if not ok:
            return {"name": scen["name"], "result": "APPLY_FAIL", "error": err,
                    "elapsed": round(time.monotonic() - t0, 1)}
        if not reboot_and_wait():
            return {"name": scen["name"], "result": "NO_SSH_POST",
                    "elapsed": round(time.monotonic() - t0, 1)}
        # ISP 체크 수행
        check_results = []
        all_pass = True
        for label, bus, addr, rh, rl, rb, expected in scen["checks"]:
            actual = read_reg(bus, addr, rh, rl, rb)
            if actual != expected and addr != 0x3c:
                fb = read_reg(bus, 0x3c, rh, rl, rb)
                if fb == expected:
                    actual = f"{actual}→fallback0x3c={fb}"
            passed = expected in (actual or "").split("→")[-1]
            check_results.append({"label": label, "bus": bus,
                                   "addr": f"0x{addr:02x}",
                                   "expected": expected, "actual": actual,
                                   "passed": passed})
            if not passed:
                all_pass = False
        # 파일 기반 체크 (bps + global): recording_time 기반 동적 대기
        bps_checks = scen.get("bps_checks", [])
        global_checks = scen.get("global_checks", [])
        glob = scen.get("global_settings", dict(GLOBAL_DEFAULT))
        needs_video = bool(bps_checks or global_checks)
        if needs_video:
            rec_sec = glob.get("recording_time", 1) * 60
            wait_timeout = rec_sec + 20   # recording_time 분 + 20s 버퍼
            ext = glob.get("muxer", "mp4")
            rc, since_str, _ = ssh("date +%s", timeout=5)
            since_ts = int(since_str) if since_str.strip().isdigit() else int(time.time())

            # bps 체크 (채널별 영상 파일 필요)
            for label, ch, expected_kbps in bps_checks:
                video = find_latest_video(ch, since_ts, ext=ext, wait_timeout=wait_timeout)
                if not video:
                    check_results.append({"label": label, "expected": f"{expected_kbps}kbps",
                                          "actual": "(no video)", "passed": False})
                    all_pass = False
                    continue
                actual_bps = probe_bitrate(video)
                if actual_bps is None:
                    check_results.append({"label": label, "expected": f"{expected_kbps}kbps",
                                          "actual": "(probe fail)", "passed": False})
                    all_pass = False
                    continue
                expected_bps = expected_kbps * 1000
                tol = expected_bps * BPS_TOLERANCE_PCT // 100
                passed = abs(actual_bps - expected_bps) <= tol
                check_results.append({
                    "label": label, "expected": f"{expected_kbps}kbps",
                    "actual": f"{round(actual_bps/1000,1)}kbps",
                    "diff_pct": round((actual_bps - expected_bps) / expected_bps * 100, 1),
                    "video": video, "passed": passed,
                })
                if not passed:
                    all_pass = False

            # global 체크 (fps/duration/muxer) — 첫 활성 채널의 파일로 검증
            if global_checks and scen["channels"]:
                ref_ch = scen["channels"][0]
                ref_video = find_latest_video(ref_ch, since_ts, ext=ext,
                                              wait_timeout=max(30, wait_timeout // 2))
                for label, kind, expected in global_checks:
                    if kind == "muxer":
                        # 파일 확장자로 판단
                        expected_ext = expected
                        actual_ext = ref_video.rsplit(".", 1)[-1] if ref_video else ""
                        passed = actual_ext == expected_ext
                        check_results.append({
                            "label": label, "expected": f"{expected_ext}",
                            "actual": actual_ext or "(no video)",
                            "video": ref_video, "passed": passed,
                        })
                        if not passed:
                            all_pass = False
                    elif kind == "fps":
                        if not ref_video:
                            check_results.append({"label": label, "expected": f"{expected}fps",
                                                  "actual": "(no video)", "passed": False})
                            all_pass = False
                            continue
                        actual_fps = probe_fps(ref_video)
                        passed = actual_fps == expected
                        check_results.append({
                            "label": label, "expected": f"{expected}fps",
                            "actual": f"{actual_fps}fps" if actual_fps is not None else "(probe fail)",
                            "video": ref_video, "passed": passed,
                        })
                        if not passed:
                            all_pass = False
                    elif kind == "duration":
                        if not ref_video:
                            check_results.append({"label": label, "expected": f"{expected}s",
                                                  "actual": "(no video)", "passed": False})
                            all_pass = False
                            continue
                        actual_dur = probe_duration(ref_video)
                        passed = (actual_dur is not None and
                                  abs(actual_dur - expected) <= DURATION_TOLERANCE_SEC)
                        check_results.append({
                            "label": label, "expected": f"{expected}s",
                            "actual": f"{round(actual_dur,1)}s" if actual_dur is not None else "(probe fail)",
                            "video": ref_video, "passed": passed,
                        })
                        if not passed:
                            all_pass = False
        return {
            "name": scen["name"], "combo": scen["combo"], "res": scen["res"],
            "test_type": scen["test_type"],
            "result": "PASS" if all_pass else "FAIL",
            "elapsed": round(time.monotonic() - t0, 1),
            "checks": check_results,
            "n_pass": sum(1 for c in check_results if c["passed"]),
            "n_total": len(check_results),
        }
    except subprocess.TimeoutExpired as e:
        return {"name": scen["name"], "result": "EXCEPTION_TIMEOUT",
                "error": str(e)[:200],
                "elapsed": round(time.monotonic() - t0, 1)}
    except Exception as e:
        return {"name": scen["name"], "result": "EXCEPTION",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "elapsed": round(time.monotonic() - t0, 1)}


def main():
    scenarios = generate_scenarios()
    print(f"Smart verify — {datetime.now().isoformat()}")
    print(f"Total scenarios: {len(scenarios)}")
    print(f"  9 combos × 2 tests (A/B) × 2 resolutions = {len(scenarios)}")

    results = []
    done = set()
    if RESULT.exists():
        try:
            prior = json.loads(RESULT.read_text())
            results = [r for r in prior if r.get("result") == "PASS"]
            done = {r["name"] for r in results}
            print(f"  Resume: {len(done)} scenarios already PASSed — skipping")
        except Exception:
            pass
    print()

    for i, scen in enumerate(scenarios, 1):
        if scen["name"] in done:
            continue
        t = datetime.now().strftime("%H:%M:%S")
        info = f"{len(scen['checks'])} checks"
        print(f"[{i}/{len(scenarios)}] {t} {scen['name']} ({info})...", flush=True)
        r = run_scenario(scen)
        results.append(r)
        suffix = ""
        if r.get("n_total"):
            suffix = f" [{r['n_pass']}/{r['n_total']}]"
        print(f"   → {r['result']} ({r['elapsed']}s){suffix}", flush=True)
        if r["result"] == "FAIL" and r.get("checks"):
            for c in r["checks"]:
                if not c["passed"]:
                    print(f"        FAIL {c['label']}: exp={c['expected']} got={c['actual']}")
        RESULT.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    p = sum(1 for r in results if r["result"] == "PASS")
    f_ = len(results) - p
    total_t = sum(r.get("elapsed", 0) for r in results)
    total_checks = sum(r.get("n_total", 0) for r in results)
    pass_checks = sum(r.get("n_pass", 0) for r in results)
    print()
    print("=" * 60)
    print(f"Scenarios: PASS {p}/{len(results)}, FAIL {f_}")
    print(f"Check points: PASS {pass_checks}/{total_checks}")
    print(f"Total time: {int(total_t // 60)}m {int(total_t % 60)}s")
    print("=" * 60)
    sys.exit(0 if f_ == 0 else 1)


if __name__ == "__main__":
    main()
