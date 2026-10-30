"""Crash-oriented, local-FS-only durable QA intent journal contract."""
from __future__ import annotations

import json
import stat

import pytest

from scripts.qa_durable_journal import JournalError, JournalStore

SOURCE = "a" * 40
OTHER_SOURCE = "b" * 40
BASELINE = "c" * 64
RUN = "qa-20261030-01"


def store(tmp_path):
    root = tmp_path / "owner-journal"
    root.mkdir()
    return JournalStore(root)


def start(s):
    return s.begin(run_id=RUN, source_sha=SOURCE, baseline_sha256=BASELINE)


def test_begin_persists_an_exclusive_journal_with_no_board_side_effect(tmp_path):
    s = store(tmp_path)
    first = start(s)
    assert first["state"] == "PREPARED"
    assert first["revision"] == 0
    assert first["execution_authorized"] is False
    assert first["target"] == "192.168.214.4"
    payload = json.loads((tmp_path / "owner-journal" / "journal.json").read_text())
    assert payload["run_id"] == RUN
    assert payload["source_sha"] == SOURCE
    assert payload["baseline_sha256"] == BASELINE
    assert stat.S_IMODE((tmp_path / "owner-journal" / "journal.json").stat().st_mode) == 0o600
    assert JournalStore(tmp_path / "owner-journal").read()["state"] == "PREPARED"


def test_restart_with_recorded_mutation_intent_requires_restore(tmp_path):
    s = store(tmp_path)
    start(s)
    s.transition("BASELINE_SAVED", expected_revision=0)
    s.transition("MUTATION_PENDING", expected_revision=1)
    resumed = JournalStore(tmp_path / "owner-journal")
    status = resumed.read(expected_source_sha=SOURCE)
    assert status["revision"] == 2
    assert status["action"] == "RESTORE_REQUIRED"
    assert status["baseline_sha256"] == BASELINE
    assert status["execution_authorized"] is False


def test_restoring_and_final_check_remain_evidence_only(tmp_path):
    s = store(tmp_path)
    start(s)
    s.transition("BASELINE_SAVED", expected_revision=0)
    s.transition("MUTATION_PENDING", expected_revision=1)
    state = s.transition("RESTORE_PENDING", expected_revision=2)
    assert state["action"] == "RESTORE_REQUIRED"
    status = s.transition("FINAL_ATTESTATION_PENDING", expected_revision=3)
    assert status["action"] == "INDEPENDENT_FINAL_ATTESTATION_REQUIRED"
    assert status["execution_authorized"] is False


def test_cannot_skip_persistent_baseline_before_mutation(tmp_path):
    s = store(tmp_path)
    start(s)
    with pytest.raises(JournalError):
        s.transition("MUTATION_PENDING", expected_revision=0)
    assert s.read()["state"] == "PREPARED"


def test_concurrent_stale_revision_cannot_overwrite_new_phase(tmp_path):
    s = store(tmp_path)
    start(s)
    s.transition("BASELINE_SAVED", expected_revision=0)
    with pytest.raises(JournalError, match="revision"):
        s.transition("MUTATION_PENDING", expected_revision=0)
    assert s.read()["state"] == "BASELINE_SAVED"


def test_existing_journal_is_never_replaced_with_new_campaign(tmp_path):
    s = store(tmp_path)
    start(s)
    with pytest.raises(JournalError, match="already"):
        s.begin(run_id="qa-new-02", source_sha=SOURCE, baseline_sha256=BASELINE)
    assert s.read()["run_id"] == RUN


def test_corrupted_journal_is_blocked_not_reset(tmp_path):
    s = store(tmp_path)
    start(s)
    f = tmp_path / "owner-journal" / "journal.json"
    content = json.loads(f.read_text())
    content["state"] = "FINAL_ATTESTATION_PENDING"
    f.write_text(json.dumps(content))
    with pytest.raises(JournalError, match="integrity"):
        s.read()
    with pytest.raises(JournalError):
        start(s)


