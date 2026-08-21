"""
checks/max9296_abi.py - max9296 드라이버 버전 + sysfs ABI(prepare/health_raw) 체크

드라이버 2.5(2026-08 pim-package-jhw 배포 조합)부터 max9296 는 어댑터별 i2c
디바이스에 두 sysfs 노드를 노출한다:
  - /sys/bus/i2c/devices/<N>-0048/prepare    — parallel prepare 상태라인(k=v 나열)
      예: state=IDLE generation=0 epoch=2 ... errno=0 worker_errno=0 lease=0 match=0
  - /sys/bus/i2c/devices/<N>-0048/health_raw — 카메라 블록 상태 JSON(schema 1)
      deserializer/채널별 link·serializer·isp 상태 (2026-08-21 보드 실측 포맷)

이 체크는 배포 조합이 기대 드라이버를 싣고 있는지(modinfo version)와 ABI 노드가
건강한지를 단언한다:
  - prepare: 파싱 가능 + errno=0 + worker_errno=0(STREAMON 거부의 durable 진단) +
    state != FAILED. (IDLE/READY/CONSUMED 등은 카메라 on/off 에 따라 달라 단언 안 함)
  - health_raw: JSON 파싱 + schema 1. deserializer OK / link up / serializer OK 는
    enable 된 채널이 있을 때만 단언한다 — 전채널 off(0ch) 구성에서 드라이버가
    deserializer 를 저전력으로 내릴 가능성을 배제 못 해 구조 단언만 남긴다.

카메라 스트리밍 여부와 무관하게 항상 유효하다 — 드라이버는 비카메라 케이스에서도
로드되어 있고, health_raw/prepare 는 idle 에서도 읽힌다 (보드 실측).
gstApp 을 강제 kill 하는 주입 케이스(fault_gstapp_crash, process_restart_smoke)는
kill/respawn 구간의 prepare 과도 상태가 주입 효과이므로 케이스 yaml 에서
`max9296_abi: {expected_version: null}` 로 끈다.

설정 (checks.max9296_abi):
  expected_version: "2.5"   # null/미설정이면 체크 전체 skip (케이스별 비활성화)
  adapters: [1, 2]          # i2c 어댑터 번호 (1 → ch2/ch3, 2 → ch0/ch1)
  i2c_addr: "0048"
"""
from __future__ import annotations

import json
import re

from checks.base_check import BaseCheck

# prepare 상태라인에서 반드시 존재·검증할 필드.
_PREPARE_REQUIRED_KEYS = ("state", "errno", "worker_errno")


def _parse_prepare_line(text: str) -> dict[str, str]:
    """'state=IDLE generation=0 ...' 형식을 dict 로.

    첫 줄만 파싱한다 — 노드는 단일 라인 ABI 라서 여러 줄이 오면 형식 밖이며,
    뒷줄이 앞줄 값을 조용히 덮는 병합을 막는다. 형식 밖 토큰은 무시.
    """
    lines = text.strip().splitlines()
    first = lines[0] if lines else ""
    fields: dict[str, str] = {}
    for token in first.split():
        if "=" in token:
            key, _, value = token.partition("=")
            fields[key] = value
    return fields


def _int_or_none(value: str):
    """'0'/'-5'/'0x0' 등 정수 표기를 int 로. 불능이면 None."""
    try:
        return int(value, 0)
    except (TypeError, ValueError):
        return None


