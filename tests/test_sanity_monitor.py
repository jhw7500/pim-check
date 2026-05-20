"""tests/test_sanity_monitor.py - point-in-time sanity case 의 monitor=0 보장.

config_integrity / board_hw_check 는 설정·HW 상태를 한 번 읽는 점검형 case 라
base.yaml 의 300초 모니터가 불필요하다. duration_sec:0 override 가 유지되는지
회귀 방지로 잠근다 (실수로 빠지면 smoke 가 case 당 5분씩 다시 느려진다).
"""
from __future__ import annotations

import os

from config import load_profile

PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "profiles")


def _duration(case: str) -> int:
    profile = load_profile(PROFILES_DIR, case=case)
    return (profile.get("monitor") or {}).get("duration_sec")


def test_config_integrity_is_snapshot():
    assert _duration("config_integrity") == 0


def test_board_hw_check_is_snapshot():
    assert _duration("board_hw_check") == 0


def test_camera_case_still_monitors():
    # 카메라 case 는 지속 관측이 필요하므로 0 으로 바뀌면 안 된다 (대조군).
    assert _duration("720p_2ch") and _duration("720p_2ch") > 0
