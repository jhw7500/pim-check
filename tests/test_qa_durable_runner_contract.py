"""Offline-only durable QA controller contract tests. No board access."""
from dataclasses import replace
import pytest
from scripts.qa_durable_runner_contract import RunnerEvidence, assess_runner


def fully_attested():
    return RunnerEvidence(
        owner_service_registered=True, journal_fsync_verified=True,
        recovery_on_controller_loss_verified=True,
        board_lease_fencing_verified=True, source_pin_verified=True,
        recorder_closed_verified=True, restore_idempotence_verified=True,
        final_state_verified=True, cleanup_budget_seconds=1860,
        lease_seconds=5400, supervised_max_seconds=5000,
    )


def test_supervised_budget_is_independent_of_chat_host_timeout():
    r=assess_runner(fully_attested())
    assert r["decision"] == "REVIEW_REQUIRED"
    assert r["execution_authorized"] is False
    assert r["required_seconds"] == 4560


@pytest.mark.parametrize("name,reason", [
    ("owner_service_registered","OWNER_SERVICE_UNVERIFIED"),
    ("journal_fsync_verified","DURABLE_JOURNAL_UNVERIFIED"),
    ("recovery_on_controller_loss_verified","OWNER_LOSS_RECOVERY_UNVERIFIED"),
    ("board_lease_fencing_verified","LEASE_FENCING_UNVERIFIED"),
    ("source_pin_verified","SOURCE_PIN_UNVERIFIED"),
    ("recorder_closed_verified","WRITER_CLOSE_UNVERIFIED"),
    ("restore_idempotence_verified","RESTORE_IDEMPOTENCE_UNVERIFIED"),
    ("final_state_verified","FINAL_STATE_UNVERIFIED"),
])
def test_missing_or_false_proof_blocks(name,reason):
    for value in (None,False):
        r=assess_runner(replace(fully_attested(),**{name:value}))
        assert reason in r["reason_codes"]
        assert r["decision"] == "BLOCK"
        assert not r["execution_authorized"]


def test_short_supervisor_budget_fails_closed():
    r=assess_runner(replace(fully_attested(),supervised_max_seconds=1800))
    assert "SUPERVISOR_WINDOW_INSUFFICIENT" in r["reason_codes"]


def test_short_lease_fails_closed():
    r=assess_runner(replace(fully_attested(),lease_seconds=4560))
    assert "LEASE_RESERVE_INSUFFICIENT" in r["reason_codes"]


def test_missing_cleanup_reserve_fails_closed():
    r=assess_runner(replace(fully_attested(),cleanup_budget_seconds=1000))
    assert "CLEANUP_RESERVE_INSUFFICIENT" in r["reason_codes"]


@pytest.mark.parametrize("name,value",[
    ("lease_seconds",True),("supervised_max_seconds",-1),
    ("owner_service_registered",1),("cleanup_budget_seconds",0),
])
def test_bad_evidence_rejected(name,value):
    with pytest.raises(ValueError):
        assess_runner(replace(fully_attested(),**{name:value}))


def test_cannot_make_board_actions_from_contract_module():
    import scripts.qa_durable_runner_contract as contract
    assert not hasattr(contract,"start_runner")
    assert not hasattr(contract,"run_board")
    assert not hasattr(contract,"delete_recordings")
