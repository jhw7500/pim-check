"""Kernel diagnostics are evidence, never permission to suppress an error.

All observations here are local fixtures or mocked SSH. The four-line sample
is the already-committed board-error fixture, not a new board observation.
"""
from __future__ import annotations

from unittest.mock import MagicMock
import json

import pytest

from checks.custom import CustomCommandCheck, item_results
from reporter import Reporter
from tests.test_cases_board_error_scope import BOOT_ID, BOOT_TIME, ERRORS, _run


def wire(lines=ERRORS, *, count=None, scope="BOOT_FALLBACK", boot_id=BOOT_ID,
         clipped=0, omitted=None):
    count = len(lines) if count is None else count
    if count == 0:
        return "0"
    omitted = count - len(lines) if omitted is None else omitted
    return (f"COUNT={count} SCOPE={scope} START={BOOT_TIME} BOOT_ID={boot_id} "
            f"FIRST={lines[0][:180]} LAST={lines[-1][:180]}\n"
            + "".join("RECORD=" + line + "\n" for line in lines)
            + f"SAMPLES={len(lines)} OMITTED={omitted} CLIPPED={clipped}\n")


def collect(output, *, tagged=True):
    spec = {"name": "kernel", "command": "fixture-only", "expected": "0", "on_fail": "kernel errors"}
    if tagged:
        spec["evidence_format"] = "kernel_log_v1"
    ssh = MagicMock()
    ssh.run.return_value = output
    check = CustomCommandCheck()
    data = check.collect(ssh, {"custom_commands": [spec]})
    return check, data, data["results"][0]


def test_four_lines_retain_raw_time_boot_and_candidate_not_root_cause():
    check, data, item = collect(wire())
    evidence = item["kernel_evidence"]
    assert evidence["status"] == "complete"
    assert evidence["count"] == 4
    assert evidence["boot_id"] == BOOT_ID
    assert evidence["scope"] == "BOOT_FALLBACK"
    assert [r["raw"] for r in evidence["records"]] == ERRORS
    assert evidence["records"][0]["timestamp"] == "2026-10-26 08:53:27.381"
    assert len(evidence["candidate_groups"]) == 1
    assert evidence["candidate_groups"][0]["record_indexes"] == [0, 1, 2, 3]
    assert evidence["grouping_is_causal"] is False
    assert evidence["root_cause"] == "UNKNOWN"
    assert evidence["recovery_status"] == "UNVERIFIED"
    passed, reason = check.validate(data, {})
    assert not passed
    assert "COUNT=4" in reason and "BOOT_ID=" + BOOT_ID in reason
    assert "CANDIDATE_GROUPS=1" in reason and "RECOVERY=UNVERIFIED" in reason


def test_duplicate_line_count_is_not_removed_from_fail_count():
    check, data, item = collect(wire([ERRORS[0], ERRORS[0]]))
    evidence = item["kernel_evidence"]
    assert evidence["count"] == 2
    assert evidence["sample_duplicate_count"] == 1
    assert check.validate(data, {})[0] is False


@pytest.mark.parametrize("replacement", ["[I2C:1]", "ch1"])
def test_different_adapter_or_channel_never_shares_group(replacement):
    other = ERRORS[1].replace("[I2C:2]", replacement) if replacement.startswith("[") else ERRORS[1].replace("ch0", replacement)
    _, _, item = collect(wire([ERRORS[0], other]))
    assert len(item["kernel_evidence"]["candidate_groups"]) == 2


def test_group_window_is_not_unbounded_transitive_chaining():
    lines = [ERRORS[0], ERRORS[0].replace("27.381", "29.181").replace("27.251533", "29.051533"),
             ERRORS[0].replace("27.381", "30.381").replace("27.251533", "30.251533")]
    _, _, item = collect(wire(lines))
    assert len(item["kernel_evidence"]["candidate_groups"]) == 2


def test_unknown_device_records_remain_separate():
    lines = [e.replace("[I2C:2]", "").replace("ch0", "") for e in ERRORS[:2]]
    _, _, item = collect(wire(lines))
    assert len(item["kernel_evidence"]["candidate_groups"]) == 2


