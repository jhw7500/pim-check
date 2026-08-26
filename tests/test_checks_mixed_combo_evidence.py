from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ssh import SshConnectionError


FIXTURES = [
    (1, [1, 3], {1: 0, 2: 0}, {
        1: {"rotation": 2, "ae": 665, "awb": 4447},
        3: {"rotation": 1, "ae": 656, "awb": 4432},
    }),
    (2, [0, 2], {1: 0, 2: 0}, {
        0: {"rotation": 3, "ae": 665, "awb": 4432},
        2: {"rotation": 0, "ae": 656, "awb": 4447},
    }),
    (3, [0, 1], {1: 0, 2: 3}, {
        0: {"rotation": 2, "ae": 665, "awb": 4447},
        1: {"rotation": 1, "ae": 656, "awb": 4432},
    }),
    (4, [0, 1, 2, 3], {1: 3, 2: 3}, {
        0: {"rotation": 2, "ae": 665, "awb": 4447},
        1: {"rotation": 1, "ae": 656, "awb": 4432},
        2: {"rotation": 3, "ae": 665, "awb": 4432},
        3: {"rotation": 0, "ae": 656, "awb": 4447},
    }),
]


def _config(test_id: int, channels: list[int], masks: dict[int, int]) -> dict:
    return {"mixed_combo_evidence": {
        "test_id": test_id,
        "enabled_channels": channels,
        "expected_mode_masks": {str(bus): value for bus, value in masks.items()},
    }}


def _scan(mask: int) -> str:
    tokens = []
    if mask & 1:
        tokens.append("11")
    if mask & 2:
        tokens.append("12")
    return "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n10: " + " ".join(tokens) + "\n"


def _word(value: int) -> str:
    return "0x{0:02x} 0x{1:02x}\n".format(value >> 8, value & 0xff)


def _ssh(masks: dict[int, int], expected: dict[int, dict[str, int]]) -> MagicMock:
    ssh = MagicMock()
    channel_by_bus_addr = {}
    for channel in expected:
        bus = 2 if channel in (0, 1) else 1
        mask = masks[bus]
        address = "0x{0:02x}".format(0x11 + (channel % 2)) if mask == 3 else "0x3c"
        channel_by_bus_addr[(bus, address)] = channel

    def run(command: str):
        if command.startswith("i2cdetect -y "):
            return _scan(masks[int(command.split()[2])])
        if command.startswith("i2ctransfer "):
            parts = command.split()
            bus = int(parts[3])
            address = next(part[3:] for part in parts if part.startswith("w2@"))
            channel = channel_by_bus_addr[(bus, address)]
            if "0x10 0x0c" in command:
                return _word(expected[channel]["rotation"])
            if "0x50 0x02" in command:
                return _word(expected[channel]["ae"])
            return _word(expected[channel]["awb"])
        return ""

    ssh.run.side_effect = run
    return ssh


@pytest.mark.parametrize("test_id,channels,masks,expected", FIXTURES)
def test_collects_numeric_mode_masks_and_register_words_for_each_fixture(
    test_id: int, channels: list[int], masks: dict[int, int], expected: dict[int, dict[str, int]],
) -> None:
    """All A/B/C/D scenarios retain numeric I2C evidence rather than summary pass flags."""
    from checks.mixed_combo_evidence import MixedComboEvidenceCheck

    check = MixedComboEvidenceCheck()
    data = check.collect(_ssh(masks, expected), _config(test_id, channels, masks))

    assert data["test_id"] == test_id
    assert data["mode_masks"] == {"1": masks[1], "2": masks[2]}
    assert data["register_words"] == {str(channel): values for channel, values in expected.items()}
    assert data["errors"] == []
    assert check.validate(data, _config(test_id, channels, masks)) == (True, "OK")


@pytest.mark.parametrize("scan,expected", [("", 0), ("11", 1), ("12", 2), ("11 12", 3)])
def test_mode_mask_parser_preserves_all_address_combinations(scan: str, expected: int) -> None:
    """The mode evidence must retain each 0x11/0x12 presence bit independently."""
    from checks.mixed_combo_evidence import parse_mode_mask

    assert parse_mode_mask(scan) == expected


def test_validate_rejects_wrong_address_mode_response() -> None:
    """A half-present dual-address response cannot be used to select an ISP address."""
    from checks.mixed_combo_evidence import MixedComboEvidenceCheck

    config = _config(3, [0, 1], {1: 0, 2: 3})
    data = {
        "test_id": 3,
        "mode_masks": {"1": 0, "2": 1},
        "register_words": {"0": {"rotation": 2, "ae": 665, "awb": 4447},
                           "1": {"rotation": 1, "ae": 656, "awb": 4432}},
        "errors": [],
    }

    passed, reason = MixedComboEvidenceCheck().validate(data, config)

    assert not passed
    assert "mode mask" in reason


@pytest.mark.parametrize("missing", ["rotation", "ae", "awb"])
def test_validate_rejects_missing_required_register_word(missing: str) -> None:
    """Incomplete register evidence must fail rather than silently compare a summary boolean."""
    from checks.mixed_combo_evidence import MixedComboEvidenceCheck

    registers = {"rotation": 2, "ae": 665, "awb": 4447}
    registers.pop(missing)
    data = {
        "test_id": 1,
        "mode_masks": {"1": 0, "2": 0},
        "register_words": {"1": registers, "3": {"rotation": 1, "ae": 656, "awb": 4432}},
        "errors": [],
    }

    passed, reason = MixedComboEvidenceCheck().validate(data, _config(1, [1, 3], {1: 0, 2: 0}))

    assert not passed
    assert missing in reason


@pytest.mark.parametrize("scan", [None, "", "i2cdetect: failed to read adapter", "10: nonsense"])
def test_collect_rejects_invalid_scan_before_register_transfers(scan: object) -> None:
    """An absent or malformed scan is an error, never proof of mode-mask zero."""
    from checks.mixed_combo_evidence import MixedComboEvidenceCheck

    ssh = MagicMock()

    def run(command: str):
        if command.startswith("i2cdetect -y "):
            return scan
        return "0x00 0x02\n"

    ssh.run.side_effect = run
    data = MixedComboEvidenceCheck().collect(ssh, _config(1, [1, 3], {1: 0, 2: 0}))

    assert data["mode_masks"] == {}
    assert data["register_words"] == {}
    assert data["errors"] == ["i2c scan evidence is invalid"]


def test_collect_returns_structured_error_on_ssh_connection_failure() -> None:
    """A scan transport failure must return a normal mixed-combo evidence payload."""
    from checks.mixed_combo_evidence import MixedComboEvidenceCheck

    ssh = MagicMock()
    ssh.run.side_effect = SshConnectionError("offline")

    data = MixedComboEvidenceCheck().collect(ssh, _config(1, [1, 3], {1: 0, 2: 0}))

    assert data == {
        "test_id": 1,
        "mode_masks": {},
        "register_words": {},
        "errors": ["SSH_ERROR: offline"],
    }
