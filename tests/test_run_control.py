"""tests/test_run_control.py - 웹뷰어 런 제어 순수 로직 검증."""
from __future__ import annotations

import os
import subprocess

import run_control

PLANS = ["smoke", "channel_verify", "comprehensive"]


class TestValidateStartRequest:
    def test_valid(self):
        ok, err, clean = run_control.validate_start_request(
            {"plan": "smoke", "host": "192.168.0.5", "user": "root",
             "password": "root"}, PLANS)
        assert ok and err is None
        assert clean == {"plan": "smoke", "host": "192.168.0.5",
                         "user": "root", "password": "root"}

    def test_strips_whitespace(self):
        ok, _e, clean = run_control.validate_start_request(
            {"plan": " smoke ", "host": " 192.168.0.5 ", "user": " root ",
             "password": "root"}, PLANS)
        assert ok and clean["plan"] == "smoke" and clean["host"] == "192.168.0.5"

    def test_unknown_plan_rejected(self):
        ok, err, _c = run_control.validate_start_request(
            {"plan": "evil", "host": "h", "user": "root", "password": "p"}, PLANS)
        assert not ok and "unknown plan" in err

    def test_missing_plan_rejected(self):
        ok, err, _c = run_control.validate_start_request(
            {"host": "h", "user": "root", "password": "p"}, PLANS)
        assert not ok and "plan is required" in err

    def test_bad_host_rejected(self):
        for bad in ["", "a b", "h;rm -rf", "$(x)", "a/b"]:
            ok, err, _c = run_control.validate_start_request(
                {"plan": "smoke", "host": bad, "user": "root",
                 "password": "p"}, PLANS)
            assert not ok and err == "invalid host"

    def test_bad_user_rejected(self):
        ok, err, _c = run_control.validate_start_request(
            {"plan": "smoke", "host": "h", "user": "ro ot", "password": "p"}, PLANS)
        assert not ok and err == "invalid user"

    def test_empty_password_rejected(self):
        ok, err, _c = run_control.validate_start_request(
            {"plan": "smoke", "host": "h", "user": "root", "password": ""}, PLANS)
        assert not ok and "password is required" in err

    def test_non_dict_rejected(self):
        ok, err, _c = run_control.validate_start_request("nope", PLANS)
        assert not ok and "invalid request body" in err


class TestControlStateFile:
    def test_roundtrip(self, tmp_path):
        d = str(tmp_path)
        assert run_control.read_control(d) is None
        run_control.write_control(d, {"pid": 123, "plan": "smoke", "host": "h"})
        info = run_control.read_control(d)
        assert info["pid"] == 123 and info["plan"] == "smoke"
        run_control.clear_control(d)
        assert run_control.read_control(d) is None

    def test_clear_missing_is_noop(self, tmp_path):
        run_control.clear_control(str(tmp_path))  # no error

    def test_corrupt_file_returns_none(self, tmp_path):
        p = run_control.control_state_path(str(tmp_path))
        with open(p, "w") as f:
            f.write("{not json")
        assert run_control.read_control(str(tmp_path)) is None


class TestPidLifecycle:
    def test_pid_alive_self(self):
        assert run_control.pid_alive(os.getpid()) is True

    def test_pid_alive_invalid(self):
        assert run_control.pid_alive(0) is False
        assert run_control.pid_alive(-1) is False

    def test_pid_alive_dead_process(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        assert run_control.pid_alive(proc.pid) is False

    def test_active_pid_alive(self, tmp_path):
        d = str(tmp_path)
        run_control.write_control(d, {"pid": os.getpid(), "plan": "smoke", "host": "h"})
        assert run_control.active_pid(d) == os.getpid()

    def test_active_pid_dead_clears_file(self, tmp_path):
        d = str(tmp_path)
        proc = subprocess.Popen(["true"])
        proc.wait()
        run_control.write_control(d, {"pid": proc.pid, "plan": "smoke", "host": "h"})
        assert run_control.active_pid(d) is None
        assert run_control.read_control(d) is None   # 죽은 런은 정리됨

    def test_active_pid_no_file(self, tmp_path):
        assert run_control.active_pid(str(tmp_path)) is None
