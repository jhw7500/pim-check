"""
tests/conftest.py — 공통 pytest fixture
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from ssh import SshClient


@pytest.fixture
def mock_ssh():
    """기본 SSH mock — run()은 None 반환, connectivity=True."""
    ssh = MagicMock(spec=SshClient)
    ssh.run.return_value = None
    ssh.check_connectivity.return_value = True
    ssh.preflight_check.return_value = []
    ssh.host = "192.168.0.5"
    ssh.user = "root"
    ssh.password = "root"
    return ssh


@pytest.fixture
def sample_profile():
    """base.yaml 기반 최소 프로파일 dict."""
    return {
        "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
        "monitor": {"duration_sec": 0, "interval_sec": 5},
        "checks": {
            "processes": {"required": ["gstApp"], "optional": []},
            "cpu": {"bg_check_max_pct": 3.0, "gst_range": [0, 100]},
            "thermal": {"max_temp_c": 93, "warn_temp_c": 88},
            "cam_state": {
                "dir": "/tmp/cam_state",
                "expected_state": "healthy",
                "valid_states": ["healthy", "degraded", "recovering", "failed"],
                "max_streak": 0,
            },
            "logs": {"error_patterns": ["kernel panic"]},
        },
    }


@pytest.fixture
def profiles_dir():
    """실제 profiles/ 디렉토리 절대 경로."""
    return os.path.join(os.path.dirname(__file__), "..", "profiles")


@pytest.fixture
def tmp_report_dir(tmp_path):
    """임시 리포트 디렉토리."""
    return str(tmp_path / "reports")


@pytest.fixture
def sample_results():
    """표준 체크 결과 리스트 (2 pass, 1 fail)."""
    return [
        {"name": "process", "passed": True, "reason": "OK", "data": {}, "duration_ms": 50},
        {"name": "thermal", "passed": True, "reason": "OK", "data": {"max_temp": 72.0}, "duration_ms": 30},
        {"name": "cam_state", "passed": False, "reason": "state='failed'", "data": {}, "duration_ms": 20},
    ]