def test_truncated_json_fails_closed(tmp_path):
    s = store(tmp_path)
    start(s)
    (tmp_path / "owner-journal" / "journal.json").write_text('{"broken":')
    with pytest.raises(JournalError):
        s.read()


def test_source_mismatch_cannot_resume_wrong_build(tmp_path):
    s = store(tmp_path)
    start(s)
    with pytest.raises(JournalError, match="source"):
        s.read(expected_source_sha=OTHER_SOURCE)


def test_symlinked_journal_target_is_rejected_without_following_it(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("don't touch")
    root = tmp_path / "owner-journal"
    root.mkdir()
    (root / "journal.json").symlink_to(outside)
    with pytest.raises(JournalError):
        start(JournalStore(root))
    assert outside.read_text() == "don't touch"


def test_symlinked_journal_directory_is_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(JournalError):
        start(JournalStore(linked))
    assert list(actual.iterdir()) == []


def test_failed_atomic_replace_keeps_prior_valid_journal(tmp_path, monkeypatch):
    import scripts.qa_durable_journal as module

    s = store(tmp_path)
    start(s)
    original = (tmp_path / "owner-journal" / "journal.json").read_bytes()

    def broken_replace(*args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(module.os, "replace", broken_replace)
    with pytest.raises(JournalError):
        s.transition("BASELINE_SAVED", expected_revision=0)
    assert (tmp_path / "owner-journal" / "journal.json").read_bytes() == original
    assert s.read()["revision"] == 0


def test_invalid_run_identity_and_target_cannot_enter_journal(tmp_path):
    s = store(tmp_path)
    with pytest.raises(ValueError):
        s.begin(run_id="../unsafe", source_sha=SOURCE, baseline_sha256=BASELINE)
    with pytest.raises(ValueError):
        s.begin(run_id=RUN, source_sha="wrong", baseline_sha256=BASELINE)
    with pytest.raises(ValueError):
        s.begin(run_id=RUN, source_sha=SOURCE, baseline_sha256=BASELINE,
                target="other-host")
    assert not (tmp_path / "owner-journal" / "journal.json").exists()


def test_missing_journal_does_not_silently_claim_clean_state(tmp_path):
    s = store(tmp_path)
    with pytest.raises(JournalError):
        s.read()


def test_journal_is_non_executable_and_never_reports_release_pass(tmp_path):
    import scripts.qa_durable_journal as module

    assert not hasattr(module, "start_board")
    assert not hasattr(module, "execute_campaign")
    assert not hasattr(module, "delete_recordings")
    s = store(tmp_path)
    start(s)
    for stage, expected in [("BASELINE_SAVED", 0), ("MUTATION_PENDING", 1),
                            ("RESTORE_PENDING", 2), ("FINAL_ATTESTATION_PENDING", 3)]:
        record = s.transition(stage, expected_revision=expected)
        assert record["execution_authorized"] is False
        assert record["decision"] == "BLOCK"



def test_owner_only_permissions_are_required_when_reopening_journal(tmp_path):
    s = store(tmp_path)
    start(s)
    path = tmp_path / "owner-journal" / "journal.json"
    path.chmod(0o644)
    with pytest.raises(JournalError, match="permission"):
        s.read()
    assert path.exists()


def test_fsync_failure_before_atomic_rename_keeps_prior_phase(tmp_path, monkeypatch):
    import os
    import scripts.qa_durable_journal as module

    s = store(tmp_path)
    start(s)
    saved = (tmp_path / "owner-journal" / "journal.json").read_bytes()
    real_fsync = os.fsync

    def fail_regular_file_fsync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("simulated storage barrier failure")
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_regular_file_fsync)
    with pytest.raises(JournalError, match="persistence"):
        s.transition("BASELINE_SAVED", expected_revision=0)
    assert (tmp_path / "owner-journal" / "journal.json").read_bytes() == saved
    assert s.read()["state"] == "PREPARED"