class Max9296AbiCheck(BaseCheck):
    name = "max9296_abi"

    def collect(self, ssh, config: dict) -> dict:
        cfg = config.get("max9296_abi") or {}
        if not cfg.get("expected_version"):
            return {"skipped": True}

        adapters = cfg.get("adapters") or [1, 2]
        if not isinstance(adapters, (list, tuple)):
            adapters = [adapters]
        addr = cfg.get("i2c_addr", "0048")

        # modinfo 는 로드 여부와 무관하게 .ko 메타데이터를 읽는다 (배포 조합 단언).
        version_raw = ssh.run("modinfo max9296 2>/dev/null | grep '^version:'")

        nodes: dict[str, dict] = {}
        for adapter in adapters:
            base = f"/sys/bus/i2c/devices/{adapter}-{addr}"
            nodes[str(adapter)] = {
                "prepare": ssh.run(f"cat {base}/prepare 2>/dev/null"),
                "health_raw": ssh.run(f"cat {base}/health_raw 2>/dev/null"),
            }
        return {"version_raw": version_raw, "nodes": nodes}

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if data.get("skipped"):
            return (True, "Skipped (no expected_version configured)")

        cfg = config.get("max9296_abi") or {}
        expected_version = cfg.get("expected_version")

        issues: list[str] = []

        version_raw = data.get("version_raw") or ""
        m = re.search(r"version:\s*(\S+)", version_raw)
        if not m:
            issues.append("max9296 module metadata not found (modinfo failed)")
        elif expected_version and m.group(1) != str(expected_version):
            issues.append(
                f"driver version '{m.group(1)}' (expected '{expected_version}')")

        for adapter, node in (data.get("nodes") or {}).items():
            prefix = f"adapter {adapter}"

            prepare_raw = node.get("prepare")
            if not prepare_raw:
                issues.append(f"{prefix}: prepare node missing")
            else:
                fields = _parse_prepare_line(prepare_raw)
                missing = [k for k in _PREPARE_REQUIRED_KEYS if k not in fields]
                if missing:
                    issues.append(
                        f"{prefix}: prepare line missing fields {missing}")
                else:
                    if fields["state"] == "FAILED":
                        issues.append(f"{prefix}: prepare state=FAILED")
                    for key in ("errno", "worker_errno"):
                        # 16진(0x..) 표기 변경에도 견디도록 정수로 비교.
                        # worker_errno != 0 은 STREAMON 거부의 durable 진단.
                        value = _int_or_none(fields[key])
                        if value is None:
                            issues.append(
                                f"{prefix}: prepare {key}={fields[key]!r} unparsable")
                        elif value != 0:
                            issues.append(f"{prefix}: prepare {key}={fields[key]}")

            health_raw = node.get("health_raw")
            if not health_raw:
                issues.append(f"{prefix}: health_raw node missing")
                continue
            try:
                health = json.loads(health_raw)
            except (ValueError, TypeError):
                issues.append(f"{prefix}: health_raw is not valid JSON")
                continue
            # JSON 이긴 하나 객체가 아닌 값 — 체크는 예외를 던지지 않는다.
            if not isinstance(health, dict):
                issues.append(f"{prefix}: health_raw is not a JSON object")
                continue
            if health.get("schema") != 1:
                issues.append(
                    f"{prefix}: health_raw schema={health.get('schema')} (expected 1)")

            channels = health.get("channels")
            if not isinstance(channels, list):
                issues.append(f"{prefix}: health_raw channels missing or not a list")
                channels = []
            # 채널 레코드 모양을 명시 검증 — 비객체/enabled 비불리언을 조용히
            # "disabled 취급"하면 링크 단언이 통째로 증발한다 (Codex P2 반영).
            # 정상 0ch 구성은 명시적 enabled: false 로 여전히 통과한다.
            enabled = []
            malformed_ch = 0
            for ch in channels:
                if not isinstance(ch, dict) or not isinstance(
                        ch.get("enabled"), bool):
                    malformed_ch += 1
                    continue
                if ch["enabled"]:
                    enabled.append(ch)
            if malformed_ch:
                issues.append(
                    f"{prefix}: {malformed_ch} malformed channel entries")

            # deserializer 상태는 enable 채널이 있을 때만 단언 (0ch 구성 방어).
            if enabled:
                des = health.get("deserializer")
                if not isinstance(des, dict) or des.get("status") != "OK":
                    status = des.get("status") if isinstance(des, dict) else des
                    issues.append(f"{prefix}: deserializer status={status}")

            for ch in enabled:
                ch_id = ch.get("channel")
                link = ch.get("link")
                if not isinstance(link, dict):
                    link = {}
                if link.get("status") != "OK" or not link.get("up"):
                    issues.append(
                        f"{prefix}: ch{ch_id} link status={link.get('status')} "
                        f"up={link.get('up')}")
                ser = ch.get("serializer")
                if not isinstance(ser, dict):
                    ser = {}
                if ser.get("status") != "OK":
                    issues.append(
                        f"{prefix}: ch{ch_id} serializer status={ser.get('status')}")

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")
