"""Exercise the real acceptance Plan so inherited WARN rules cannot skip its stop."""

from pathlib import Path

import pytest

from plan import evaluate_gate, execute_plan, load_plan


PROFILES = Path(__file__).resolve().parents[1] / "profiles"


class OfflineSsh:
    def check_connectivity(self):
        return True

    def close(self):
        pass

    def run(self, *args, **kwargs):
        raise AssertionError("acceptance policy test must not access a board")


@pytest.mark.parametrize("thermal_passed", [False, True])
def test_acceptance_stops_on_inherited_known_thermal_failure(monkeypatch, thermal_passed):
    """A known thermal failure must stop before process-restart fault injection."""
    plan = load_plan(str(PROFILES / "plans" / "qa_recovery_acceptance.yaml"))
    started = []
    collected = []

    def make_engine(ssh, profile):
        return object()

    def collect(engine, ssh, duration, **kwargs):
        collected.append(started[-1])
        passed = thermal_passed if len(collected) == 1 else True
        return [{
            "name": "thermal",
            "passed": passed,
            "reason": "Temperature 95C exceeds the limit" if not passed else "OK",
            "data": {},
            "duration_ms": 0,
        }], 1, 1

    # Replace only board collection; keep profile inheritance, known-issue
    # matching, execution ordering, stop policy, and final gate real.
    monkeypatch.setattr("verify_retry.run_verify_with_retry", collect)
    executions = execute_plan(
        plan,
        str(PROFILES),
        ssh_factory=lambda *args: OfflineSsh(),
        setup_factory=None,
        engine_factory=make_engine,
        cli_args={"target": {"host": "board.invalid"}},
        on_case_start=lambda idx, total, name, section: started.append(name),
    )

    expected = ["720p_4ch"]
    if thermal_passed:
        expected += ["process_restart_smoke", "verify_ord_vcm_integration"]
    assert started == expected
    assert collected == expected
    assert [execution.case_name for execution in executions] == expected
    assert executions[0].passed is thermal_passed
    assert "known_issue" not in executions[0].results[0]
    assert evaluate_gate(plan, executions).verdict == ("PASS" if thermal_passed else "FAIL")
