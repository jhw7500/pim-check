"""
checks/cam_health.py - gstApp camera-health producer 스냅샷 체크

gstApp(2026-08 camera-health v1 내장 빌드, cacb78a)은 실행 중 1초 주기로
/run/pim-camera/gstApp.json 에 관측 스냅샷을 발행한다 (schema 1, atomic rename).
이 체크는 producer 가 살아 있고(신선도) 관측에 FAIL 블록이 없는지 단언한다.

- 신선도: 스냅샷의 observed_monotonic_ms(CLOCK_MONOTONIC ms)를 /proc/uptime 과
  비교한다. 보드는 suspend 가 없어 두 시계는 같은 기준이다. boot_id 도 대조해
  이전 부팅 잔존 파일을 stale 로 판정한다.
- 상태: status ∈ {OK, N/A, STARTING} 허용, FAIL 만 결함으로 본다.
  (STARTING 은 초기화 중 신호 — aggregator 도 결함으로 안 본다.
   미래 skew 는 mid-read 발행을 fresh 로 보는 producer 계약에 맞춰 허용.)

주의: gstApp 이 의도적으로 죽는 fault 케이스(fault_gstapp_crash/zombie)는
케이스 yaml 에서 `cam_health: {path: null}` 로 이 체크를 꺼야 한다.

설정 (checks.cam_health):
  path: /run/pim-camera/gstApp.json   # null/미설정이면 체크 전체 skip
  stale_ms: 5000                      # 발행 1Hz 대비 여유 (aggregator TTL 3000ms)
"""
from __future__ import annotations

import json

from checks.base_check import BaseCheck

DEFAULT_STALE_MS = 5000


class CamHealthCheck(BaseCheck):
    name = "cam_health"

    def collect(self, ssh, config: dict) -> dict:
        cfg = config.get("cam_health") or {}
        path = cfg.get("path")
        if not path:
            return {"skipped": True}

        raw = ssh.run(f"cat {path} 2>/dev/null")
        uptime_raw = ssh.run("cat /proc/uptime")
        boot_id_raw = ssh.run("cat /proc/sys/kernel/random/boot_id 2>/dev/null")
        return {
            "raw": raw,
            "uptime_raw": uptime_raw,
            "boot_id": boot_id_raw.strip() if boot_id_raw else None,
        }

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if data.get("skipped"):
            return (True, "Skipped (no path configured)")

        cfg = config.get("cam_health") or {}
        path = cfg.get("path")
        stale_ms = cfg.get("stale_ms", DEFAULT_STALE_MS)

        raw = data.get("raw")
        if not raw:
            return (False, f"{path} not found (producer not publishing)")
        try:
            snapshot = json.loads(raw)
        except (ValueError, TypeError):
            return (False, f"{path} is not valid JSON")

        issues: list[str] = []

        if snapshot.get("schema") != 1:
            issues.append(f"schema={snapshot.get('schema')} (expected 1)")

        # 이전 부팅 잔존 파일 감지 — boot_id 불일치는 monotonic 비교보다 앞선다.
        board_boot_id = data.get("boot_id")
        snap_boot_id = snapshot.get("boot_id")
        if board_boot_id and snap_boot_id and board_boot_id != snap_boot_id:
            issues.append("snapshot boot_id mismatch (stale file from previous boot)")

        observed_ms = snapshot.get("observed_monotonic_ms")
        uptime_raw = data.get("uptime_raw") or ""
        try:
            uptime_ms = float(uptime_raw.split()[0]) * 1000.0
        except (ValueError, IndexError):
            uptime_ms = None
        if not isinstance(observed_ms, (int, float)):
            issues.append("observed_monotonic_ms missing")
        elif uptime_ms is not None:
            age_ms = uptime_ms - float(observed_ms)
            # 음수 age(미래 skew)는 mid-read 발행 → fresh 취급 (producer 계약).
            if age_ms > stale_ms:
                issues.append(
                    f"stale: age {age_ms:.0f}ms > {stale_ms}ms (producer stopped?)")

        observations = snapshot.get("observations") or []
        failed = [
            f"{o.get('block')}/{(o.get('scope') or {}).get('id')}:{o.get('code')}"
            for o in observations
            if o.get("status") == "FAIL"
        ]
        if failed:
            issues.append(f"FAIL observations: {', '.join(failed)}")

        if issues:
            return (False, "; ".join(issues))
        return (True, f"OK ({len(observations)} observations)")
