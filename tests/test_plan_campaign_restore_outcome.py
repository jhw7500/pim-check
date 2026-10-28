"""Final campaign restoration is part of the Plan verdict and live result.

The real Plan loop and SetupManager teardown run against mocked board I/O.
A post-restore reboot error must survive gate evaluation, reports and JSONL
replay without rewriting already completed case results.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest
import yaml

import engine as engine_mod
import pim_check
import plan as plan_mod
import run_stream
import setup as setup_mod
from setup import EDGECONF_PATH, SetupManager
from viewer_state import ViewerState


@pytest.fixture
def campaign(tmp_path):
    profiles = tmp_path / "profiles"
    cases = profiles / "cases"
    cases.mkdir(parents=True)
    (profiles / "base.yaml").write_text(yaml.safe_dump({
        "target": {"host": "board.invalid"},
        "monitor": {"duration_sec": 0},
    }))
    (cases / "configured.yaml").write_text(yaml.safe_dump({
        "name": "configured",
        "setup": {"edgeconf_changes": {".VHL_CAM.fps": 30},
                  "reboot_after": True},
    }))
    (cases / "readonly.yaml").write_text("name: readonly\n")
    plan = plan_mod.Plan(
        name="restore-contract", description="restore-contract", version=1,
        cases={"regression": ["configured", "readonly"]},
        execution={**plan_mod.DEFAULT_EXECUTION, "case_retry": 0},
        gate={**plan_mod.DEFAULT_GATE, "threshold_pass_rate": 0.0},
        reports=[
            {"format": "json", "path": "result.json"},
            {"format": "junit", "path": "result.xml"},
        ],
    )
    plan_dir = profiles / "plans"
    plan_dir.mkdir()
    (plan_dir / "smoke.yaml").write_text(yaml.safe_dump({
        name: getattr(plan, name)
        for name in ("name", "description", "version", "cases", "execution", "gate", "reports")
    }))
    sessions = []
    reset_calls = []
    failures = {"stage": "reset", "engine": None}

    def ssh_factory(*args):
        ssh = MagicMock()
        ssh.check_connectivity.return_value = True
        ssh.run.return_value = "OK"
        sessions.append(ssh)
        return ssh

    def setup_factory(ssh):
        mgr = SetupManager(ssh)

        def setup(cfg, **kwargs):
            mgr._config_snapshots[EDGECONF_PATH] = "eyJzdGF0ZSI6ImJhc2VsaW5lIn0="
            return True

        def reset(*args, **kwargs):
            reset_calls.append((args, kwargs))
            if failures["stage"] == "reset":
                raise TimeoutError("restore reboot did not return")
            return "reboot"

        mgr.run_setup = setup
        # Keep real run_teardown and snapshot adoption; only physical writes and
        # reset/readiness are replaced at the board boundary.
        mgr.restore_from_snapshot = lambda path: True
        mgr.reset_and_wait = reset
        if failures["stage"] == "adopt":
            def fail_adopt(snapshots):
                raise RuntimeError("cannot adopt campaign baseline")
            mgr.adopt_snapshots = fail_adopt
        return mgr

    def engine_factory(*args):
        engine = MagicMock()
        if failures["engine"] is not None:
            engine.run_snapshot.side_effect = failures["engine"]
        else:
            engine.run_snapshot.return_value = [{
                "name": "probe", "passed": True, "reason": "OK",
                "data": {}, "duration_ms": 1,
            }]
        return engine

    def execute(**kwargs):
        return plan_mod.execute_plan(
            plan, str(profiles), ssh_factory=ssh_factory,
            setup_factory=setup_factory, engine_factory=engine_factory, **kwargs)

    return {
        "plan": plan, "profiles": profiles, "execute": execute,
        "sessions": sessions, "reset_calls": reset_calls, "failures": failures,
        "ssh_factory": ssh_factory, "setup_factory": setup_factory,
        "engine_factory": engine_factory,
    }


@pytest.mark.parametrize("stage", ["adopt", "reset"])
def test_final_restore_failure_overrides_a_permissive_gate(campaign, stage):
    campaign["failures"]["stage"] = stage
    executions = campaign["execute"]()

    assert [(e.case_name, e.passed) for e in executions[:2]] == [
        ("configured", True), ("readonly", True)]
    assert plan_mod.evaluate_gate(campaign["plan"], executions).verdict == "FAIL"
    cleanup = executions[-1]
    assert cleanup.section == "cleanup"
    assert not cleanup.passed
    assert cleanup.error.startswith("FAULT_RECOVERY_UNVERIFIED")
    assert campaign["sessions"][-1].close.called


def test_successful_restore_does_not_add_a_failure_result(campaign):
    campaign["failures"]["stage"] = None
    executions = campaign["execute"]()

    assert len(executions) == 2
    assert all(e.passed for e in executions)
    assert plan_mod.evaluate_gate(campaign["plan"], executions).verdict == "PASS"
    assert len(campaign["reset_calls"]) == 1


def test_collection_error_and_restore_error_are_both_retained(campaign):
    campaign["failures"]["engine"] = RuntimeError("collection failed")
    executions = campaign["execute"]()

    assert all("RUN_EXCEPTION" in e.error for e in executions[:2])
    assert "restore reboot did not return" in executions[-1].error
    assert executions[-1].section == "cleanup"
    assert plan_mod.evaluate_gate(campaign["plan"], executions).verdict == "FAIL"


def test_keyboard_interrupt_is_not_replaced_by_cleanup_failure(campaign, capsys):
    campaign["failures"]["engine"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        campaign["execute"]()

    assert "FAULT_RECOVERY_UNVERIFIED" in capsys.readouterr().out
    assert campaign["sessions"][-1].close.called


def test_restore_failure_is_visible_in_json_and_junit(campaign, tmp_path):
    executions = campaign["execute"]()
    gate = plan_mod.evaluate_gate(campaign["plan"], executions)
    plan_mod.render_reports(campaign["plan"], executions, gate,
                            host="board.invalid", base_path=str(tmp_path))

    report = json.loads((tmp_path / "result.json").read_text())
    assert report["gate"]["verdict"] == "FAIL"
    assert report["executions"][-1]["section"] == "cleanup"
    assert report["executions"][-1]["error"].startswith("FAULT_RECOVERY_UNVERIFIED")
    suite = ET.parse(tmp_path / "result.xml").getroot()
    assert suite.attrib["failures"] == "1"
    assert "restore reboot did not return" in suite.findall("testcase")[-1].find("failure").attrib["message"]


def test_failed_progress_sink_cannot_hide_the_cleanup_result(campaign):
    def broken_progress(*args):
        raise OSError("event disk full")

    executions = campaign["execute"](progress=broken_progress)
    assert plan_mod.evaluate_gate(campaign["plan"], executions).verdict == "FAIL"
    assert executions[-1].section == "cleanup"


def test_cli_exit_and_replayed_viewer_include_final_restore_failure(campaign, tmp_path, monkeypatch):
    events = tmp_path / "events"
    monkeypatch.setattr(run_stream, "default_events_dir", lambda: str(events))
    monkeypatch.setattr(pim_check, "PROFILES_DIR", str(campaign["profiles"]))
    monkeypatch.setattr(plan_mod, "render_reports", lambda *args, **kwargs: [])
    monkeypatch.setattr(pim_check, "SshClient", campaign["ssh_factory"])
    monkeypatch.setattr(setup_mod, "SetupManager", campaign["setup_factory"])
    monkeypatch.setattr(engine_mod, "Engine", campaign["engine_factory"])
    args = argparse.Namespace(plan="smoke", host="board.invalid",
                              user=None, password=None, duration=None,
                              quiet=True, until_pass=False)

    assert pim_check._run_plan(args) == 1
    lines = (events / "current.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    ends = [record for record in records if record["event_type"] == "case_end"]
    assert [record["result"] for record in ends] == ["pass", "pass", "fail"]
    assert records[-1]["event_type"] == "run_end"
    assert records[-1]["fail_count"] == 1
    state = ViewerState.from_lines(lines)
    assert state.progress == (3, 3)
    assert state.pass_count == 2 and state.fail_count == 1
    assert state.case_status["configured"] == "pass"
    assert state.case_status["readonly"] == "pass"
    assert state.case_status["campaign_restore"] == "fail"


@pytest.mark.parametrize("include_start", [False, True])
def test_viewer_accounts_for_cleanup_that_was_not_a_requested_case(include_start):
    records = [
        {"event_type": "run_start", "cases": ["probe", "skipped"], "total_cases": 2},
        {"event_type": "case_end", "case_name": "probe", "result": "fail",
         "completed_cases": 1, "pass_count": 0, "fail_count": 1},
    ]
    if include_start:
        records.append({"event_type": "case_start", "case_name": "campaign_restore"})
    records.append({
        "event_type": "case_end", "case_name": "campaign_restore", "result": "fail",
        "completed_cases": 2, "pass_count": 0, "fail_count": 2,
        "reason": "FAULT_RECOVERY_UNVERIFIED: restore",
    })
    state = ViewerState.from_lines(json.dumps(record) for record in records)

    assert state.progress == (2, 3)
    assert state.case_status["skipped"] == "pending"
    assert state.case_status["campaign_restore"] == "fail"
