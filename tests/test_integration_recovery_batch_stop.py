"""Unknown recovery must stop same-target batches, independently of pass rate.

Only SSH/process boundaries are mocked; CLI, web routing and plan teardown run
their actual orchestration. No hardware lease, server or board is used.
"""
from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

import pim_check
import web
from plan import DEFAULT_EXECUTION, DEFAULT_GATE, CaseExecution, Plan, evaluate_gate, execute_plan
from setup import EDGECONF_PATH, SetupManager

RECOVERY_EXIT = 4
RECOVERY_ERROR = {
    "status": "ERROR",
    "message": "FAULT_RECOVERY_UNVERIFIED: mock postcondition",
    "recovery_unverified": True,
}


@pytest.mark.parametrize("first_code,expected_calls", [(RECOVERY_EXIT, 1), (1, 2), (0, 2)])
def test_cli_all_stops_only_for_unknown_recovery(first_code, expected_calls):
    args = pim_check.parse_args(["--all", "--quiet"])
    with patch("pim_check.list_cases", return_value=["fault_A", "fault_B"]), \
         patch("pim_check.run_case", side_effect=[first_code, 0]) as run:
        result = pim_check._main_run(args)
    assert result == first_code
    assert run.call_count == expected_calls


def test_cli_watch_does_not_restart_after_unknown_recovery():
    args = pim_check.parse_args(["--watch", "1", "--quiet"])
    with patch("pim_check.run_case", side_effect=[RECOVERY_EXIT, KeyboardInterrupt]) as run, \
         patch("history.save_dashboard"), patch("time.sleep"):
        result = pim_check._main_run(args)
    assert result == RECOVERY_EXIT
    assert run.call_count == 1


def _request(path):
    handler = MagicMock(spec=web.DashboardHandler)
    handler.path = path
    handler.headers = {}
    response = {}
    handler._respond = lambda code, body, content_type: response.update(
        code=code, body=json.loads(body))
    web.DashboardHandler._route_request(handler)
    assert response["code"] == 200
    return response["body"]


@pytest.mark.parametrize("route", ["selected", "tag"])
@pytest.mark.parametrize("unknown,expected_calls", [(True, 1), (False, 2)])
def test_web_batches_stop_on_unknown_recovery(tmp_path, route, unknown, expected_calls):
    cases = tmp_path / "cases"
    cases.mkdir()
    for name in ("fault_A", "fault_B"):
        (cases / f"{name}.yaml").write_text("tags: [fault]\n")
    first = deepcopy(RECOVERY_ERROR) if unknown else {"status": "FAIL"}
    url = ("/api/run-selected?cases=fault_A,fault_B" if route == "selected"
           else "/api/run-tag?tag=fault")
    with patch.object(web, "PROFILES_DIR", str(tmp_path)), \
         patch("web._list_cases", return_value=["fault_A", "fault_B"]), \
         patch("web._run_test", side_effect=[first, {"status": "PASS"}]) as run:
        result = _request(url)
    assert run.call_count == expected_calls
    assert len(result["results"]) == expected_calls
    assert result["count"] == expected_calls
    if unknown:
        assert result["requested"] == 2
        assert result["stopped"] is True


@pytest.mark.parametrize("mode", ["single", "rotate"])
def test_web_auto_runner_stops_without_another_board_operation(mode):
    attempted = []

    def run(case, host):
        attempted.append(case)
        if len(attempted) > 1:  # Bound the old buggy loop without sleeping.
            web._auto_state["running"] = False
        return deepcopy(RECOVERY_ERROR)

    with patch.dict(web._auto_state, {
        "running": True, "mode": mode, "host": "mock-board", "interval": 0,
        "case": "fault_A", "tag": None,
    }, clear=True), patch("web._list_cases", return_value=["fault_A", "fault_B"]), \
         patch("web._run_test", side_effect=run):
        web._auto_runner()
        assert web._auto_state["running"] is False
        assert attempted == ["fault_A"]
        status = _request("/api/status")
        assert "FAULT_RECOVERY_UNVERIFIED" in status["stop_reason"]


@pytest.mark.parametrize("threshold", [0.0, 0.5])
def test_unknown_recovery_cannot_pass_a_permissive_gate(threshold):
    plan = Plan("recovery", "", 1, {}, dict(DEFAULT_EXECUTION),
                {**DEFAULT_GATE, "threshold_pass_rate": threshold}, [])
    executions = [
        CaseExecution("regression", "healthy", [], True, 0),
        CaseExecution("regression", "fault", [], False, 0,
                      error="FAULT_RECOVERY_UNVERIFIED: port never recovered"),
    ]
    gate = evaluate_gate(plan, executions)
    assert gate.pass_rate == 0.5
    assert gate.verdict == "FAIL"


def test_completed_setup_side_recovery_does_not_block_campaign_restore(tmp_path):
    (tmp_path / "cases").mkdir()
    (tmp_path / "base.yaml").write_text(
        "target:\n  host: mock-board\nmonitor:\n  duration_sec: 0\n")
    (tmp_path / "cases" / "legacy.yaml").write_text(
        "setup:\n  edgeconf_changes:\n    TEST.value: 1\n"
        "  recovery_command: recover-once\n"
        "  recovery_verify_command: verify-recovery\n")
    plan = Plan("legacy", "", 1, {"regression": ["legacy"]},
                dict(DEFAULT_EXECUTION), dict(DEFAULT_GATE), [])
    commands, restored = [], []

    def ssh_factory(*args):
        ssh = MagicMock()
        ssh.check_connectivity.return_value = True

        def remote(command, **kwargs):
            commands.append(command)
            return "OK"
        ssh.run.side_effect = remote
        return ssh

    def manager_factory(ssh):
        mgr = SetupManager(ssh)

        def apply(config, **kwargs):
            mgr._config_snapshots = {EDGECONF_PATH: "original-snapshot"}
            return True
        mgr.run_setup = apply
        mgr._local0_log = lambda message: None
        mgr._restore_conf = lambda path: restored.append(
            (path, mgr.get_snapshot_payload(path)))
        mgr.reset_and_wait = lambda *args, **kwargs: None
        return mgr

    def engine_factory(*args):
        engine = MagicMock()
        engine.run_snapshot.return_value = [
            {"name": "mock", "passed": True, "reason": "OK", "data": {}, "duration_ms": 1}]
        return engine

    result = execute_plan(plan, str(tmp_path), ssh_factory=ssh_factory,
                          setup_factory=manager_factory, engine_factory=engine_factory)
    assert len(result) == 1 and result[0].passed
    assert commands.count("recover-once") == 1
    assert commands.count("verify-recovery") == 1
    assert restored == [(EDGECONF_PATH, "original-snapshot")]
