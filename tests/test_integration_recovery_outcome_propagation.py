"""A failed recovery postcondition must not be reported as PASS by entrypoints.

All network, board, telemetry and reporters are mocked.
"""
from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest


PROFILE = {
    "name": "mocked process restart",
    "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
    "monitor": {"duration_sec": 0},
    "checks": {},
    "setup": {"inject_command": "inject mock"},
    "teardown": {"recovery_command": "attempt recovery",
                 "recovery_verify_command": "check state"},
}


@pytest.fixture(autouse=True)
def disable_failure_notifications(monkeypatch):
    monkeypatch.setattr("user_config.load_user_config", lambda: {})


def fake_ssh():
    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    ssh.preflight_check.return_value = []
    return ssh


def fake_engine():
    eng = MagicMock()
    eng.run_snapshot.return_value = [
        {"name": "mock-check", "passed": True, "reason": "OK",
         "data": {}, "duration_ms": 1},
    ]
    return eng


def fake_manager(*, setup_error=None):
    mgr = MagicMock()
    if setup_error:
        mgr.run_setup.side_effect = setup_error
    else:
        mgr.run_setup.return_value = True
    mgr.run_teardown.side_effect = RuntimeError("recovery postcondition FAILED")
    return mgr


@pytest.mark.parametrize("engine_error", [False, True])
def test_web_overrides_candidate_pass_and_persists_fault_outcome(engine_error):
    import web

    mgr = fake_manager()
    engine = fake_engine()
    if engine_error:
        engine.run_snapshot.side_effect = RuntimeError("collection interrupted")
    with patch("web.load_profile", return_value=deepcopy(PROFILE)), \
         patch("web.SshClient", return_value=fake_ssh()), \
         patch("web.SetupManager", return_value=mgr), \
         patch("web.Engine", return_value=engine), \
         patch("web.append_result") as saved, patch("web.save_dashboard"):
        result = web._run_test("process_restart_smoke", "192.168.0.5", duration=0)
    assert result["status"] == "ERROR"
    assert "FAULT_RECOVERY_UNVERIFIED" in result["message"]
    assert result["recovery_unverified"] is True
    assert result["checks"][-1]["name"] == "teardown_recovery"
    saved.assert_called_once()
    rows = saved.call_args.args[0]
    assert rows[-1]["name"] == "teardown_recovery"
    assert rows[-1]["passed"] is False


def test_parallel_overrides_candidate_pass_when_cleanup_fails():
    from parallel import run_on_target

    mgr = fake_manager()
    with patch("parallel.load_profile", return_value=deepcopy(PROFILE)), \
         patch("parallel.SshClient", return_value=fake_ssh()), \
         patch("parallel.SetupManager", return_value=mgr), \
         patch("parallel.Engine", return_value=fake_engine()):
        result = run_on_target("192.168.0.5", "root", "root", "process_restart_smoke", 0)
    assert result["status"] == "ERROR"
    assert "FAULT_RECOVERY_UNVERIFIED" in result["error"]
    assert result["results"][0]["name"] == "teardown_recovery"


@pytest.mark.parametrize("engine_error", [False, True])
def test_cli_does_not_return_zero_when_cleanup_fails(engine_error):
    import pim_check

    mgr = fake_manager()
    results = [{"name": "mock-check", "passed": True, "reason": "OK",
                "data": {}, "duration_ms": 1}]
    with patch("pim_check.load_profile", return_value=deepcopy(PROFILE)), \
         patch("pim_check.SshClient", return_value=fake_ssh()), \
         patch("pim_check.SetupManager", return_value=mgr), \
         patch("pim_check.Engine", return_value=fake_engine()), \
         patch("pim_check.run_verify_with_retry", return_value=(results, 1, 1),
               side_effect=RuntimeError("collection interrupted") if engine_error else None):
        rc = pim_check.run_case("process_restart_smoke", "192.168.0.5",
                                "root", "root", 0, quiet=True)
    assert rc == 4


