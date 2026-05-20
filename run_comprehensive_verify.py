#!/usr/bin/env python3
"""채널별 카메라 설정 포괄 검증 — Phase 2 (Quad) + Phase 3 (Dual).

테스트 매트릭스:
- Phase 2: 4채널 전부 활성 + 각 채널 vflip/hflip/ae/awb 개별 토글 (32 tests/res)
- Phase 3: 듀얼 조합 (4조합: 동일버스 2 + 교차버스 2) × 각 채널 × 4 설정 (64 tests/res)
- 해상도: 720p + fhd

설정 → 레지스터 매핑:
- vflip/hflip → ROTATION (0x100c): bit0=hflip, bit1=vflip
- ae_on → AE_CTRL (0x5002): AUTO=0x0299, MANUAL=0x0290
- awb → AWB_CTRL (0x5100): auto=0x115f, off=0x1150, ...

주소 모드:
- 채널 버스(i2c-2=ch0,ch1 / i2c-1=ch2,ch3)에 1채널만 enable: single mode 0x3c
- 2채널 enable: dual mode, ch0/ch2=0x11, ch1/ch3=0x12
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
RESULT = BASE / "comprehensive_results.json"
# 타겟 IP는 환경변수 TARGET_HOST 우선, 없으면 fallback.
TARGET = os.environ.get("TARGET_HOST", "192.168.0.5")
REBOOT_WAIT = 450   # reboot 후 SSH 복귀 최대 대기 (초) — 정상 빨리 복귀 시 break 하므로 정상 비용 0
STABILIZE = 30
RETRY_EXTRA_WAIT = 90   # 1차 read 실패 시 추가 안정화 대기 (초) 후 재시도
RETRY_COUNT = 3         # 재시도 횟수 (총 1차 + RETRY_COUNT회) — 정상 비용 0, retry 발생 시 회복률↑
STOP_ON_FAIL = False    # 96 시나리오 전수 실행 (cron 깊은 회귀 검증 목적).
# 2026-05-19 실측: EXCEPTION_TIMEOUT이 ~15% 빈도로 발생(SSH/i2c 일시 hang),
# 대부분 1회 재시도로 흡수되나 가끔 연속 발생. True면 일회성 timing 이슈로 70 시나리오
# 미실행 → 깊은 검증 의도와 충돌. False로 전수 실행 후 FAIL 패턴 분석.

# 채널 → 버스 매핑
CH_BUS = {0: 2, 1: 2, 2: 1, 3: 1}
# 채널 → dual mode 주소 (ch0/ch2는 low=0x11, ch1/ch3는 high=0x12)
CH_DUAL_ADDR = {0: 0x11, 1: 0x12, 2: 0x11, 3: 0x12}
# 채널 → edgeconf jq path
CH_PATH = {
    0: ".VHL_CAM.i2c2.ch0",
    1: ".VHL_CAM.i2c2.ch1",
    2: ".VHL_CAM.i2c1.ch2",
    3: ".VHL_CAM.i2c1.ch3",
}

# Resolution → width/height
RES_MAP = {"720p": (1280, 720), "fhd": (1920, 1080)}

# Setting → (register, expected_default_value, test_value, test_expected_register)
# value_default/test_value는 edgeconf에 쓰는 값, expected_register는 i2c로 읽을 때 기대값
SETTING_TESTS = {
    "vflip": {
        "reg_hi": 0x10, "reg_lo": 0x0c,
        "default_ec": False, "test_ec": True,
        "default_hex": "0x000x00", "test_hex": "0x000x02",
    },
    "hflip": {
        "reg_hi": 0x10, "reg_lo": 0x0c,
        "default_ec": False, "test_ec": True,
        "default_hex": "0x000x00", "test_hex": "0x000x01",
    },
    "ae_on": {
        "reg_hi": 0x50, "reg_lo": 0x02,
        "default_ec": True, "test_ec": False,
        "default_hex": "0x020x99", "test_hex": "0x020x90",
    },
    "awb": {
        "reg_hi": 0x51, "reg_lo": 0x00,
        "default_ec": "auto", "test_ec": "off",
        "default_hex": "0x110x5f", "test_hex": "0x110x50",
    },
}


def ssh(cmd, timeout=15):
    r = subprocess.run(
        ["sshpass", "-p", "root", "ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=15", f"root@{TARGET}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def wait_ssh(timeout=REBOOT_WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            rc, out, _ = ssh("echo ok", timeout=10)
        except subprocess.TimeoutExpired:
            # ssh 연결이 ConnectTimeout 안에 안 끝나 subprocess timeout이 먼저 발동
            # (보드 부팅 직후 SSH 미준비 시 정상 발생). 예외를 전파하면 run_scenario가
            # EXCEPTION_TIMEOUT으로 오판 → 재시도 못 하고 시나리오 실패.
            # 2026-05-19 run#3 0/90 회귀: ConnectTimeout=15 >= subprocess timeout=10이
            # 되며 이 경로가 EXCEPTION_TIMEOUT으로 터진 것 수정. 여기서 재시도로 흡수.
            time.sleep(5)
            continue
        if rc == 0 and "ok" in out:
            return True
        time.sleep(5)
    return False


def apply_edgeconf(changes: list[tuple[str, object]]):
    """모든 변경을 단일 jq 체인으로 한 번에 적용 (SSH 1회).

    값 직렬화는 json.dumps를 사용해 모든 JSON 타입(bool/int/float/str/list/dict)을 정확히 변환.
    이전 구현은 list/dict가 string으로 wrap돼 bps=[2048,1024] 같은 array가
    "[2048, 1024]" 문자열로 잘못 저장되는 버그가 있었음.
    """
    exprs = []
    for path, value in changes:
        # bool은 Python json.dumps가 "true"/"false"로 정확히 출력 — jq 호환.
        # list/dict도 JSON 그대로 출력 (jq는 JSON literal 그대로 수용).
        v_jq = json.dumps(value)
        exprs.append(f"{path} = {v_jq}")
    combined = " | ".join(exprs)
    cmd = (f"jq '{combined}' /root/shared_v/edgeconf_pim.json > /tmp/_e.json "
           f"&& mv /tmp/_e.json /root/shared_v/edgeconf_pim.json && echo OK")
    # 최대 3회 재시도
    err = ""
    for attempt in range(3):
        rc, out, err = ssh(cmd, timeout=30)
        if rc == 0 and "OK" in out:
            return True, ""
        time.sleep(3)
    return False, f"apply failed after retries: {err[:150]}"


def reboot_and_wait():
    subprocess.run(
        ["sshpass", "-p", "root", "ssh", "-o", "StrictHostKeyChecking=no",
         f"root@{TARGET}", "sync && reboot &"],
        capture_output=True, timeout=10,
    )
    time.sleep(15)
    if not wait_ssh(timeout=REBOOT_WAIT):
        return False
    time.sleep(STABILIZE)
    return True


def read_isp(bus: int, addr: int, reg_hi: int, reg_lo: int) -> str:
    cmd = f"i2ctransfer -f -y {bus} w2@0x{addr:02x} 0x{reg_hi:02x} 0x{reg_lo:02x} r2 2>/dev/null | tr -d ' '"
    # read_isp는 run_scenario try 밖 + main 루프에도 try 없음 → 여기서 TimeoutExpired가
    # 새면 스크립트 전체 크래시. ssh timeout(15)==ConnectTimeout(15) 경계라 느린 연결 시
    # 발생 가능. ""(빈 결과)로 변환하면 run_scenario의 RETRY_COUNT 재시도 로직이 흡수.
    try:
        rc, out, _ = ssh(cmd)
    except subprocess.TimeoutExpired:
        return ""
    return out if rc == 0 else ""


def run_scenario(scen: dict) -> dict:
    """단일 시나리오 실행 후 결과 반환."""
    t0 = time.monotonic()
    try:
        # 1) 현재 접속 확인
        if not wait_ssh(60):
            return {**scen, "result": "NO_SSH_PRE", "elapsed": round(time.monotonic() - t0, 1)}

        # 2) edgeconf 변경 적용
        changes = scen["changes"]
        ok, err = apply_edgeconf(changes)
        if not ok:
            return {**scen, "result": "APPLY_FAIL", "error": err,
                    "elapsed": round(time.monotonic() - t0, 1)}

        # 3) 재부팅 + 대기
        if not reboot_and_wait():
            return {**scen, "result": "NO_SSH_POST",
                    "elapsed": round(time.monotonic() - t0, 1)}
    except subprocess.TimeoutExpired as e:
        return {**scen, "result": "EXCEPTION_TIMEOUT",
                "error": str(e)[:200],
                "elapsed": round(time.monotonic() - t0, 1)}
    except Exception as e:
        return {**scen, "result": "EXCEPTION",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "elapsed": round(time.monotonic() - t0, 1)}

    # 4) ISP 레지스터 읽기 (1차 + 실패 시 RETRY_COUNT회 추가 대기 후 재시도)
    bus = scen["bus"]
    addr = scen["addr"]
    reg_hi = scen["reg_hi"]
    reg_lo = scen["reg_lo"]
    expected = scen["expected_hex"]

    actual = read_isp(bus, addr, reg_hi, reg_lo)
    passed = (actual == expected)
    retries_used = 0
    actual_history = [actual or "(no response)"]

    # 5) 1차 실패 시 추가 안정화 대기 후 재시도.
    #    (단발 timing/ISP unstable 이슈 회피)
    while not passed and retries_used < RETRY_COUNT:
        time.sleep(RETRY_EXTRA_WAIT)
        actual = read_isp(bus, addr, reg_hi, reg_lo)
        passed = (actual == expected)
        retries_used += 1
        actual_history.append(actual or "(no response)")

    # 6) 의도한 주소에서 값이 일치해야만 PASS (fallback 없음, FINDING #12 정정).
    return {
        **scen,
        "actual": actual or "(no response)",
        "result": "PASS" if passed else "FAIL",
        "retries_used": retries_used,
        "actual_history": actual_history,  # 재시도 추적 (1차/N차 변화 분석)
        "elapsed": round(time.monotonic() - t0, 1),
    }


def generate_scenarios():
    """Phase 2 (quad) + Phase 3 (dual) 시나리오 생성."""
    scenarios = []

    # Phase 2: 모든 4 채널 활성화 + 한 채널의 설정만 토글
    # 모든 테스트 시작 전 모든 per-channel 설정 + 글로벌 설정을 defaults로 완전 리셋 (이전 테스트 잔존 방지).
    # FINDING #13 (docs/03-analysis/verification-audit-2026-04-22.md) 정정:
    # 기존은 SETTING_TESTS(vflip/hflip/ae/awb)만 reset → bps/fps/muxer/recording_time/capture 잔존 가능.
    # → bps=[2048,1024], fps=15, recording_time=1, muxer=mp4, capture.enable=false도 함께 reset.
    def build_reset_changes(w, h, enables):
        """해상도 + 글로벌 defaults + 모든 per-channel 설정 defaults로 리셋."""
        chs = [
            (".VHL_CAM.cam_width", w),
            (".VHL_CAM.cam_height", h),
            (".VHL_CAM.fps", 15),
            (".VHL_CAM.recording_time", 1),
            (".VHL_CAM.muxer", "mp4"),
            (".VHL_CAM.capture.enable", False),
        ]
        for ch in range(4):
            chs.append((f"{CH_PATH[ch]}.enable", enables[ch]))
            chs.append((f"{CH_PATH[ch]}.bps", [2048, 1024]))
            for s_name, s_info in SETTING_TESTS.items():
                chs.append((f"{CH_PATH[ch]}.{s_name}", s_info["default_ec"]))
        return chs

    for res_name, (w, h) in RES_MAP.items():
        for target_ch in range(4):
            for setting, info in SETTING_TESTS.items():
                # 1) 모든 채널 enable + 모든 설정 defaults
                changes = build_reset_changes(w, h, [True] * 4)
                # 2) target 채널의 target 설정만 test_value로 오버라이드
                changes.append((f"{CH_PATH[target_ch]}.{setting}", info["test_ec"]))

                # dual mode (2 channels on bus) → ch의 dual addr
                scenarios.append({
                    "phase": 2, "mode": "quad", "res": res_name,
                    "target_ch": target_ch, "setting": setting,
                    "name": f"p2_quad_{res_name}_ch{target_ch}_{setting}",
                    "changes": changes,
                    "bus": CH_BUS[target_ch],
                    "addr": CH_DUAL_ADDR[target_ch],
                    "reg_hi": info["reg_hi"], "reg_lo": info["reg_lo"],
                    "expected_hex": info["test_hex"],
                })

    # Phase 3: 듀얼 조합 — 4 combos × 2 channels per combo
    dual_combos = [
        ("samebus_i2c2", (0, 1)),   # ch0+ch1, same bus i2c-2, dual mode
        ("samebus_i2c1", (2, 3)),   # ch2+ch3, same bus i2c-1, dual mode
        ("crossbus_lo", (0, 2)),    # ch0+ch2, cross-bus, single mode each
        ("crossbus_hi", (1, 3)),    # ch1+ch3, cross-bus, single mode each
    ]
    for res_name, (w, h) in RES_MAP.items():
        for combo_name, (a, b) in dual_combos:
            for target_ch in (a, b):
                for setting, info in SETTING_TESTS.items():
                    # 활성 채널 세트
                    enables = [ch in (a, b) for ch in range(4)]
                    # 1) 모든 채널(활성/비활성) 설정 defaults + target 채널만 enable 적용
                    changes = build_reset_changes(w, h, enables)
                    # 2) target 채널의 target 설정만 test_value
                    changes.append((f"{CH_PATH[target_ch]}.{setting}", info["test_ec"]))

                    # 동일 버스 조합(같은 bus의 2 ch): dual mode
                    # 교차 버스 조합(각 bus에 1 ch씩): 각 bus single mode
                    same_bus = CH_BUS[a] == CH_BUS[b]
                    if same_bus:
                        addr = CH_DUAL_ADDR[target_ch]
                    else:
                        addr = 0x3c  # single mode on target's bus

                    scenarios.append({
                        "phase": 3, "mode": f"dual_{combo_name}", "res": res_name,
                        "target_ch": target_ch, "setting": setting,
                        "name": f"p3_{combo_name}_{res_name}_ch{target_ch}_{setting}",
                        "changes": changes,
                        "bus": CH_BUS[target_ch],
                        "addr": addr,
                        "reg_hi": info["reg_hi"], "reg_lo": info["reg_lo"],
                        "expected_hex": info["test_hex"],
                    })

    return scenarios


def main():
    scenarios = generate_scenarios()
    print(f"Comprehensive verify — {datetime.now().isoformat()}")
    print(f"Total scenarios: {len(scenarios)}")
    print(f"  Phase 2 (quad): {sum(1 for s in scenarios if s['phase']==2)}")
    print(f"  Phase 3 (dual): {sum(1 for s in scenarios if s['phase']==3)}")

    # Resume: 기존 결과 로드하여 이미 PASS한 시나리오 스킵
    results = []
    done_names: set[str] = set()
    if RESULT.exists():
        try:
            prior = json.loads(RESULT.read_text())
            results = [r for r in prior if r.get("result") == "PASS"]
            done_names = {r["name"] for r in results}
            print(f"  Resume: {len(done_names)} scenarios already PASSed — skipping")
        except Exception:
            pass
    print()

    for i, scen in enumerate(scenarios, 1):
        if scen["name"] in done_names:
            continue
        t_start = datetime.now().strftime("%H:%M:%S")
        print(f"[{i}/{len(scenarios)}] {t_start} {scen['name']}...", flush=True)
        r = run_scenario(scen)

        # EXCEPTION_TIMEOUT: SSH 일시 timeout — STOP하지 않고 60s 대기 후 시나리오 1회 retry.
        # 단발 SSH timeout은 진짜 결함이 아니므로 회복 시도.
        if r["result"] == "EXCEPTION_TIMEOUT":
            print("   ⚠️  EXCEPTION_TIMEOUT — 60s 대기 후 재시도", flush=True)
            time.sleep(60)
            r2 = run_scenario(scen)
            r2["after_exception_retry"] = True
            r = r2

        results.append(r)
        mark = "[+]" if r["result"] == "PASS" else "[X]"
        actual_str = r.get("actual", "-")
        print(f"   {mark} {r['result']} ({r['elapsed']}s) expected={scen['expected_hex']} got={actual_str}",
              flush=True)
        RESULT.write_text(json.dumps(results, indent=2, ensure_ascii=False))

        # STOP_ON_FAIL: EXCEPTION_TIMEOUT 재시도 후에도 PASS 아니면 즉시 중단.
        # edgeconf는 해당 시나리오의 설정 그대로 남아 있어 수동 진단 가능.
        if STOP_ON_FAIL and r["result"] != "PASS":
            print()
            print("=" * 60)
            print("⚠️  STOP_ON_FAIL 활성: 첫 FAIL 발생 → 즉시 중단")
            print(f"   시나리오: {scen['name']}")
            print(f"   결과: {r['result']}, expected={scen['expected_hex']}, actual={actual_str}")
            print("   타겟 edgeconf는 이 시나리오 설정으로 남아있음.")
            print("   수동 검증 명령:")
            print(f"     ssh root@{TARGET}")
            print(f"     i2ctransfer -f -y {scen['bus']} w2@0x{scen['addr']:02x} "
                  f"0x{scen['reg_hi']:02x} 0x{scen['reg_lo']:02x} r2 | tr -d ' '")
            print(f"   기대값: {scen['expected_hex']}")
            print("=" * 60)
            sys.exit(2)

    # 요약
    p = sum(1 for r in results if r["result"] == "PASS")
    f = len(results) - p
    total_t = sum(r["elapsed"] for r in results)
    print()
    print("=" * 60)
    print(f"PASS: {p}/{len(results)}, FAIL: {f}")
    print(f"Total time: {int(total_t // 60)}m {int(total_t % 60)}s")
    print("=" * 60)

    if f > 0:
        print("\n실패 시나리오:")
        for r in results:
            if r["result"] != "PASS":
                print(f"  [{r['result']}] {r['name']}")
                print(f"       expected={r.get('expected_hex')} got={r.get('actual')}")

    sys.exit(0 if f == 0 else 1)


if __name__ == "__main__":
    main()
