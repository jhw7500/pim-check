"""
checks/cam_health.py - gstApp camera-health producer 스냅샷 체크

gstApp(2026-08 camera-health v1 내장 빌드, cacb78a)은 실행 중 1초 주기로
/run/pim-camera/gstApp.json 에 관측 스냅샷을 발행한다 (schema 1, atomic rename).
이 체크는 producer 가 살아 있고(신선도) 관측에 FAIL 블록이 없는지 단언한다.

- 신선도: 스냅샷의 observed_monotonic_ms(CLOCK_MONOTONIC ms)를 /proc/uptime 과
  비교한다. 보드는 suspend 가 없어 두 시계는 같은 기준이다. boot_id 도 대조해
  이전 부팅 잔존 파일을 stale 로 판정한다. uptime 을 못 읽으면 신선도 미검증을
  결함으로 표면화한다(조용한 skip 금지).
- 상태: status ∈ {OK, N/A, STARTING} 허용, FAIL 만 결함으로 본다.
  (STARTING 은 초기화 중 신호 — aggregator 도 결함으로 안 본다.
   미래 skew 는 mid-read 발행 tolerance(1s)까지만 fresh — 그 이상 미래면
   이전 부팅 잔존 + boot_id 부재 조합이므로 결함.)
- 부팅 직후: 파일이 아직 없고 uptime < early_boot_grace_sec 이면 hard fail 이
  아니라 stabilization 신호(NEED_PRODUCER_SNAPSHOT_AFTER_BOOT)로 분류해
  retry/pending 기제(verify_retry 단일 출처)에 태운다.

주의: gstApp 을 의도적으로 죽이는 케이스(fault_gstapp_crash,
process_restart_smoke)는 케이스 yaml 에서 `cam_health: {path: null}` 로 이
체크를 꺼야 한다. fault_gstapp_zombie 는 의도적으로 켜 둔다 — 진짜 zombie 면
producer 정지 → stale FAIL 이 참양성이다.

설정 (checks.cam_health):
  path: /run/pim-camera/gstApp.json   # null/미설정이면 체크 전체 skip
  stale_ms: 5000                      # 발행 1Hz 대비 여유 (aggregator TTL 3000ms)
  early_boot_grace_sec: 180           # 부팅 직후 파일 부재를 '준비 중'으로 볼 상한
"""
from __future__ import annotations

import json

from checks.base_check import BaseCheck

DEFAULT_STALE_MS = 5000
DEFAULT_EARLY_BOOT_GRACE_SEC = 180
# mid-read 발행(observed 가 uptime 보다 근소하게 미래)을 fresh 로 볼 허용폭.
FUTURE_SKEW_TOLERANCE_MS = 1000


def _as_float(value, default: float) -> float:
    """설정값 타입 방어 — 문자열 숫자("5000")도 수용, 불능이면 default."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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
        stale_ms = _as_float(cfg.get("stale_ms"), DEFAULT_STALE_MS)
        grace_ms = _as_float(
            cfg.get("early_boot_grace_sec"), DEFAULT_EARLY_BOOT_GRACE_SEC) * 1000.0

        uptime_raw = data.get("uptime_raw") or ""
        try:
            uptime_ms = float(uptime_raw.split()[0]) * 1000.0
        except (ValueError, IndexError):
            uptime_ms = None

        raw = data.get("raw")
        if not raw:
            # 부팅 직후에는 gstApp 기동 ~ 첫 발행(1Hz) 사이 창이 있다 — '준비 중'.
            if uptime_ms is not None and uptime_ms < grace_ms:
                return (
                    False,
                    f"{path} not present yet after boot — "
                    "NEED_PRODUCER_SNAPSHOT_AFTER_BOOT",
                )
            return (False, f"{path} not found (producer not publishing)")
        try:
            snapshot = json.loads(raw)
        except (ValueError, TypeError):
            return (False, f"{path} is not valid JSON")
        # JSON 이긴 하나 객체가 아닌 값([], 숫자 등) — 체크는 예외를 던지지 않는다.
        if not isinstance(snapshot, dict):
            return (False, f"{path} is not a JSON object")

        issues: list[str] = []

        if snapshot.get("schema") != 1:
            issues.append(f"schema={snapshot.get('schema')} (expected 1)")

        # 이전 부팅 잔존 파일 감지 — boot_id 불일치는 monotonic 비교보다 앞선다.
        board_boot_id = data.get("boot_id")
        snap_boot_id = snapshot.get("boot_id")
        if board_boot_id and snap_boot_id and board_boot_id != snap_boot_id:
            issues.append("snapshot boot_id mismatch (stale file from previous boot)")

        observed_ms = snapshot.get("observed_monotonic_ms")
        if not isinstance(observed_ms, (int, float)):
            issues.append("observed_monotonic_ms missing")
        elif uptime_ms is None:
            # 신선도의 기준 시계를 못 읽었으면 검사가 증발했다고 조용히 통과하지
            # 않는다 — 미검증 자체를 표면화.
            issues.append("/proc/uptime unreadable — freshness not verified")
        else:
            age_ms = uptime_ms - float(observed_ms)
            if age_ms > stale_ms:
                issues.append(
                    f"stale: age {age_ms:.0f}ms > {stale_ms:.0f}ms (producer stopped?)")
            elif age_ms < -FUTURE_SKEW_TOLERANCE_MS:
                # 큰 미래값 = 이전 부팅 monotonic 잔존인데 boot_id 로 못 걸러진 것.
                issues.append(
                    f"observed_monotonic_ms {-age_ms:.0f}ms in the future "
                    "(stale file from previous boot?)")

        observations = snapshot.get("observations")
        if not isinstance(observations, list):
            issues.append("observations missing or not a list")
            observations = []
        failed = []
        malformed = 0
        for o in observations:
            if not isinstance(o, dict):
                malformed += 1
                continue
            if o.get("status") == "FAIL":
                scope = o.get("scope")
                scope_id = scope.get("id") if isinstance(scope, dict) else "?"
                failed.append(f"{o.get('block')}/{scope_id}:{o.get('code')}")
        if malformed:
            issues.append(f"{malformed} malformed observation entries")
        if failed:
            issues.append(f"FAIL observations: {', '.join(failed)}")

        if issues:
            return (False, "; ".join(issues))
        return (True, f"OK ({len(observations)} observations)")
