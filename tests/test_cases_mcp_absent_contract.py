"""Offline contract tests for the board-specific MCP4018-absent profile.

No SSH client is created and no profile command is executed. These tests
validate configuration and existing check verdicts, not hardware acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from checks.custom import CustomCommandCheck
from checks.recording import RecordingCheck
from config import load_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles"
BASE_CASE = "720p_4ch"
CASE = "720p_4ch_mcp_absent"
CHANNELS = (("i2c2", "ch0", 16), ("i2c2", "ch1", 32),
            ("i2c1", "ch2", 48), ("i2c1", "ch3", 63))


class UniqueLoader(yaml.SafeLoader):
    """Do not silently accept duplicate YAML mappings."""


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                             _mapping)


def _raw(case):
    return yaml.load((PROFILES / "cases" / f"{case}.yaml").read_text(
        encoding="utf-8"), Loader=UniqueLoader)


def _profile():
    return load_profile(str(PROFILES), case=CASE)


def test_yaml_duplicate_keys_are_rejected():
    with pytest.raises(ValueError, match="duplicate YAML key"):
        yaml.load("checks:\n  recording: {}\n  recording: {}\n",
                  Loader=UniqueLoader)


def test_real_profile_has_no_duplicate_keys():
    assert _raw(CASE)["checks"]["recording"]["session_progress"] == "required"


def test_target_default_and_variant_tag():
    profile = _profile()
    assert profile["target"]["host"] == "192.168.214.4"
    assert "mcp_absent" in profile["tags"]


@pytest.mark.parametrize("bus,channel,wiper", CHANNELS)
def test_four_channels_enabled_but_each_led_disabled(bus, channel, wiper):
    changes = _profile()["setup"]["edgeconf_changes"]
    prefix = f".VHL_CAM.{bus}.{channel}"
    assert changes[prefix + ".enable"] is True
    assert changes[prefix + ".led_flash.enable"] is False
    assert changes[prefix + ".led_flash.wiper"] == wiper


def test_original_equipped_board_profile_is_preserved():
    changes = _raw(BASE_CASE)["setup"]["edgeconf_changes"]
    assert changes[".VHL_CAM.i2c2.ch0.led_flash.enable"] is True
    assert changes[".VHL_CAM.i2c1.ch2.led_flash.enable"] is True


def test_non_led_setup_matches_original_exactly():
    old = _raw(BASE_CASE)["setup"]
    candidate = deepcopy(_raw(CASE)["setup"])
    for bus, channel, _ in CHANNELS:
        key = f".VHL_CAM.{bus}.{channel}.led_flash.enable"
        candidate["edgeconf_changes"][key] = old["edgeconf_changes"][key]
    assert candidate == old


def test_non_led_custom_checks_are_preserved_exactly():
    old = _raw(BASE_CASE)["checks"]["custom_commands"]
    new = _raw(CASE)["checks"]["custom_commands"]
    assert [x for x in new if "led_flash" not in x["name"]] == [
        x for x in old if "led_flash" not in x["name"]]


def test_frame_flow_and_recording_checks_are_not_skipped():
    checks = _profile()["checks"]
    assert checks["frame_flow"]["enabled"] is True
    assert checks["frame_flow"]["path"] == "/run/pim-camera/gstApp.json"
    assert checks["recording"]["expected_channels"] == 4
    assert checks["recording"]["session_progress"] == "required"
    passed, reason = RecordingCheck().validate({"session_count": 0}, checks)
    assert passed is False
    assert "NEED_2_FINALIZES_AFTER_BOOT" in reason


@pytest.mark.parametrize("bus,channel,wiper", CHANNELS)
@pytest.mark.parametrize("observed,expected_pass", [("false", True), ("true", False)])
def test_led_readback_verdicts_reject_reenabled_hardware(
        bus, channel, wiper, observed, expected_pass):
    items = _profile()["checks"]["custom_commands"]
    spec = next(x for x in items if x["name"].startswith(channel + " led_flash "))
    assert f".VHL_CAM.{bus}.{channel}.led_flash" in spec["command"]
    assert spec["expected"] == f"false|{wiper}"
    row = {**spec, "output": f"{observed}|{wiper}",
           "on_fail": "LED value does not match the absent-hardware profile"}
    passed, _ = CustomCommandCheck().validate({"results": [row]}, {})
    assert passed is expected_pass


def test_profile_keeps_original_reset_and_does_not_add_fault_actions():
    old, new = _raw(BASE_CASE), _raw(CASE)
    assert new["setup"]["reboot_after"] is True
    assert set(new["setup"]) == set(old["setup"])
    assert new.get("teardown") == old.get("teardown")
