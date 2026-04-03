"""
learner.py - 타겟 현재 상태를 수집하여 YAML 케이스 템플릿 생성
"""
from __future__ import annotations

import json
from datetime import datetime


def learn_baseline(ssh, name: str | None = None) -> str:
    """타겟의 현재 상태를 수집하여 YAML 케이스 문자열을 생성한다.

    Args:
        ssh: SshClient 인스턴스
        name: 케이스 이름 (None이면 자동 생성)

    Returns:
        YAML 케이스 문자열
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not name:
        name = f"learned_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    lines = [
        f"# Auto-generated baseline: {now}",
        f"# Target: {ssh.host}",
        f'name: "{name}"',
        f'description: "Learned baseline from target {ssh.host} at {now}"',
        "",
    ]

    # === edgeconf 카메라 설정 ===
    cam_width = ssh.run("jq '.VHL_CAM.cam_width' /root/shared_v/edgeconf_pim.json")
    cam_height = ssh.run("jq '.VHL_CAM.cam_height' /root/shared_v/edgeconf_pim.json")
    fps = ssh.run("jq '.VHL_CAM.fps' /root/shared_v/edgeconf_pim.json")
    rec_time = ssh.run("jq '.VHL_CAM.recording_time' /root/shared_v/edgeconf_pim.json")

    ch0_en = ssh.run("jq '.VHL_CAM.i2c2.ch0.enable' /root/shared_v/edgeconf_pim.json")
    ch1_en = ssh.run("jq '.VHL_CAM.i2c2.ch1.enable' /root/shared_v/edgeconf_pim.json")
    ch2_en = ssh.run("jq '.VHL_CAM.i2c1.ch2.enable' /root/shared_v/edgeconf_pim.json")
    ch3_en = ssh.run("jq '.VHL_CAM.i2c1.ch3.enable' /root/shared_v/edgeconf_pim.json")

    enabled_channels = sum(1 for v in [ch0_en, ch1_en, ch2_en, ch3_en] if v == "true")

    # === 프로세스 CPU ===
    gst_cpu = ssh.run("ps -C gstApp -o %cpu= 2>/dev/null | head -1 | tr -d ' '")
    try:
        gst_cpu_val = float(gst_cpu) if gst_cpu else 0
        gst_min = max(0, int(gst_cpu_val * 0.5))
        gst_max = min(100, int(gst_cpu_val * 1.5) + 10)
    except ValueError:
        gst_min, gst_max = 0, 100

    # === 온도 ===
    temp_raw = ssh.run("cat /sys/devices/virtual/thermal/thermal_zone0/temp")
    try:
        temp_c = int(temp_raw) / 1000 if temp_raw else 0
        warn_temp = min(85, int(temp_c) + 5)
        max_temp = min(90, int(temp_c) + 10)
    except ValueError:
        warn_temp, max_temp = 80, 85

    # === 프로세스 목록 ===
    running = []
    for proc in ["gstApp", "BG_Check_for_pim", "chk_cam_operate", "ord", "vcm"]:
        pid = ssh.run(f"pgrep -x {proc}")
        if pid:
            running.append(proc)

    required = [p for p in running if p in ["gstApp", "chk_cam_operate"]]
    optional = [p for p in running if p in ["ord", "vcm"]]

    # === cam_state ===
    cam_state = ssh.run("cat /tmp/cam_state/state 2>/dev/null") or "unknown"
    cam_streak = ssh.run("cat /tmp/cam_state/streak 2>/dev/null") or "0"

    # === 네트워크 ===
    eth0_ip = ssh.run("ip -br addr show eth0 2>/dev/null | awk '{print $3}' | cut -d/ -f1")
    wifi_ip = ssh.run("ip -br addr show wlp1s0 2>/dev/null | awk '{print $3}' | cut -d/ -f1")

    # === YAML 생성 ===
    lines.append("checks:")
    lines.append("  processes:")
    lines.append("    required:")
    for p in required:
        lines.append(f"      - {p}")
    lines.append("    optional:")
    for p in optional:
        lines.append(f"      - {p}")
    lines.append("")

    lines.append("  cpu:")
    lines.append(f"    gst_range: [{gst_min}, {gst_max}]")
    lines.append(f"    bg_check_max_pct: 3.0")
    lines.append("")

    lines.append("  cam_state:")
    lines.append("    dir: /tmp/cam_state")
    lines.append("    valid_states: [healthy, degraded, recovering, failed]")
    lines.append(f"    expected_state: {cam_state.strip()}")
    lines.append(f"    max_streak: {cam_streak.strip()}")
    lines.append("")

    lines.append("  thermal:")
    lines.append(f"    max_temp_c: {max_temp}")
    lines.append(f"    warn_temp_c: {warn_temp}")
    lines.append("")

    lines.append("  recording:")
    if enabled_channels > 0:
        lines.append(f"    expected_channels: {enabled_channels}")
        lines.append(f'    session_progress: "{enabled_channels}/{enabled_channels}"')
    else:
        lines.append("    expected_channels: null")
        lines.append("    session_progress: null")
    lines.append("")

    # === 코멘트: 수집된 원본 값 ===
    lines.append("# --- Learned values ---")
    lines.append(f"# Resolution: {cam_width}x{cam_height}")
    lines.append(f"# FPS: {fps}")
    lines.append(f"# Recording time: {rec_time} min")
    lines.append(f"# Channels enabled: {enabled_channels} (ch0={ch0_en} ch1={ch1_en} ch2={ch2_en} ch3={ch3_en})")
    lines.append(f"# gstApp CPU: {gst_cpu}% → range [{gst_min}, {gst_max}]")
    lines.append(f"# Temperature: {temp_c:.1f}C → warn {warn_temp}C, max {max_temp}C")
    lines.append(f"# Running: {', '.join(running)}")
    lines.append(f"# cam_state: {cam_state.strip()}, streak: {cam_streak.strip()}")
    lines.append(f"# ETH0: {eth0_ip}, WiFi: {wifi_ip}")

    return "\n".join(lines) + "\n"
