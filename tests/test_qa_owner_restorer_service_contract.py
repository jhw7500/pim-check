"""Review-only systemd candidate must never pretend to be installed."""
from pathlib import Path


def test_proposed_systemd_unit_remains_inactive_template():
    root = Path(__file__).resolve().parents[1]
    candidate = root / "deploy/pim-qa-owner-restorer.service.example"
    unit = candidate.read_text(encoding="utf-8")
    assert not (root / "deploy/pim-qa-owner-restorer.service").exists()
    for item in (
        "ConditionPathExists=/etc/pim-check/qa-owner-restorer-reviewed.json",
        "Type=notify", "WatchdogSec=30s", "Restart=on-failure",
        "StateDirectory=pim-qa-owner", "StateDirectoryMode=0700",
        "UMask=0077", "NoNewPrivileges=true", "ProtectSystem=strict",
        "PrivateNetwork=no", "TimeoutStopSec=1900s",
        "/usr/local/libexec/pim-qa-owner-restorer",
    ):
        assert item in unit
    assert "DESIGN TEMPLATE ONLY" in unit
    assert "with_pim_board.sh" not in unit  # must be inside audited executor


def test_owner_command_approval_docs_do_not_claim_installation():
    root = Path(__file__).resolve().parents[1]
    doc = (root / "docs/pim-owner-approved-4ch-recovery-contract.md").read_text()
    assert "not hardware-approved" in doc
    assert "owner-only" in doc
    assert "prepare_host_command" in doc
    assert "not an executable unit yet" in doc
