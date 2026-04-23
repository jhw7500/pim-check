#!/usr/bin/env python3
"""bps(bitrate) 전용 빠른 검증 — killcam 방식 (no reboot).

4개 bps 값(1024/2048/4096/8192 kbps)을 ch0에서 순차 테스트.
- jq로 edgeconf의 .VHL_CAM.i2c2.ch0.bps = [v, v] 설정
- killcam으로 gstApp 재시작 (~10s)
- recording_time만큼 녹화 대기 + 버퍼
- /dev/shm/recordings/*-ch0.mp4 ffprobe로 bit_rate 측정
- 설정값 × 1000 과 ±10% tolerance로 비교

bps 배열 [high, low]: 첫번째(recording) bps만 검증, 두번째(RTSP)는 미지원이라 동일값으로 저장.
"""
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
RESULT = BASE / "bps_quick_results.json"
TARGET = "192.168.0.5"

TEST_CHANNEL = 0  # i2c-2 bus, 편의상 ch0만 사용
TEST_BPS_VALUES = [1024, 2048, 4096, 8192]
RECORDING_WAIT = 75   # recording_time(1min) + buffer
TOLERANCE_PCT = 10    # ±10%


def ssh(cmd, timeout=20):
    r = subprocess.run(
        ["sshpass", "-p", "root", "ssh", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=5", f"root@{TARGET}", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def apply_bps(channel, bps):
    """edgeconf의 해당 채널 bps를 [bps, bps]로 변경."""
    path = f".VHL_CAM.i2c2.ch{channel}.bps" if channel in (0, 1) else f".VHL_CAM.i2c1.ch{channel}.bps"
    # ch0 enable도 보장 + recording_time=1 확정
    cmd = (
        f"jq '{path} = [{bps}, {bps}] | "
        f".VHL_CAM.i2c2.ch0.enable = true | "
        f".VHL_CAM.recording_time = 1' "
        f"/root/shared_v/edgeconf_pim.json > /tmp/_e.json && "
        f"mv /tmp/_e.json /root/shared_v/edgeconf_pim.json && echo OK"
    )
    rc, out, err = ssh(cmd, timeout=15)
    return rc == 0 and "OK" in out, err


def restart_cam():
    """killcam 실행 (gstApp 재시작). killcam은 자동 restart 로직 포함."""
    rc, out, err = ssh("killcam", timeout=20)
    return rc == 0, out, err


def find_latest_video(channel):
    """/dev/shm/recordings/에서 해당 채널의 최신 mp4 경로 반환."""
    cmd = (f"find /dev/shm/recordings -name '*-ch{channel}.mp4' "
           f"-printf '%T@ %p\\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-")
    rc, out, _ = ssh(cmd, timeout=10)
    return out if rc == 0 else ""


def probe_bitrate(filepath):
    """ffprobe로 video stream bit_rate (bps) 추출."""
    cmd = (f"ffprobe -v error -select_streams v:0 "
           f"-show_entries stream=bit_rate -of csv=p=0 '{filepath}' 2>/dev/null")
    rc, out, _ = ssh(cmd, timeout=15)
    if rc != 0 or not out:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def wait_for_fresh_video(channel, since_ts, timeout=RECORDING_WAIT):
    """since_ts 이후 생성된 해당 채널 mp4 파일 찾기."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        f = find_latest_video(channel)
        if f:
            # 파일 mtime 확인
            rc, out, _ = ssh(f"stat -c %Y '{f}'", timeout=10)
            if rc == 0 and out.strip().isdigit() and int(out) >= since_ts:
                # 파일 크기도 체크 (녹화 완료 판단)
                rc2, sz, _ = ssh(f"stat -c %s '{f}'", timeout=10)
                if rc2 == 0 and sz.strip().isdigit() and int(sz) > 100000:
                    return f
        time.sleep(5)
    return ""


def run_test(ch, bps):
    t0 = time.monotonic()
    print(f"  [1] edgeconf 변경: ch{ch}.bps = [{bps}, {bps}]", flush=True)
    ok, err = apply_bps(ch, bps)
    if not ok:
        return {"channel": ch, "bps": bps, "result": "APPLY_FAIL", "error": err}

    # 변경 시작 시각 (이 시점 이후 생성된 파일이 valid)
    rc, since_str, _ = ssh("date +%s", timeout=5)
    since_ts = int(since_str) if since_str.strip().isdigit() else int(time.time())

    print("  [2] killcam 실행...", flush=True)
    ok2, out, err2 = restart_cam()
    if not ok2:
        return {"channel": ch, "bps": bps, "result": "KILLCAM_FAIL",
                "error": err2[:150]}
    # 재시작 직후 안정화
    time.sleep(12)

    print(f"  [3] 녹화 완료 대기 (최대 {RECORDING_WAIT}s)...", flush=True)
    video = wait_for_fresh_video(ch, since_ts)
    if not video:
        return {"channel": ch, "bps": bps, "result": "NO_VIDEO",
                "elapsed": round(time.monotonic() - t0, 1)}

    print(f"  [4] ffprobe: {video}", flush=True)
    actual_bps = probe_bitrate(video)
    if actual_bps is None:
        return {"channel": ch, "bps": bps, "result": "PROBE_FAIL",
                "video": video, "elapsed": round(time.monotonic() - t0, 1)}

    expected_bps = bps * 1000  # kbps → bps
    tol = expected_bps * TOLERANCE_PCT // 100
    passed = abs(actual_bps - expected_bps) <= tol
    return {
        "channel": ch, "bps": bps,
        "expected_bps": expected_bps, "actual_bps": actual_bps,
        "actual_kbps": round(actual_bps / 1000, 1),
        "diff_pct": round((actual_bps - expected_bps) / expected_bps * 100, 1),
        "tolerance_pct": TOLERANCE_PCT,
        "video": video,
        "result": "PASS" if passed else "FAIL",
        "elapsed": round(time.monotonic() - t0, 1),
    }


def main():
    print(f"BPS Quick Verify (killcam 방식) — {datetime.now().isoformat()}")
    print(f"Channel: ch{TEST_CHANNEL}, Values: {TEST_BPS_VALUES} kbps, Tolerance: ±{TOLERANCE_PCT}%")
    print()

    results = []
    for i, bps in enumerate(TEST_BPS_VALUES, 1):
        t = datetime.now().strftime("%H:%M:%S")
        print(f"[{i}/{len(TEST_BPS_VALUES)}] {t} Testing bps={bps} kbps...", flush=True)
        r = run_test(TEST_CHANNEL, bps)
        results.append(r)
        mark = "[+]" if r["result"] == "PASS" else "[X]"
        if r["result"] == "PASS":
            print(f"  {mark} PASS ({r['elapsed']}s) "
                  f"actual={r['actual_kbps']}kbps diff={r['diff_pct']:+.1f}%", flush=True)
        else:
            print(f"  {mark} {r['result']} ({r.get('elapsed',0)}s) "
                  f"actual={r.get('actual_kbps','-')}kbps", flush=True)
        RESULT.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    p = sum(1 for r in results if r["result"] == "PASS")
    print()
    print("=" * 60)
    print(f"PASS: {p}/{len(results)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
