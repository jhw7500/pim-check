from __future__ import annotations

import re
from typing import Dict, List, Optional

from checks.base_check import BaseCheck
from ssh import SshConnectionError, SshTimeoutError


_REGISTER_NAMES = ("rotation", "ae", "awb")
_REGISTER_ADDRESSES = {
    "rotation": ("0x10", "0x0c"),
    "ae": ("0x50", "0x02"),
    "awb": ("0x51", "0x00"),
}
_SCAN_ROWS = ("00", "10", "20", "30", "40", "50", "60", "70")
_SCAN_ROW_CELL_COUNTS = {"00": 13, "10": 16, "20": 16, "30": 16,
                         "40": 16, "50": 16, "60": 16, "70": 8}
_SCAN_HEADER_RE = re.compile(
    r"^\s*0\s+1\s+2\s+3\s+4\s+5\s+6\s+7\s+8\s+9\s+a\s+b\s+c\s+d\s+e\s+f\s*$",
    re.IGNORECASE,
)
_SCAN_ROW_RE = re.compile(r"^([0-7][0]):\s*((?:--|UU|[0-9A-Fa-f]{2})(?:\s+(?:--|UU|[0-9A-Fa-f]{2})){0,15})\s*$")


def parse_mode_mask(output: object) -> int:
    """Encode the observed 0x11/0x12 address response as bits 0/1."""
    text = output if isinstance(output, str) else ""
    tokens = set(re.findall(r"(?<![0-9A-Fa-f])(?:11|12)(?![0-9A-Fa-f])", text))
    return (1 if "11" in tokens else 0) | (2 if "12" in tokens else 0)


def _parse_scan(output: object) -> Optional[int]:
    if not isinstance(output, str) or not output.strip():
        return None
    saw_header = False
    rows = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        if _SCAN_HEADER_RE.fullmatch(line):
            if saw_header:
                return None
            saw_header = True
            continue
        match = _SCAN_ROW_RE.fullmatch(line)
        if (match is None or match.group(1) in rows
                or len(match.group(2).split()) != _SCAN_ROW_CELL_COUNTS[match.group(1)]):
            return None
        rows.add(match.group(1))
    if not saw_header or rows != set(_SCAN_ROWS):
        return None
    return parse_mode_mask(output)


def _parse_word(output: object) -> Optional[int]:
    if not isinstance(output, str):
        return None
    text = output.strip()
    match = re.fullmatch(r"0x([0-9A-Fa-f]{2})\s+0x([0-9A-Fa-f]{2})", text)
    if match:
        return (int(match.group(1), 16) << 8) | int(match.group(2), 16)
    match = re.fullmatch(r"0x([0-9A-Fa-f]{2})0x([0-9A-Fa-f]{2})", text)
    if match:
        return (int(match.group(1), 16) << 8) | int(match.group(2), 16)
    match = re.fullmatch(r"0x([0-9A-Fa-f]{4})", text)
    return int(match.group(1), 16) if match else None


