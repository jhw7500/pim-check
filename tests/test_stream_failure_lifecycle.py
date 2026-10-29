"""SSE 오류 경계: 모든 시작/복구/저장 실패는 terminal done으로 닫는다.

대상 보드와 네트워크는 호출하지 않는다. 프로파일, SSH, 저장소를 모두 mock한다.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from setup import SetupManager
from stream import StreamRunner


def _events(runner):
    events = []
    while not runner.events.empty():
        events.append(runner.events.get_nowait())
    return events


def _terminal(events):
    done = [event["data"] for event in events if event["event"] == "done"]
    assert len(done) == 1, "SSE consumer must receive exactly one terminal event"
    assert events[-1]["event"] == "done"
    return done[0]


@pytest.mark.parametrize("stage", ["profile", "ssh_constructor", "connect", "preflight"])
def test_pre_mutation_failure_always_closes_stream(stage):
    profile = {"target": {}, "checks": {}}
    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    ssh.preflight_check.return_value = []
    with patch("stream.load_profile", return_value=profile) as loader, \
         patch("stream.SshClient", return_value=ssh) as ssh_cls, \
         patch("stream.SetupManager") as setup_cls, \
         patch("stream.Engine") as engine_cls:
        if stage == "profile":
            loader.side_effect = ValueError("profile parse failed")
        elif stage == "ssh_constructor":
            ssh_cls.side_effect = RuntimeError("ssh initialization failed")
        elif stage == "connect":
            ssh.check_connectivity.side_effect = RuntimeError("ssh probe failed")
        else:
            ssh.preflight_check.side_effect = RuntimeError("preflight failed")
        runner = StreamRunner("case", "mock-board")
        runner._run()
    events = _events(runner)
    assert _terminal(events)["status"] == "ERROR"
    assert any(e["event"] == "error" for e in events)
    setup_cls.assert_not_called()
    engine_cls.assert_not_called()


def test_setup_preview_failure_does_not_run_teardown_or_reset():
    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    ssh.preflight_check.return_value = []
    mgr = SetupManager(ssh)
    mgr.check_current = MagicMock(side_effect=RuntimeError("read before apply failed"))
    mgr.run_setup = MagicMock()
    mgr.run_teardown = MagicMock()
    mgr.reset_and_wait = MagicMock()
    profile = {"target": {}, "checks": {},
               "setup": {"edgeconf_changes": {".VHL_CAM.fps": 30},
                         "reboot_after": True}}
    with patch("stream.load_profile", return_value=profile), \
         patch("stream.SshClient", return_value=ssh), \
         patch("stream.SetupManager", return_value=mgr):
        runner = StreamRunner("case", "mock-board")
        runner._run()
    events = _events(runner)
    assert _terminal(events)["status"] == "ERROR"
    mgr.run_setup.assert_not_called()
    mgr.run_teardown.assert_not_called()
    mgr.reset_and_wait.assert_not_called()
    ssh.run.assert_not_called()


def test_failed_setup_and_failed_recovery_is_explicitly_unverified():
    setup = {"inject_command": "inject"}
    teardown = {"recovery_command": "recover", "recovery_verify_command": "check"}
    profile = {"target": {}, "checks": {}, "setup": setup, "teardown": teardown}
    mgr = MagicMock()
    mgr._setup_mutation_attempted = True
    mgr._recovery_outcome_uncertain = False
    mgr.run_setup.side_effect = RuntimeError("partially injected")
    mgr.run_teardown.side_effect = RuntimeError("recovery postcondition failed")
    with patch("stream.load_profile", return_value=profile), \
         patch("stream.SshClient") as ssh_cls, \
         patch("stream.SetupManager", return_value=mgr):
        ssh_cls.return_value.check_connectivity.return_value = True
        ssh_cls.return_value.preflight_check.return_value = []
        runner = StreamRunner("case", "mock-board")
        runner._run()
    events = _events(runner)
    done = _terminal(events)
    assert done["status"] == "ERROR"
    assert done["recovery_unverified"] is True
    assert any("FAULT_RECOVERY_UNVERIFIED" in e["data"].get("message", "")
               for e in events if e["event"] == "error")
    mgr.run_teardown.assert_called_once_with(setup, teardown)


def test_failed_final_teardown_sets_unverified_on_terminal_event():
    profile = {"target": {}, "checks": {},
               "setup": {"inject_command": "inject"},
               "teardown": {"recovery_command": "recover"}}
    mgr = MagicMock()
    mgr.run_setup.return_value = True
    mgr.run_teardown.side_effect = RuntimeError("still injected")
    with patch("stream.load_profile", return_value=profile), \
         patch("stream.SshClient") as ssh_cls, \
         patch("stream.SetupManager", return_value=mgr), \
         patch("stream.Engine") as engine_cls:
        ssh_cls.return_value.check_connectivity.return_value = True
        ssh_cls.return_value.preflight_check.return_value = []
        engine_cls.return_value.checks = []
        runner = StreamRunner("case", "mock-board")
        runner._run()
    events = _events(runner)
    done = _terminal(events)
    assert done["status"] == "ERROR"
    assert done["recovery_unverified"] is True
    assert any("FAULT_RECOVERY_UNVERIFIED" in e["data"].get("message", "")
               for e in events if e["event"] == "error")


@pytest.mark.parametrize("sink", ["history", "dashboard"])
def test_report_persistence_exception_closes_stream_with_error(sink):
    profile = {"target": {}, "checks": {}}
    with patch("stream.load_profile", return_value=profile), \
         patch("stream.SshClient") as ssh_cls, \
         patch("stream.SetupManager"), \
         patch("stream.Engine") as engine_cls, \
         patch("stream.append_result") as history, \
         patch("stream.save_dashboard") as dashboard:
        ssh_cls.return_value.check_connectivity.return_value = True
        ssh_cls.return_value.preflight_check.return_value = []
        engine_cls.return_value.checks = []
        (history if sink == "history" else dashboard).side_effect = OSError("read-only report volume")
        runner = StreamRunner("case", "mock-board", reports_dir="/tmp/test")
        runner._run()
    events = _events(runner)
    done = _terminal(events)
    assert done["status"] == "ERROR"
    assert "report" in " ".join(e["data"].get("message", "").lower()
                                for e in events if e["event"] == "error")
    history.assert_called_once()
    if sink == "history":
        dashboard.assert_not_called()
    else:
        dashboard.assert_called_once()


def test_report_failure_cannot_hide_teardown_failure():
    profile = {"target": {}, "checks": {}, "setup": {"inject_command": "inject"},
               "teardown": {"recovery_command": "recover"}}
    mgr = MagicMock()
    mgr.run_setup.return_value = True
    mgr.run_teardown.side_effect = RuntimeError("recovery failed")
    with patch("stream.load_profile", return_value=profile), \
         patch("stream.SshClient") as ssh_cls, \
         patch("stream.SetupManager", return_value=mgr), \
         patch("stream.Engine") as engine_cls, \
         patch("stream.append_result", side_effect=OSError("report unavailable")):
        ssh_cls.return_value.check_connectivity.return_value = True
        ssh_cls.return_value.preflight_check.return_value = []
        engine_cls.return_value.checks = []
        runner = StreamRunner("case", "mock-board", reports_dir="/tmp/test")
        runner._run()
    events = _events(runner)
    done = _terminal(events)
    assert done["status"] == "ERROR"
    assert done["recovery_unverified"] is True
    assert any("FAULT_RECOVERY_UNVERIFIED" in e["data"].get("message", "")
               for e in events if e["event"] == "error")