def test_partial_samples_never_claim_complete_grouping():
    _, _, item = collect(wire([ERRORS[0]] * 32, count=100))
    e = item["kernel_evidence"]
    assert e["status"] == "partial"
    assert e["count"] == 100 and e["omitted_count"] == 68
    assert e["grouping_complete"] is False
    assert len(e["records"]) == 32


def test_clipped_samples_remain_explicitly_partial():
    _, _, item = collect(wire([ERRORS[0]], clipped=1))
    assert item["kernel_evidence"]["status"] == "partial"
    assert item["kernel_evidence"]["grouping_complete"] is False


@pytest.mark.parametrize("output", [None, "", "NO_SOURCE", "READ_ERROR", "INVALID_START_TIME", "INVALID_BOOT_ID", "BOOT_CHANGED", "COUNT=4 SCOPE=BOOT_FALLBACK"])
def test_unavailable_evidence_fails_without_inventing_count(output):
    check, data, item = collect(output)
    assert item["kernel_evidence"]["status"] == "invalid"
    assert item["kernel_evidence"]["count"] is None
    assert check.validate(data, {})[0] is False


@pytest.mark.parametrize("mutate", [
    lambda text: text.replace("COUNT=4", "COUNT=0"),
    lambda text: text.replace("SAMPLES=4", "SAMPLES=3"),
    lambda text: text.replace("OMITTED=0", "OMITTED=1"),
    lambda text: text.replace(BOOT_ID, "bad-id"),
    lambda text: text.replace("2026-10-26", "2026-02-30"),
    lambda text: text + "RECOVERED=true\n",
])
def test_inconsistent_or_injected_payload_never_passes(mutate):
    check, data, item = collect(mutate(wire()))
    assert item["kernel_evidence"]["status"] == "invalid"
    assert check.validate(data, {})[0] is False


def test_clean_zero_does_not_claim_verified_hardware_or_recovery():
    check, data, item = collect("0")
    assert check.validate(data, {})[0] is True
    e = item["kernel_evidence"]
    assert e["status"] == "no_matches" and e["count"] == 0
    assert e["recovery_status"] == "NOT_ASSESSED"
    assert e["root_cause"] == "UNKNOWN"


def test_plain_custom_commands_keep_their_old_shape():
    _, _, item = collect("0", tagged=False)
    assert "kernel_evidence" not in item
    assert set(item_results({"results": [item], "skipped": False})[0]) == {"name", "expected", "actual", "passed"}


def test_viewer_item_exposes_structured_kernel_evidence():
    _, data, item = collect(wire())
    assert item_results(data)[0]["kernel_evidence"] == item["kernel_evidence"]


def test_json_report_preserves_only_typed_evidence_not_command_data():
    check, data, item = collect(wire())
    data["password"] = "do-not-copy-general-data"
    passed, reason = check.validate(data, {})
    report = Reporter().to_json([{"name": "custom_commands", "passed": passed, "reason": reason, "data": data}], "board_error_detect")
    evidence = report["checks"][0]["evidence"][0]["kernel_log"]
    assert evidence == item["kernel_evidence"]
    text = json.dumps(report)
    assert "fixture-only" not in text
    assert "do-not-copy-general-data" not in text
    assert evidence["records"][0]["raw"] == ERRORS[0]


def test_local_shell_emits_boot_identity_and_all_four_bounded_records():
    r = _run("\n".join(ERRORS) + "\n")
    assert r.returncode == 0
    assert "BOOT_ID=" + BOOT_ID in r.stdout
    assert all("RECORD=" + line in r.stdout for line in ERRORS)
    assert "SAMPLES=4 OMITTED=0 CLIPPED=0" in r.stdout


@pytest.mark.parametrize("boot_id", ["", "garbled", "123"])
def test_missing_or_invalid_boot_identity_cannot_be_clean_pass(boot_id):
    r = _run("2026-10-26 08:53:29 kernel[notice] normal\n", boot_id=boot_id)
    assert r.returncode == 0
    assert r.stdout.strip() == "INVALID_BOOT_ID"


def test_calendar_invalid_start_fails_closed_in_actual_shell():
    r = _run("\n".join(ERRORS) + "\n", uptime="2026-02-30 00:00:00")
    assert r.stdout.strip() == "INVALID_START_TIME"


