"""Offline safety foundation for a future owner-run 45 minute PIM campaign."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from qa_durable_campaign_journal import (  # noqa: E402
    CampaignContract, CampaignJournal, JournalError, inspect_admission,
    assess_execution_budget,
)


def private_dir(tmp_path):
    p = tmp_path / "owner"
    p.mkdir(mode=0o700)
    p.chmod(0o700)
    return p


def contract(run_id="a" * 32, **changes):
    values = {
        "run_id": run_id,
        "source_sha": "b" * 40,
        "baseline_sha256": "c" * 64,
        "board_id": "pim",
        "target": "192.168.214.4",
        "owner_plan_id": "qa_recovery_acceptance",
        "campaign_seconds": 2700,
        "restore_seconds": 1860,
        "lease_seconds": 5400,
    }
    values.update(changes)
    return CampaignContract(**values)


def phase(journal, name, *, after=None):
    opts = {"proof_sha256": "d" * 64}
    if after is not None:
        opts["baseline_after_sha256"] = after
    return journal.advance(name, **opts)


def ready_to_restore(journal):
    phase(journal, "BASELINED")
    phase(journal, "LEASED")
    phase(journal, "RESTORE_ARMED")


def complete(journal):
    ready_to_restore(journal)
    phase(journal, "RUNNING")
    phase(journal, "RESTORING")
    phase(journal, "RESTORE_CHECKED", after="c" * 64)
    phase(journal, "CLOSED")


@pytest.mark.parametrize("kwargs", [
    {"run_id": "../board"},
    {"source_sha": "not-sha"},
    {"baseline_sha256": "a" * 63},
    {"board_id": "other"},
    {"target": "192.168.214.5"},
    {"campaign_seconds": 1800},
    {"restore_seconds": 30},
    {"lease_seconds": 4000},
    {"owner_plan_id": "../../etc/passwd"},
])
def test_invalid_run_contract_is_rejected(kwargs):
    with pytest.raises(ValueError):
        contract(**kwargs)


def test_create_fsynced_private_chain_and_reopen(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        assert journal.phase == "PREPARED"
        assert journal.execution_authorized is False
        path = directory / ("a" * 32 + ".jsonl")
        assert path.is_file() and (path.stat().st_mode & 0o777) == 0o600
        phase(journal, "BASELINED")
        assert journal.phase == "BASELINED"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["phase"] for r in rows] == ["PREPARED", "BASELINED"]
    assert rows[0]["previous_sha256"] == "0" * 64
    assert rows[1]["previous_sha256"] == rows[0]["record_sha256"]
    with CampaignJournal.open(directory, "a" * 32) as reopened:
        assert reopened.phase == "BASELINED"
        assert reopened.requires_restore is False
        assert reopened.execution_authorized is False


def test_private_directory_and_regular_file_required(tmp_path):
    directory = private_dir(tmp_path)
    directory.chmod(0o755)
    with pytest.raises(JournalError):
        CampaignJournal.create(directory, contract())
    directory.chmod(0o700)
    with CampaignJournal.create(directory, contract()):
        pass
    file = directory / ("a" * 32 + ".jsonl")
    file.chmod(0o644)
    with pytest.raises(JournalError):
        CampaignJournal.open(directory, "a" * 32)
    file.chmod(0o600)
    with CampaignJournal.open(directory, "a" * 32):
        pass


def test_durable_restore_marker_survives_crashed_controller(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        ready_to_restore(journal)
        assert journal.phase == "RESTORE_ARMED"
        assert journal.requires_restore
    with CampaignJournal.open(directory, "a" * 32) as recovered:
        assert recovered.requires_restore is True
        assert recovered.phase == "RESTORE_ARMED"
        with pytest.raises(JournalError):
            phase(recovered, "CLOSED")
        phase(recovered, "RESTORING")
        phase(recovered, "RESTORE_CHECKED", after="c" * 64)
        phase(recovered, "CLOSED")
        assert recovered.requires_restore is False


def test_bad_transition_or_missing_proof_cannot_skip_restore(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        with pytest.raises(JournalError):
            phase(journal, "RUNNING")
        with pytest.raises(JournalError):
            journal.advance("BASELINED", proof_sha256="")
        phase(journal, "BASELINED")
        with pytest.raises(JournalError):
            phase(journal, "RESTORE_ARMED")
        assert journal.phase == "BASELINED"


def test_restore_verified_requires_original_baseline_and_proof(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        ready_to_restore(journal)
        phase(journal, "RUNNING")
        phase(journal, "RESTORING")
        with pytest.raises(JournalError):
            phase(journal, "RESTORE_CHECKED", after="e" * 64)
        with pytest.raises(JournalError):
            phase(journal, "CLOSED")
        phase(journal, "RESTORE_CHECKED", after="c" * 64)
        phase(journal, "CLOSED")
        assert journal.execution_authorized is False


def test_fault_unknown_stays_pending_until_explicit_restore(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        ready_to_restore(journal)
        phase(journal, "RUNNING")
        phase(journal, "FAULT_UNKNOWN")
        assert journal.requires_restore is True
        with pytest.raises(JournalError):
            phase(journal, "CLOSED")
    with CampaignJournal.open(directory, "a" * 32) as journal:
        assert journal.phase == "FAULT_UNKNOWN"
        phase(journal, "RESTORING")
        phase(journal, "RESTORE_CHECKED", after="c" * 64)
        phase(journal, "CLOSED")


def test_cannot_run_second_controller_with_locked_journal(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()):
        with pytest.raises(JournalError):
            CampaignJournal.open(directory, "a" * 32)


def test_other_pending_run_blocks_new_campaign_even_if_only_prepared(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()):
        pass
    status = inspect_admission(directory)
    assert status["decision"] == "BLOCK"
    assert "PENDING_JOURNAL" in status["reason_codes"]
    assert status["execution_authorized"] is False
    with pytest.raises(JournalError):
        CampaignJournal.create(directory, contract("b" * 32))


def test_completed_run_allows_review_only_not_execution(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        complete(journal)
    status = inspect_admission(directory)
    assert status["decision"] == "REVIEW_REQUIRED"
    assert status["execution_authorized"] is False
    with CampaignJournal.create(directory, contract("b" * 32)) as new:
        assert new.phase == "PREPARED"


def test_corrupted_hash_or_incomplete_record_blocks_and_is_not_repaired(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()):
        pass
    path = directory / ("a" * 32 + ".jsonl")
    original = path.read_bytes()
    path.write_bytes(original.replace(b"PREPARED", b"RUNNING!"))
    with pytest.raises(JournalError):
        CampaignJournal.open(directory, "a" * 32)
    assert inspect_admission(directory)["decision"] == "BLOCK"
    assert path.read_bytes() != original

    path.write_bytes(original + b'{"incomplete":')
    with pytest.raises(JournalError):
        CampaignJournal.open(directory, "a" * 32)
    assert "CORRUPT_JOURNAL" in inspect_admission(directory)["reason_codes"]


def test_symlink_or_hardlinked_journal_file_rejected(tmp_path):
    directory = private_dir(tmp_path)
    real = tmp_path / "outside"
    real.write_text("outside")
    path = directory / ("a" * 32 + ".jsonl")
    path.symlink_to(real)
    with pytest.raises(JournalError):
        CampaignJournal.open(directory, "a" * 32)
    with pytest.raises(JournalError):
        CampaignJournal.create(directory, contract())
    path.unlink()
    with CampaignJournal.create(directory, contract()):
        pass
    twin = tmp_path / "twin.jsonl"
    os.link(path, twin)
    with pytest.raises(JournalError):
        CampaignJournal.open(directory, "a" * 32)


def test_inspection_fail_closed_on_unexpected_directory_entry(tmp_path):
    directory = private_dir(tmp_path)
    (directory / "unknown.txt").write_text("suspicious")
    result = inspect_admission(directory)
    assert result["decision"] == "BLOCK"
    assert result["execution_authorized"] is False
    assert "UNTRUSTED_DIRECTORY_ENTRY" in result["reason_codes"]


def test_mutation_budget_blocked_by_host_call_limit(tmp_path):
    result = assess_execution_budget(host_limit_seconds=1800,
                                     campaign_seconds=2700,
                                     restore_seconds=1860,
                                     lease_seconds=5400,
                                     durable_owner_service_verified=False)
    assert result["decision"] == "BLOCK"
    assert "HOST_TIMEOUT_CAN_INTERRUPT_RECOVERY" in result["reason_codes"]
    assert "OWNER_SERVICE_UNVERIFIED" in result["reason_codes"]
    assert result["execution_authorized"] is False
    with_owner = assess_execution_budget(host_limit_seconds=1800,
                                          campaign_seconds=2700,
                                          restore_seconds=1860,
                                          lease_seconds=5400,
                                          durable_owner_service_verified=True)
    assert with_owner["decision"] == "REVIEW_REQUIRED"
    assert with_owner["execution_authorized"] is False


def test_appending_one_entry_fsyncs_file_and_folder_creation(tmp_path, monkeypatch):
    directory = private_dir(tmp_path)
    observed = []
    real_fsync = os.fsync

    def tracked(fd):
        observed.append(os.fstat(fd).st_mode)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", tracked)
    with CampaignJournal.create(directory, contract()) as journal:
        phase(journal, "BASELINED")
    assert len(observed) >= 3  # genesis data, containing directory, transition


def test_abrupt_process_exit_preserves_restore_pending(tmp_path):
    import subprocess

    directory = private_dir(tmp_path)
    source = (
        "import os,sys;"
        "sys.path.insert(0,'scripts');"
        "from qa_durable_campaign_journal import CampaignContract,CampaignJournal;"
        "c=CampaignContract(run_id='a'*32,source_sha='b'*40,"
        "baseline_sha256='c'*64,board_id='pim',target='192.168.214.4',"
        "owner_plan_id='qa_recovery_acceptance',campaign_seconds=2700,"
        "restore_seconds=1860,lease_seconds=5400);"
        "j=CampaignJournal.create(sys.argv[1],c);"
        "j.advance('BASELINED',proof_sha256='d'*64);"
        "j.advance('LEASED',proof_sha256='d'*64);"
        "j.advance('RESTORE_ARMED',proof_sha256='d'*64);"
        "os._exit(17)"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", source, str(directory)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15, check=False)
    assert proc.returncode == 17, proc.stderr
    assert inspect_admission(directory)["decision"] == "BLOCK"
    with CampaignJournal.open(directory, "a" * 32) as journal:
        assert journal.phase == "RESTORE_ARMED"
        assert journal.requires_restore is True


def test_signed_but_malformed_phase_type_fails_closed(tmp_path):
    import hashlib

    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        phase(journal, "BASELINED")
    p = directory / ("a" * 32 + ".jsonl")
    events = [json.loads(x) for x in p.read_text().splitlines()]
    event = events[-1]
    event["phase"] = []
    payload = {k: v for k, v in event.items() if k != "record_sha256"}
    event["record_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    p.write_text("".join(
        json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n"
        for x in events), encoding="utf8")
    result = inspect_admission(directory)
    assert result["decision"] == "BLOCK"
    assert "CORRUPT_JOURNAL" in result["reason_codes"]


def test_owner_busy_journal_cannot_be_claimed_done(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()):
        status = inspect_admission(directory)
        assert status["decision"] == "BLOCK"
        assert "ACTIVE_JOURNAL" in status["reason_codes"]


def test_empty_or_untrusted_parent_fails_admission(tmp_path):
    assert inspect_admission(tmp_path / "missing")["decision"] == "BLOCK"
    directory = private_dir(tmp_path)
    link = tmp_path / "shortcut"
    link.symlink_to(directory)
    assert inspect_admission(link)["decision"] == "BLOCK"


def test_journal_never_dereferences_symlinked_target_file(tmp_path):
    directory = private_dir(tmp_path)
    outside = tmp_path / "external"
    outside.write_text("protected")
    (directory / ("a" * 32 + ".jsonl")).symlink_to(outside)
    assert inspect_admission(directory)["decision"] == "BLOCK"
    assert outside.read_text() == "protected"


def test_nonfinite_json_payload_fails_closed_without_uncaught_valueerror(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        phase(journal, "BASELINED")
    path = directory / ("a" * 32 + ".jsonl")
    rows = path.read_text().splitlines()
    # Python JSON can parse NaN by default. It must not escape the safety gate.
    assert '"proof_sha256":"' + "d" * 64 + '"' in rows[1]
    rows[1] = rows[1].replace('"proof_sha256":"' + "d" * 64 + '"',
                              '"proof_sha256":NaN')
    path.write_text("\n".join(rows) + "\n")
    status = inspect_admission(directory)
    assert status["decision"] == "BLOCK"
    assert "CORRUPT_JOURNAL" in status["reason_codes"]


def test_removed_open_journal_refuses_further_phase_updates(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        path = directory / ("a" * 32 + ".jsonl")
        path.unlink()
        with pytest.raises(JournalError):
            phase(journal, "BASELINED")


def test_replaced_open_journal_refuses_writing_to_stale_inode(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        path = directory / ("a" * 32 + ".jsonl")
        old = path.read_bytes()
        path.rename(directory / "old-run")
        path.write_bytes(old)
        path.chmod(0o600)
        with pytest.raises(JournalError):
            phase(journal, "BASELINED")


def test_tampered_open_journal_refuses_new_append(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        path = directory / ("a" * 32 + ".jsonl")
        path.write_bytes(path.read_bytes().replace(b"PREPARED", b"INVALID_"))
        with pytest.raises(JournalError):
            phase(journal, "BASELINED")


def test_changed_permissions_during_open_journal_block_append(tmp_path):
    directory = private_dir(tmp_path)
    with CampaignJournal.create(directory, contract()) as journal:
        path = directory / ("a" * 32 + ".jsonl")
        path.chmod(0o666)
        with pytest.raises(JournalError):
            phase(journal, "BASELINED")
