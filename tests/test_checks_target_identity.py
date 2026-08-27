from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock

import pytest

from ssh import SshConnectionError


SHA256 = "a" * 64


def _config(claims: list[dict]) -> dict:
    return {"target_identity": claims}


def _ssh_for_module(
    *,
    path: str = "/lib/modules/5.10/max9296.ko",
    resolved_path: Optional[str] = None,
    sha256: str = SHA256,
) -> MagicMock:
    ssh = MagicMock()
    canonical_path = resolved_path if resolved_path is not None else path

    def run(command: str):
        if command == "modinfo -n max9296":
            return path + "\n"
        if command.startswith("readlink -f -- "):
            return canonical_path + "\n"
        if command.startswith("sha256sum -- "):
            return sha256 + "  " + canonical_path + "\n"
        if command == "modinfo max9296":
            return "filename: " + path + "\nversion: 2.5\n"
        return ""

    ssh.run.side_effect = run
    return ssh


def _ssh_for_file(requested: str, resolved: str, sha256: str = SHA256) -> MagicMock:
    ssh = MagicMock()

    def run(command: str):
        if command.startswith("readlink -f -- "):
            return resolved + "\n"
        if command.startswith("sha256sum -- "):
            return sha256 + "  " + resolved + "\n"
        return ""

    ssh.run.side_effect = run
    return ssh


def test_collect_and_validate_module_sha256_claim() -> None:
    """Hashing the resolved max9296 module must produce an exact identity claim."""
    from checks.target_identity import TargetIdentityCheck

    check = TargetIdentityCheck()
    data = check.collect(_ssh_for_module(), _config([{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": SHA256,
    }]))

    assert data["claims"] == [{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "path": "/lib/modules/5.10/max9296.ko",
        "expected": SHA256,
        "actual": SHA256,
    }]
    assert check.validate(data, _config([{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": SHA256,
    }])) == (True, "OK")


def test_collect_accepts_usr_merged_canonical_module_path() -> None:
    """Dropping /usr after readlink would reject a legitimate usr-merged kernel module."""
    from checks.target_identity import TargetIdentityCheck

    requested = "/lib/modules/5.10/max9296.ko"
    resolved = "/usr/lib/modules/5.10/max9296.ko"
    config = _config([{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": SHA256,
    }])
    check = TargetIdentityCheck()

    data = check.collect(
        _ssh_for_module(path=requested, resolved_path=resolved),
        config,
    )

    assert data == {
        "claims": [{
            "id": "max9296.module_sha256",
            "kind": "module_sha256",
            "module": "max9296",
            "path": resolved,
            "expected": SHA256,
            "actual": SHA256,
        }],
        "errors": [],
    }
    assert check.validate(data, config) == (True, "OK")


def test_collect_and_validate_module_version_claim() -> None:
    """The max9296 module version is a distinct typed identity descriptor."""
    from checks.target_identity import TargetIdentityCheck

    check = TargetIdentityCheck()
    config = _config([{
        "id": "max9296.module_version",
        "kind": "module_version",
        "module": "max9296",
        "version": "2.5",
    }])
    data = check.collect(_ssh_for_module(), config)

    assert data["claims"] == [{
        "id": "max9296.module_version",
        "kind": "module_version",
        "module": "max9296",
        "expected": "2.5",
        "actual": "2.5",
    }]
    assert check.validate(data, config) == (True, "OK")


def test_missing_modinfo_is_an_identity_failure() -> None:
    """A missing module path cannot be treated as a hashable target artifact."""
    from checks.target_identity import TargetIdentityCheck

    check = TargetIdentityCheck()
    ssh = MagicMock()
    ssh.run.return_value = ""
    config = _config([{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": SHA256,
    }])

    passed, reason = check.validate(check.collect(ssh, config), config)

    assert not passed
    assert "modinfo" in reason


@pytest.mark.parametrize("module", ["max9296;rm", "../max9296", "max 9296"])
def test_malformed_module_name_fails_closed(module: str) -> None:
    """Unsafe module names must not be interpolated into target commands."""
    from checks.target_identity import TargetIdentityCheck

    check = TargetIdentityCheck()
    config = _config([{
        "id": "unsafe.module",
        "kind": "module_version",
        "module": module,
        "version": "2.5",
    }])

    passed, reason = check.validate(check.collect(MagicMock(), config), config)

    assert not passed
    assert "unsafe module" in reason


@pytest.mark.parametrize("path", ["/etc/passwd", "/tmp/max9296.ko"])
def test_resolved_path_outside_allowlist_fails_before_hashing(path: str) -> None:
    """A malicious modinfo path must not turn the collector into an arbitrary-file hasher."""
    from checks.target_identity import TargetIdentityCheck

    check = TargetIdentityCheck()
    config = _config([{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": SHA256,
    }])

    passed, reason = check.validate(check.collect(_ssh_for_module(path=path), config), config)

    assert not passed
    assert "allowlist" in reason