@pytest.mark.parametrize("token", ["NO_SOURCE", "READ_ERROR", "INVALID_START_TIME", "INVALID_BOOT_ID", "BOOT_CHANGED", "INVALID_LOG_TIME"])
def test_failure_token_remains_actionable_in_json_and_text(token):
    check, data, item = collect(token)
    assert item["kernel_evidence"]["error"] == token
    assert token in check.validate(data, {})[1]


def test_kernel_text_cannot_inject_terminal_escape_into_reason():
    check, data, _ = collect(wire([ERRORS[0] + "\x1b[31m"]))
    reason = check.validate(data, {})[1]
    assert "\x1b" not in reason
    assert "\\u001b" in reason


def test_reporter_does_not_copy_unrelated_or_unversioned_data():
    report = Reporter().to_json([
        {"name": "other", "passed": True, "data": {"results": [{"kernel_evidence": {"password": "private"}}]}},
        {"name": "custom_commands", "passed": False, "data": {"results": [{"kernel_evidence": {"password": "private"}}]}},
        {"name": "other", "passed": True, "data": []},
    ], "fixture")
    assert all("evidence" not in c for c in report["checks"])


def test_report_projection_is_allowlisted_not_generic_dict_copy():
    check, data, item = collect(wire())
    e = item["kernel_evidence"]
    e["password"] = "private-value"
    e["records"][0]["password"] = "private-value"
    e["candidate_groups"][0]["password"] = "private-value"
    report = Reporter().to_json([{"name": "custom_commands", "passed": False, "data": data}], "fixture")
    assert "private-value" not in json.dumps(report)
    assert report["checks"][0]["evidence"][0]["kernel_log"]["count"] == 4


def local_scan_wrapper(tmp_path, wrapper):
    # Only temporary fixture paths are touched; this never contacts a board.
    import os
    import subprocess
    from tests.test_cases_board_error_scope import _spec, LOG_PATH, ANCHOR_PATH, BOOT_ID_PATH
    log, anchor, boot = (tmp_path / x for x in ("kern.log", "anchor", "boot_id"))
    log.write_text("\n".join(ERRORS) + "\n")
    boot.write_text(BOOT_ID + "\n")
    fake = tmp_path / "awk"
    fake.write_text("#!/bin/sh\n" + wrapper.replace("@BOOT@", str(boot)).replace("@LOG@", str(log)) + '\nexec /usr/bin/awk "$@"\n')
    fake.chmod(0o700)
    cmd = (_spec()["command"].replace(LOG_PATH, str(log)).replace(ANCHOR_PATH, str(anchor))
           .replace(BOOT_ID_PATH, str(boot)).replace("uptime -s", "printf '%s\\n' '" + BOOT_TIME + "'"))
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    return subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=8)


def test_actual_scan_failure_cannot_be_hidden_by_valid_zero_stdout(tmp_path):
    r = local_scan_wrapper(tmp_path, 'case "$2" in scope=*) echo 0; exit 2;; esac')
    assert r.returncode == 0 and r.stdout.strip() == "READ_ERROR"


def test_boot_transition_during_scan_cannot_be_clean_pass(tmp_path):
    r = local_scan_wrapper(tmp_path, 'case "$2" in scope=*) echo other-boot > "@BOOT@"; echo 0; exit 0;; esac')
    assert r.returncode == 0 and r.stdout.strip() == "BOOT_CHANGED"


def test_malformed_error_timestamp_fails_closed_in_actual_shell():
    r = _run("not-a-time kernel[err] MCP4018 write fail\n")
    assert r.returncode == 0 and r.stdout.strip() == "INVALID_LOG_TIME"



def test_ssh_none_keeps_existing_infrastructure_failure_classification():
    from engine import _hard_issues
    check, data, _ = collect(None)
    passed, reason = check.validate(data, {})
    assert "(got: None)" in reason
    assert not passed
    assert _hard_issues({"name": "custom_commands", "passed": passed, "reason": reason, "data": data}) == []


def test_unencodable_input_is_invalid_evidence_not_a_crash():
    check, data, item = collect("\ud800")
    assert item["kernel_evidence"]["status"] == "invalid"
    assert not check.validate(data, {})[0]


def test_source_emptied_between_precheck_and_scan_cannot_be_zero(tmp_path):
    r = local_scan_wrapper(tmp_path, 'case "$2" in scope=*) : > "@LOG@";; esac')
    assert r.returncode == 0 and r.stdout.strip() == "NO_SOURCE"
