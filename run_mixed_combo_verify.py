#!/usr/bin/env python3
"""
4가지 채널 조합 × 서로 다른 채널별 설정 혼합 검증.

목적:
  - 2026-04-22 schema 정정 이후, 최신 드라이버(수정본)에서
    단독/이중/4채널 모드 모두에서 채널별 설정이 ISP에 정확히 반영되는지 확인.
  - 각 채널에 서로 다른 (vflip/hflip/ae/awb) 조합을 주입하여
    "한 채널의 설정이 다른 채널로 누설/덮어쓰기 되지 않는지" 동시 검증.

테스트:
  1) ch1+ch3 cross-bus (양 버스 single, 내부 ch1 슬롯 재검증)
  2) ch0+ch2 cross-bus (양 버스 single, 내부 ch0 슬롯 회귀)
  3) ch0+ch1 same-bus i2c-2 dual
  4) ch0,ch1,ch2,ch3 quad (양 버스 dual)
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

TARGET = "192.168.0.5"
EDGECONF = "/root/shared_v/edgeconf_pim.json"

# 설정 조합 (vflip, hflip, ae_on, awb) + 예상 레지스터 값
COMBO = {
    "A": {
        "vflip": True, "hflip": False, "ae_on": True, "awb": "auto",
        "expect_rot": "0x000x02", "expect_ae": "0x020x99", "expect_awb": "0x110x5f",
    },
    "B": {
        "vflip": False, "hflip": True, "ae_on": False, "awb": "off",
        "expect_rot": "0x000x01", "expect_ae": "0x020x90", "expect_awb": "0x110x50",
    },
    "C": {
        "vflip": True, "hflip": True, "ae_on": True, "awb": "off",
        "expect_rot": "0x000x03", "expect_ae": "0x020x99", "expect_awb": "0x110x50",
    },
    "D": {
        "vflip": False, "hflip": False, "ae_on": False, "awb": "auto",
        "expect_rot": "0x000x00", "expect_ae": "0x020x90", "expect_awb": "0x110x5f",
    },
}

CH_INFO = {
    0: {"bus": 2, "path": ".VHL_CAM.i2c2.ch0", "dual_addr": "0x11"},
    1: {"bus": 2, "path": ".VHL_CAM.i2c2.ch1", "dual_addr": "0x12"},
    2: {"bus": 1, "path": ".VHL_CAM.i2c1.ch2", "dual_addr": "0x11"},
    3: {"bus": 1, "path": ".VHL_CAM.i2c1.ch3", "dual_addr": "0x12"},
}

TESTS = [
    {"id": 1, "name": "ch1+ch3 cross-bus (internal ch1 slot × 2, single mode)",
     "enabled": {1: "A", 3: "B"}},
    {"id": 2, "name": "ch0+ch2 cross-bus (internal ch0 slot × 2, single mode)",
     "enabled": {0: "C", 2: "D"}},
    {"id": 3, "name": "ch0+ch1 same-bus i2c-2 dual",
     "enabled": {0: "A", 1: "B"}},
    {"id": 4, "name": "quad (all 4 channels, both buses dual)",
     "enabled": {0: "A", 1: "B", 2: "C", 3: "D"}},
]


def ssh(cmd: str, timeout: int = 15) -> tuple[int, str, str]:
    r = subprocess.run(
        ["sshpass", "-p", "root", "ssh",
         "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         "-o", "LogLevel=ERROR",
         "-o", "ConnectTimeout=5",
         f"root@{TARGET}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode, r.stdout, r.stderr


def wait_target_up(total_timeout: int = 300) -> bool:
    """ping 복구 → SSH 복구까지 대기.

    SSH 시도 시 TimeoutExpired가 발생하면 루프가 중단되지 않도록 예외 catch.
    """
    t0 = time.time()
    # ping down 확인 후 up 확인
    down_seen = False
    while time.time() - t0 < total_timeout:
        rc = subprocess.run(["ping", "-c", "1", "-W", "1", TARGET],
                             capture_output=True).returncode
        if rc != 0:
            down_seen = True
        elif down_seen:
            break
        time.sleep(1)
    # SSH 복구
    while time.time() - t0 < total_timeout:
        try:
            rc, _, _ = ssh("echo ok", timeout=5)
            if rc == 0:
                return True
        except subprocess.TimeoutExpired:
            pass  # SSH 응답 없음 — 계속 대기
        time.sleep(2)
    return False


def apply_edgeconf(enabled: dict[int, str]) -> None:
    """Cleanroom reset → 대상 채널만 enable + 설정 주입.

    FINDING #15 (docs/03-analysis/verification-audit-2026-04-22.md) 정정:
    이전 버전은 채널 enable/vflip/hflip/ae/awb만 세팅하고 bps/fps/muxer/recording_time/capture 및
    비활성 채널의 per-channel settings를 방치 → 이전 테스트 state가 누설되어 간헐적 false PASS 가능.
    → 모든 global + per-channel 설정을 defaults로 전량 reset 후 대상만 override.
    """
    # 1. Global 설정 defaults
    jq_parts = [
        ".VHL_CAM.cam_width = 1280",
        ".VHL_CAM.cam_height = 720",
        ".VHL_CAM.fps = 15",
        ".VHL_CAM.recording_time = 1",
        '.VHL_CAM.muxer = "mp4"',
        ".VHL_CAM.capture.enable = false",
    ]
    # 2. 모든 채널을 defaults로 reset (enable=false + per-channel settings)
    for ch in range(4):
        path = CH_INFO[ch]["path"]
        jq_parts.extend([
            f"{path}.enable = false",
            f"{path}.vflip = false",
            f"{path}.hflip = false",
            f"{path}.ae_on = true",
            f'{path}.awb = "auto"',
            f"{path}.bps = [2048, 1024]",
        ])
    # 3. enabled 채널: enable=true + 대상 설정 적용 (위 defaults를 override)
    for ch, combo_name in enabled.items():
        info = CH_INFO[ch]
        combo = COMBO[combo_name]
        jq_parts.extend([
            f"{info['path']}.enable = true",
            f"{info['path']}.vflip = {str(combo['vflip']).lower()}",
            f"{info['path']}.hflip = {str(combo['hflip']).lower()}",
            f"{info['path']}.ae_on = {str(combo['ae_on']).lower()}",
            f'{info["path"]}.awb = "{combo["awb"]}"',
        ])

    jq_expr = " | ".join(jq_parts)
    cmd = (
        f"jq '{jq_expr}' {EDGECONF} > /tmp/eff.json && "
        f"mv /tmp/eff.json {EDGECONF}"
    )
    rc, out, err = ssh(cmd)
    if rc != 0:
        raise RuntimeError(f"edgeconf apply failed: {err}")


def addr_for(ch: int, enabled_on_bus_count: int) -> str:
    info = CH_INFO[ch]
    return info["dual_addr"] if enabled_on_bus_count == 2 else "0x3c"


def read_reg(bus: int, addr: str, reg_hi: str, reg_lo: str) -> str:
    """ISP 레지스터 2바이트 읽기. 실패 시 'N/A' 반환."""
    cmd = (
        f"i2ctransfer -f -y {bus} w2@{addr} {reg_hi} {reg_lo} r2 2>/dev/null "
        f"| tr -d ' '"
    )
    rc, out, _ = ssh(cmd)
    result = out.strip()
    return result if result else "N/A"


def run_test(test: dict) -> dict:
    print(f"\n{'='*70}")
    print(f"[Test {test['id']}] {test['name']}")
    print(f"  enabled: {test['enabled']}")
    print(f"{'='*70}")

    # 버스별 enabled 채널 수 계산
    bus_counts = {1: 0, 2: 0}
    for ch in test["enabled"]:
        bus_counts[CH_INFO[ch]["bus"]] += 1

    # edgeconf 적용
    print(f"[{time.strftime('%T')}] edgeconf 적용...")
    apply_edgeconf(test["enabled"])

    # 재부팅
    print(f"[{time.strftime('%T')}] reboot...")
    ssh("reboot", timeout=5)
    time.sleep(3)

    # 복구 대기
    print(f"[{time.strftime('%T')}] 복구 대기...")
    if not wait_target_up():
        return {"test_id": test["id"], "name": test["name"], "error": "target not recovered"}
    print(f"[{time.strftime('%T')}] 복구 완료. 30s 안정화...")
    time.sleep(30)

    # i2c 모드 진입 사전 검증 (FINDING #15 정정):
    # AP1302 특성:
    #   - chip 베이스 주소 0x3c는 power-on 시 항상 응답 (per-context 무관)
    #   - dual mode 시 0x11(ch0/ch2 slot) + 0x12(ch1/ch3 slot) 추가 응답
    # → **0x11/0x12 응답 유무**로 모드 판별 (0x3c는 무시):
    #   - 2-ch 버스: "11,12" 둘 다 응답
    #   - 0 or 1-ch 버스: 11/12 응답 없음 (single은 0x3c만, no-ch는 AP1302 powered but not used)
    scan_cmd = (
        "i2cdetect -y {bus} 2>/dev/null | "
        "awk 'NR>1 {{for(i=2;i<=NF;i++) "
        "if ($i==\"11\" || $i==\"12\") print $i}}' | "
        "sort -u | tr '\\n' ',' | sed 's/,$//'"
    )
    mode_checks = []
    for bus in (1, 2):
        count = bus_counts[bus]
        expected_pattern = "11,12" if count == 2 else ""
        _, out, _ = ssh(scan_cmd.format(bus=bus))
        actual = out.strip()
        mode_ok = (actual == expected_pattern)
        mode_checks.append({
            "bus": bus, "expected_count": count, "expected_pattern": expected_pattern,
            "actual_pattern": actual, "pass": mode_ok,
        })
        mark = "✅" if mode_ok else "❌"
        mode_label = "dual(11,12)" if count == 2 else ("single/noch(no 11 no 12)" if count <= 1 else "")
        print(f"  {mark} i2c-{bus} 모드({count}ch→{mode_label}): expected='{expected_pattern}' actual='{actual}'")
    if not all(m["pass"] for m in mode_checks):
        return {
            "test_id": test["id"], "name": test["name"],
            "bus_counts": bus_counts, "mode_checks": mode_checks,
            "error": "i2c 모드 진입 실패 — 기대 모드와 실제 동작 모드 불일치",
        }

    # 검증
    results = []
    for ch, combo_name in test["enabled"].items():
        info = CH_INFO[ch]
        combo = COMBO[combo_name]
        bus = info["bus"]
        count = bus_counts[bus]
        addr = addr_for(ch, count)
        mode = "dual" if count == 2 else "single"

        rot = read_reg(bus, addr, "0x10", "0x0c")
        ae = read_reg(bus, addr, "0x50", "0x02")
        awb = read_reg(bus, addr, "0x51", "0x00")

        rot_pass = rot == combo["expect_rot"]
        ae_pass = ae == combo["expect_ae"]
        awb_pass = awb == combo["expect_awb"]
        all_pass = rot_pass and ae_pass and awb_pass

        results.append({
            "ch": ch, "combo": combo_name, "mode": mode, "bus": bus, "addr": addr,
            "rot": {"expect": combo["expect_rot"], "actual": rot, "pass": rot_pass},
            "ae": {"expect": combo["expect_ae"], "actual": ae, "pass": ae_pass},
            "awb": {"expect": combo["expect_awb"], "actual": awb, "pass": awb_pass},
            "all_pass": all_pass,
        })

        mark = "✅" if all_pass else "❌"
        print(f"  {mark} ch{ch} [{combo_name}] mode={mode} bus={bus} addr={addr}")
        for key in ("rot", "ae", "awb"):
            r = results[-1][key]
            m = "✓" if r["pass"] else "✗"
            print(f"     {m} {key.upper():3s} expect={r['expect']} actual={r['actual']}")

    test_pass = all(r["all_pass"] for r in results)
    return {
        "test_id": test["id"], "name": test["name"],
        "bus_counts": bus_counts,
        "results": results, "pass": test_pass,
    }


def main() -> int:
    # gstApp 동작 확인
    rc, out, _ = ssh("pgrep -af gstApp")
    if rc != 0:
        print(f"⚠️ gstApp 미실행: {out}")
    else:
        print(f"gstApp: {out.strip()}")

    all_results = []
    for test in TESTS:
        try:
            res = run_test(test)
        except Exception as e:
            res = {"test_id": test["id"], "name": test["name"], "error": str(e)}
        all_results.append(res)

    # 요약
    print(f"\n\n{'='*70}\nSUMMARY\n{'='*70}")
    for r in all_results:
        if "error" in r:
            print(f"❌ Test {r['test_id']}: ERROR — {r['error']}")
        else:
            mark = "✅" if r["pass"] else "❌"
            sub = sum(1 for x in r["results"] if x["all_pass"])
            total = len(r["results"])
            print(f"{mark} Test {r['test_id']}: {sub}/{total} ch passed — {r['name']}")

    # 저장
    out_path = Path(__file__).parent / "mixed_combo_results.json"
    out_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n결과 저장: {out_path}")

    return 0 if all(r.get("pass") for r in all_results) else 1


if __name__ == "__main__":
    sys.exit(main())