def test_sha_mismatch_is_reported_as_an_identity_failure() -> None:
    """A collected digest differing from the committed claim must not pass identity validation."""
    from checks.target_identity import TargetIdentityCheck

    check = TargetIdentityCheck()
    config = _config([{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": SHA256,
    }])

    passed, reason = check.validate(check.collect(_ssh_for_module(sha256="b" * 64), config), config)

    assert not passed
    assert "mismatch" in reason


def test_no_identity_claims_fail_closed() -> None:
    """An empty baseline identity inventory cannot authorize a target."""
    from checks.target_identity import TargetIdentityCheck

    check = TargetIdentityCheck()
    passed, reason = check.validate(check.collect(MagicMock(), _config([])), _config([]))

    assert not passed
    assert "no identity claims" in reason


def test_validate_rejects_self_consistent_claim_unbound_to_descriptor() -> None:
    """An attacker cannot replace the descriptor's expected digest with a matching forged pair."""
    from checks.target_identity import TargetIdentityCheck

    config = _config([{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": SHA256,
    }])
    data = {
        "errors": [],
        "claims": [{
            "id": "forged.module_sha256",
            "kind": "module_sha256",
            "module": "other_module",
            "path": "/lib/modules/5.10/other.ko",
            "expected": "b" * 64,
            "actual": "b" * 64,
        }],
    }

    passed, reason = TargetIdentityCheck().validate(data, config)

    assert not passed
    assert "binding" in reason


def test_validate_rejects_duplicate_and_missing_claim_ids() -> None:
    """Claim IDs are a one-to-one binding, not just a length check."""
    from checks.target_identity import TargetIdentityCheck

    config = _config([
        {"id": "one", "kind": "module_version", "module": "max9296", "version": "2.5"},
        {"id": "two", "kind": "module_version", "module": "max96724", "version": "1.0"},
    ])
    data = {
        "errors": [],
        "claims": [
            {"id": "one", "kind": "module_version", "module": "max9296", "expected": "2.5", "actual": "2.5"},
            {"id": "one", "kind": "module_version", "module": "max9296", "expected": "2.5", "actual": "2.5"},
        ],
    }

    passed, reason = TargetIdentityCheck().validate(data, config)

    assert not passed
    assert "duplicate" in reason or "binding" in reason


def test_collect_returns_structured_error_on_ssh_connection_failure() -> None:
    """An SSH transport failure must become identity evidence, not escape the collector."""
    from checks.target_identity import TargetIdentityCheck

    ssh = MagicMock()
    ssh.run.side_effect = SshConnectionError("offline")
    config = _config([{
        "id": "max9296.module_sha256",
        "kind": "module_sha256",
        "module": "max9296",
        "sha256": SHA256,
    }])

    data = TargetIdentityCheck().collect(ssh, config)

    assert data == {"claims": [], "errors": ["SSH_ERROR: offline"]}


def test_collect_and_validate_allowed_file_symlink_with_bound_requested_path() -> None:
    """A configured symlink path stays bound while its resolved hash target remains allowlisted."""
    from checks.target_identity import TargetIdentityCheck

    requested = "/root/shared_v/edgeconf-current.json"
    resolved = "/root/shared_v/releases/edgeconf-2026.json"
    config = _config([{
        "id": "edgeconf.file_sha256",
        "kind": "file_sha256",
        "path": requested,
        "sha256": SHA256,
    }])
    check = TargetIdentityCheck()
    data = check.collect(_ssh_for_file(requested, resolved), config)

    assert data == {
        "claims": [{
            "id": "edgeconf.file_sha256",
            "kind": "file_sha256",
            "requested_path": requested,
            "path": resolved,
            "expected": SHA256,
            "actual": SHA256,
        }],
        "errors": [],
    }
    assert check.validate(data, config) == (True, "OK")


def test_file_symlink_resolving_outside_allowlist_fails_closed() -> None:
    """An allowed requested path cannot smuggle an arbitrary resolved hash target."""
    from checks.target_identity import TargetIdentityCheck

    requested = "/root/shared_v/edgeconf-current.json"
    config = _config([{
        "id": "edgeconf.file_sha256",
        "kind": "file_sha256",
        "path": requested,
        "sha256": SHA256,
    }])

    data = TargetIdentityCheck().collect(_ssh_for_file(requested, "/etc/shadow"), config)

    assert data["claims"] == []
    assert "allowlist" in data["errors"][0]