class MixedComboEvidenceCheck(BaseCheck):
    """Collect raw I2C mode masks and ISP register words for one scenario."""

    name = "mixed_combo_evidence"
    scope = "hardware_evidence"

    @staticmethod
    def _settings(config: dict) -> dict:
        settings = config.get("mixed_combo_evidence") or {}
        return settings if isinstance(settings, dict) else {}

    @staticmethod
    def _channels(settings: dict) -> Optional[List[int]]:
        channels = settings.get("enabled_channels")
        if not isinstance(channels, list) or not channels:
            return None
        if any(not isinstance(channel, int) or isinstance(channel, bool) or channel not in (0, 1, 2, 3)
               for channel in channels) or len(channels) != len(set(channels)):
            return None
        return channels

    @staticmethod
    def _expected_masks(settings: dict) -> Optional[Dict[str, int]]:
        masks = settings.get("expected_mode_masks")
        if not isinstance(masks, dict):
            return None
        normalized = {str(key): value for key, value in masks.items()}
        if set(normalized) != {"1", "2"} or any(value not in (0, 3) for value in normalized.values()):
            return None
        return normalized

    @staticmethod
    def _bus_for_channel(channel: int) -> int:
        return 2 if channel in (0, 1) else 1

    @staticmethod
    def _address_for_channel(channel: int, mask: int) -> Optional[str]:
        if mask == 0:
            return "0x3c"
        if mask == 3:
            return "0x{0:02x}".format(0x11 + (channel % 2))
        return None

    def collect(self, ssh, config: dict) -> dict:
        settings = self._settings(config)
        test_id = settings.get("test_id")
        try:
            return self._collect(ssh, config)
        except (SshConnectionError, SshTimeoutError) as exc:
            return {
                "test_id": test_id,
                "mode_masks": {},
                "register_words": {},
                "errors": ["SSH_ERROR: {0}".format(exc)],
            }

    def _collect(self, ssh, config: dict) -> dict:
        settings = self._settings(config)
        test_id = settings.get("test_id")
        channels = self._channels(settings)
        expected_masks = self._expected_masks(settings)
        result: Dict[str, object] = {
            "test_id": test_id,
            "mode_masks": {},
            "register_words": {},
            "errors": [],
        }
        if not isinstance(test_id, int) or isinstance(test_id, bool) or test_id < 1 or channels is None or expected_masks is None:
            result["errors"] = ["mixed-combo collection settings are invalid"]
            return result
        mode_masks: Dict[str, int] = {}
        for bus in (1, 2):
            mask = _parse_scan(ssh.run("i2cdetect -y {0} 2>/dev/null".format(bus)))
            if mask is None:
                result["errors"] = ["i2c scan evidence is invalid"]
                return result
            mode_masks[str(bus)] = mask
        result["mode_masks"] = mode_masks
        errors: List[str] = []
        registers: Dict[str, Dict[str, int]] = {}
        for channel in channels:
            bus = self._bus_for_channel(channel)
            mask = mode_masks[str(bus)]
            address = self._address_for_channel(channel, mask)
            if address is None:
                errors.append("i2c-{0} returned incomplete dual-address mode mask {1}".format(bus, mask))
                continue
            words: Dict[str, int] = {}
            for name in _REGISTER_NAMES:
                high, low = _REGISTER_ADDRESSES[name]
                command = "i2ctransfer -f -y {0} w2@{1} {2} {3} r2 2>/dev/null".format(
                    bus, address, high, low)
                value = _parse_word(ssh.run(command))
                if value is None:
                    errors.append("ch{0} {1} register word is malformed".format(channel, name))
                    continue
                words[name] = value
            registers[str(channel)] = words
        result["register_words"] = registers
        result["errors"] = errors
        return result

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "mixed-combo evidence is not an object"
        settings = self._settings(config)
        channels = self._channels(settings)
        expected_masks = self._expected_masks(settings)
        if channels is None or expected_masks is None:
            return False, "mixed-combo validation settings are invalid"
        errors = data.get("errors")
        if not isinstance(errors, list):
            return False, "mixed-combo evidence errors are malformed"
        if errors:
            return False, "; ".join(str(error) for error in errors)
        masks = data.get("mode_masks")
        if not isinstance(masks, dict) or set(masks) != {"1", "2"}:
            return False, "mode mask evidence is incomplete"
        for bus in ("1", "2"):
            value = masks.get(bus)
            if not isinstance(value, int) or isinstance(value, bool) or value not in (0, 1, 2, 3):
                return False, "i2c-{0} mode mask is invalid".format(bus)
            if value != expected_masks[bus]:
                return False, "i2c-{0} mode mask {1} (expected {2})".format(bus, value, expected_masks[bus])
        registers = data.get("register_words")
        if not isinstance(registers, dict):
            return False, "register word evidence is malformed"
        for channel in channels:
            words = registers.get(str(channel))
            if not isinstance(words, dict):
                return False, "ch{0} register words are missing".format(channel)
            for name in _REGISTER_NAMES:
                value = words.get(name)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 0xffff:
                    return False, "ch{0} {1} register word is missing or invalid".format(channel, name)
        return True, "OK"