@pytest.mark.parametrize("entrypoint", ["web", "parallel", "cli"])
def test_partial_injection_setup_exception_attempts_cleanup(entrypoint):
    from ssh import SshConnectionError

    mgr = fake_manager(setup_error=SshConnectionError("lost after injection"))
    if entrypoint == "web":
        import web
        with patch("web.load_profile", return_value=deepcopy(PROFILE)), \
             patch("web.SshClient", return_value=fake_ssh()), \
             patch("web.SetupManager", return_value=mgr), \
             patch("web.Engine", return_value=fake_engine()):
            result = web._run_test("process_restart_smoke", "192.168.0.5", duration=0)
        assert result["status"] == "ERROR"
        assert result["recovery_unverified"] is True
    elif entrypoint == "parallel":
        from parallel import run_on_target
        with patch("parallel.load_profile", return_value=deepcopy(PROFILE)), \
             patch("parallel.SshClient", return_value=fake_ssh()), \
             patch("parallel.SetupManager", return_value=mgr), \
             patch("parallel.Engine", return_value=fake_engine()):
            result = run_on_target("192.168.0.5", "root", "root", "process_restart_smoke", 0)
        assert result["status"] in {"ERROR", "SETUP_FAILED"}
    else:
        import pim_check
        with patch("pim_check.load_profile", return_value=deepcopy(PROFILE)), \
             patch("pim_check.SshClient", return_value=fake_ssh()), \
             patch("pim_check.SetupManager", return_value=mgr):
            assert pim_check.run_case("process_restart_smoke", "192.168.0.5",
                                      "root", "root", 0, quiet=True) == 4
    mgr.run_teardown.assert_called_once()


@pytest.mark.parametrize("sink", ["history", "dashboard"])
@pytest.mark.parametrize("unknown_recovery", [True, False])
def test_report_storage_errors_preserve_terminal_recovery_signal(sink, unknown_recovery):
    """Report I/O cannot make the auto runner repeat an unresolved fault."""
    import web

    mgr = fake_manager()
    if not unknown_recovery:
        mgr.run_teardown.side_effect = None
    failure = OSError("report storage unavailable")
    with patch("web.load_profile", return_value=deepcopy(PROFILE)), \
         patch("web.SshClient", return_value=fake_ssh()), \
         patch("web.SetupManager", return_value=mgr), \
         patch("web.Engine", return_value=fake_engine()), \
         patch("web.append_result", side_effect=failure if sink == "history" else None), \
         patch("web.save_dashboard", side_effect=failure if sink == "dashboard" else None):
        if unknown_recovery:
            result = web._run_test("process_restart_smoke", "mock-board", duration=0)
            assert result["status"] == "ERROR"
            assert result["recovery_unverified"] is True
            assert "report storage unavailable" in result["message"]
        else:
            with pytest.raises(OSError, match="report storage unavailable"):
                web._run_test("process_restart_smoke", "mock-board", duration=0)
    assert web._run_state["active"] is False


def test_cli_preserves_collection_exception_when_recovery_is_known():
    import pim_check

    mgr = fake_manager()
    mgr.run_teardown.side_effect = None
    with patch("pim_check.load_profile", return_value=deepcopy(PROFILE)), \
         patch("pim_check.SshClient", return_value=fake_ssh()), \
         patch("pim_check.SetupManager", return_value=mgr), \
         patch("pim_check.Engine", return_value=fake_engine()), \
         patch("pim_check.run_verify_with_retry", side_effect=RuntimeError("collection interrupted")):
        with pytest.raises(RuntimeError, match="collection interrupted"):
            pim_check.run_case("process_restart_smoke", "mock-board",
                               "root", "root", 0, quiet=True)


@pytest.mark.parametrize("sink", ["history", "json", "webhook"])
@pytest.mark.parametrize("unknown_recovery", [True, False])
def test_cli_report_or_notification_error_preserves_recovery_exit(sink, unknown_recovery):
    import pim_check

    mgr = fake_manager()
    if not unknown_recovery:
        mgr.run_teardown.side_effect = None
    rows = [{"name": "mock-check", "passed": False, "reason": "ordinary failure",
             "data": {}, "duration_ms": 1}]
    sink_path = {"history": "history.append_result",
                 "json": "pim_check.Reporter.save_json",
                 "webhook": "notifier.send_webhook"}[sink]
    with patch("pim_check.load_profile", return_value=deepcopy(PROFILE)), \
         patch("pim_check.SshClient", return_value=fake_ssh()), \
         patch("pim_check.SetupManager", return_value=mgr), \
         patch("pim_check.Engine", return_value=fake_engine()), \
         patch("pim_check.run_verify_with_retry", return_value=(rows, 1, 1)), \
         patch(sink_path, side_effect=OSError("report sink unavailable")):
        kwargs = {"quiet": True, "save_history": sink == "history",
                  "save_json": sink == "json",
                  "webhook_url": "https://example.invalid/test" if sink == "webhook" else None}
        if unknown_recovery:
            assert pim_check.run_case("process_restart_smoke", "mock-board",
                                      "root", "root", 0, **kwargs) == 4
        else:
            with pytest.raises(OSError, match="report sink unavailable"):
                pim_check.run_case("process_restart_smoke", "mock-board",
                                   "root", "root", 0, **kwargs)
