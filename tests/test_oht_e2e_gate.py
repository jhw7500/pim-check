"""OHT COPY admission is metadata-only and fails closed on unknown inputs.

Fixtures are synthetic except for a sanitized snapshot of one observed board
configuration. No board lease, OHT socket or recording operation is executed.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from oht_e2e_gate import CopyPreflight, assess_copy_preflight, from_observation


@pytest.fixture
def synthetic_ready():
    return CopyPreflight(
        source_contract_verified=True,
        exclusive_lease_verified=True,
        merged_runtime_verified=True,
        machine_id_length=6,
        ord_port=10007,
        vcm_port=10009,
        recording_minutes=1,
        srt_enabled=True,
        target_copy=True,
        event_auto_remove=False,
        disk_used_percent=50,
        disk_limit_percent=90,
        minimum_headroom_percent=5,
        available_kib=8_000_000,
        expected_new_kib=100_000,
        minimum_reserve_kib=1_000_000,
        recovery_journal_files=0,
    )


def test_synthetic_ready_metadata_is_not_blocked(synthetic_ready):
    assert assess_copy_preflight(synthetic_ready) == ()


def test_missing_all_metadata_is_not_approved():
    reasons = assess_copy_preflight(CopyPreflight())
    assert "SOURCE_BINDING_UNVERIFIED" in reasons
    assert "EXCLUSIVE_LEASE_UNVERIFIED" in reasons
    assert "ACTIVE_RUNTIME_UNVERIFIED" in reasons
    assert "DISK_POLICY_INVALID" in reasons
    assert "AUTO_DELETE_NOT_DISABLED" in reasons
    assert "SPACE_BUDGET_UNVERIFIED" in reasons
    assert "RECOVERY_JOURNAL_NOT_CLEAN" in reasons


def test_observed_board_near_disk_management_limit_blocks_copy():
    """2026-10-29 read-only observation, not an OHT packet capture."""
    snapshot = CopyPreflight(
        source_contract_verified=False,  # no installed binary/source proof
        exclusive_lease_verified=False,  # lease was released at inspection end
        merged_runtime_verified=False,   # only source JSON and runtime hash read
        machine_id_length=None,  # jq counted six characters, not raw bytes
        ord_port=10007,
        vcm_port=10009,
        recording_minutes=1,
        srt_enabled=True,
        target_copy=True,
        event_auto_remove=True,
        disk_used_percent=90,
        disk_limit_percent=90,
        available_kib=25_185_692,
        recovery_journal_files=0,
    )
    blockers = assess_copy_preflight(snapshot)
    assert "DISK_LIMIT_REACHED" in blockers
    assert "AUTO_DELETE_NOT_DISABLED" in blockers
    assert "SOURCE_BINDING_UNVERIFIED" in blockers
    assert "MACHINE_ID_INVALID" in blockers
    assert "SPACE_BUDGET_UNVERIFIED" in blockers
    assert "RECOVERY_JOURNAL_NOT_CLEAN" not in blockers


@pytest.mark.parametrize("field,bad,code", [
    ("source_contract_verified", False, "SOURCE_BINDING_UNVERIFIED"),
    ("source_contract_verified", 1, "SOURCE_BINDING_UNVERIFIED"),
    ("exclusive_lease_verified", None, "EXCLUSIVE_LEASE_UNVERIFIED"),
    ("merged_runtime_verified", "true", "ACTIVE_RUNTIME_UNVERIFIED"),
    ("machine_id_length", 5, "MACHINE_ID_INVALID"),
    ("machine_id_length", True, "MACHINE_ID_INVALID"),
    ("ord_port", 0, "ORD_PORT_INVALID"),
    ("ord_port", 65536, "ORD_PORT_INVALID"),
    ("ord_port", True, "ORD_PORT_INVALID"),
    ("vcm_port", "10009", "VCM_PORT_INVALID"),
    ("recording_minutes", 0, "RECORDING_DURATION_UNVERIFIED"),
    ("srt_enabled", False, "SRT_NOT_ENABLED"),
    ("target_copy", False, "COPY_NOT_ENABLED"),
    ("event_auto_remove", True, "AUTO_DELETE_NOT_DISABLED"),
    ("event_auto_remove", 0, "AUTO_DELETE_NOT_DISABLED"),
    ("disk_used_percent", True, "DISK_POLICY_INVALID"),
    ("disk_used_percent", -1, "DISK_POLICY_INVALID"),
    ("disk_limit_percent", 101, "DISK_POLICY_INVALID"),
    ("minimum_headroom_percent", None, "HEADROOM_POLICY_UNVERIFIED"),
    ("minimum_headroom_percent", 0, "HEADROOM_POLICY_UNVERIFIED"),
    ("minimum_headroom_percent", True, "HEADROOM_POLICY_UNVERIFIED"),
    ("disk_used_percent", 85, "DISK_HEADROOM_INSUFFICIENT"),
    ("disk_used_percent", 89, "DISK_HEADROOM_INSUFFICIENT"),
    ("disk_used_percent", 90, "DISK_LIMIT_REACHED"),
    ("disk_used_percent", 91, "DISK_LIMIT_REACHED"),
    ("expected_new_kib", None, "SPACE_BUDGET_UNVERIFIED"),
    ("expected_new_kib", -1, "SPACE_BUDGET_UNVERIFIED"),
    ("expected_new_kib", 0, "SPACE_BUDGET_UNVERIFIED"),
    ("minimum_reserve_kib", 0, "SPACE_BUDGET_UNVERIFIED"),
    ("available_kib", 100_000, "SPACE_BUDGET_EXHAUSTED"),
    ("available_kib", 1_100_000, "SPACE_BUDGET_EXHAUSTED"),
    ("minimum_reserve_kib", True, "SPACE_BUDGET_UNVERIFIED"),
    ("recovery_journal_files", 1, "RECOVERY_JOURNAL_NOT_CLEAN"),
    ("recovery_journal_files", True, "RECOVERY_JOURNAL_NOT_CLEAN"),
])
def test_single_fault_remains_blocking(synthetic_ready, field, bad, code):
    assert code in assess_copy_preflight(replace(synthetic_ready, **{field: bad}))


def test_no_implicit_file_deletion_authorization(synthetic_ready):
    assert "AUTO_DELETE_NOT_DISABLED" in assess_copy_preflight(
        replace(synthetic_ready, event_auto_remove=True)
    )


def test_from_observation_rejects_unknown_keys_and_missing_fields_fail_closed():
    with pytest.raises(ValueError, match="unknown copy preflight keys"):
        from_observation({"source_contract_verified": True, "packet_sent": True})
    assert not assess_copy_preflight(from_observation({})) == ()


def test_source_and_lease_flags_are_not_a_proof_of_final_files(synthetic_ready):
    """A clear preflight result is not the actual transaction verdict."""
    assert assess_copy_preflight(synthetic_ready) == ()
    assert not hasattr(synthetic_ready, "output_file")
    assert not hasattr(synthetic_ready, "copy_ack")
