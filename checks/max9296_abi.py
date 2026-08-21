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
  - health_raw: JSON 파싱 + schema 1 + deserializer OK + enable 된 채널의 link up +
    serializer OK.

카메라 스트리밍 여부와 무관하게 항상 유효하다 — 드라이버는 비카메라 케이스에서도
로드되어 있고, health_raw/prepare 는 idle 에서도 읽힌다 (보드 실측).

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


def _parse_prepare_line(line: str) -> dict[str, str]:
    """'state=IDLE generation=0 ...' 형식을 dict 로. 형식 밖 토큰은 무시."""
    fields: dict[str, str] = {}
    for token in line.strip().split():
        if "=" in token:
            key, _, value = token.partition("=")
            fields[key] = value
    return fields


class Max9296AbiCheck(BaseCheck):
    name = "max9296_abi"

    def collect(self, ssh, config: dict) -> dict:
        cfg = config.get("max9296_abi") or {}
        if not cfg.get("expected_version"):
            return {"skipped": True}

        adapters = cfg.get("adapters") or [1, 2]
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
                    if fields["errno"] != "0":
                        issues.append(f"{prefix}: prepare errno={fields['errno']}")
                    if fields["worker_errno"] != "0":
                        # STREAMON 거부의 durable 진단 — 0 이 아니면 최근 스트림
                        # 기동이 드라이버 워커에서 거부된 것.
                        issues.append(
                            f"{prefix}: prepare worker_errno={fields['worker_errno']}")

            health_raw = node.get("health_raw")
            if not health_raw:
                issues.append(f"{prefix}: health_raw node missing")
                continue
            try:
                health = json.loads(health_raw)
            except (ValueError, TypeError):
                issues.append(f"{prefix}: health_raw is not valid JSON")
                continue
            if health.get("schema") != 1:
                issues.append(
                    f"{prefix}: health_raw schema={health.get('schema')} (expected 1)")
            des = health.get("deserializer") or {}
            if des.get("status") != "OK":
                issues.append(
                    f"{prefix}: deserializer status={des.get('status')}")
            for ch in health.get("channels") or []:
                if not ch.get("enabled"):
                    continue
                ch_id = ch.get("channel")
                link = ch.get("link") or {}
                if link.get("status") != "OK" or not link.get("up"):
                    issues.append(
                        f"{prefix}: ch{ch_id} link status={link.get('status')} "
                        f"up={link.get('up')}")
                ser = ch.get("serializer") or {}
                if ser.get("status") != "OK":
                    issues.append(
                        f"{prefix}: ch{ch_id} serializer status={ser.get('status')}")

        if issues:
            return (False, "; ".join(issues))
        return (True, "OK")
