"""
tests/test_checks_max9296_abi.py - Max9296AbiCheck 단위 테스트

fixture 는 2026-08-21 보드(192.168.214.4, 드라이버 2.5) 실측 출력을 사용한다.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from checks.max9296_abi import Max9296AbiCheck, _parse_prepare_line

# 보드 실측: idle 상태 prepare 상태라인.
PREPARE_IDLE = (
    "state=IDLE generation=0 epoch=2 mode=none table=none width=0 height=0 "
    "fps=0 code=0x0 enable=0 errno=0 worker_errno=0 lease=0 match=0"
)

# 보드 실측 health_raw 를 구조 보존 축약한 fixture (adapter 1 → ch2/ch3).
HEALTH_OK = json.dumps({
    "schema": 1,
    "adapter": 1,
    "sequence": 1,
    "observed_monotonic_ms": 6025286,
    "busy": False,
    "mode": "dual-wide",
    "streaming": False,
    "deserializer": {"status": "OK", "errno": 0, "device_id": 150,
                     "link_a_up": True, "link_b_up": True},
    "channels": [
        {"channel": 2, "enabled": True, "phy": "B",
         "link": {"status": "OK", "up": True},
         "serializer": {"status": "OK", "errno": 0, "device_id": 145}},
        {"channel": 3, "enabled": True, "phy": "A",
         "link": {"status": "OK", "up": True},
         "serializer": {"status": "OK", "errno": 0, "device_id": 145}},
    ],
})

MODINFO_25 = "version:        2.5"

CONFIG = {
    "max9296_abi": {
        "expected_version": "2.5",
        "adapters": [1, 2],
        "i2c_addr": "0048",
    }
}


def _ssh_ok():
    ssh = MagicMock()

    def side_effect(cmd):
        if cmd.startswith("modinfo"):
            return MODINFO_25
        if cmd.endswith("/prepare 2>/dev/null"):
            return PREPARE_IDLE
        if cmd.endswith("/health_raw 2>/dev/null"):
            return HEALTH_OK
        return None

    ssh.run.side_effect = side_effect
    return ssh


class TestParsePrepareLine(unittest.TestCase):
    def test_parses_board_idle_line(self):
        fields = _parse_prepare_line(PREPARE_IDLE)
        self.assertEqual(fields["state"], "IDLE")
        self.assertEqual(fields["errno"], "0")
        self.assertEqual(fields["worker_errno"], "0")
        self.assertEqual(fields["lease"], "0")


class TestMax9296AbiCollect(unittest.TestCase):
    def test_collect_reads_version_and_both_adapters(self):
        check = Max9296AbiCheck()
        data = check.collect(_ssh_ok(), CONFIG)
        self.assertEqual(data["version_raw"], MODINFO_25)
        self.assertEqual(set(data["nodes"].keys()), {"1", "2"})
        self.assertEqual(data["nodes"]["1"]["prepare"], PREPARE_IDLE)

    def test_collect_skips_without_expected_version(self):
        check = Max9296AbiCheck()
        ssh = MagicMock()
        data = check.collect(ssh, {"max9296_abi": {"expected_version": None}})
        self.assertTrue(data["skipped"])
        ssh.run.assert_not_called()


class TestMax9296AbiValidate(unittest.TestCase):
    def setUp(self):
        self.check = Max9296AbiCheck()

    def _data(self, **over):
        data = {
            "version_raw": MODINFO_25,
            "nodes": {
                "1": {"prepare": PREPARE_IDLE, "health_raw": HEALTH_OK},
                "2": {"prepare": PREPARE_IDLE, "health_raw": HEALTH_OK},
            },
        }
        data.update(over)
        return data

    def test_pass_on_board_fixture(self):
        passed, reason = self.check.validate(self._data(), CONFIG)
        self.assertTrue(passed, reason)

    def test_skipped_passes(self):
        passed, reason = self.check.validate({"skipped": True}, CONFIG)
        self.assertTrue(passed)
        self.assertIn("Skipped", reason)

    def test_fail_on_version_mismatch(self):
        data = self._data(version_raw="version:        2.4")
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("2.4", reason)
        self.assertIn("2.5", reason)

    def test_fail_on_module_missing(self):
        data = self._data(version_raw=None)
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("modinfo", reason)

    def test_fail_on_prepare_node_missing(self):
        data = self._data()
        data["nodes"]["2"]["prepare"] = None
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("adapter 2", reason)
        self.assertIn("prepare node missing", reason)

    def test_fail_on_worker_errno(self):
        # STREAMON 거부의 durable 진단 — worker_errno 가 음수로 남는다.
        bad = PREPARE_IDLE.replace("worker_errno=0", "worker_errno=-5")
        data = self._data()
        data["nodes"]["1"]["prepare"] = bad
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("worker_errno=-5", reason)

    def test_fail_on_prepare_state_failed(self):
        bad = PREPARE_IDLE.replace("state=IDLE", "state=FAILED")
        data = self._data()
        data["nodes"]["1"]["prepare"] = bad
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("state=FAILED", reason)

    def test_fail_on_enabled_channel_link_down(self):
        health = json.loads(HEALTH_OK)
        health["channels"][0]["link"] = {"status": "FAIL", "up": False}
        data = self._data()
        data["nodes"]["1"]["health_raw"] = json.dumps(health)
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("ch2 link", reason)

    def test_disabled_channel_link_ignored(self):
        health = json.loads(HEALTH_OK)
        health["channels"][0]["enabled"] = False
        health["channels"][0]["link"] = {"status": "FAIL", "up": False}
        data = self._data()
        data["nodes"]["1"]["health_raw"] = json.dumps(health)
        passed, reason = self.check.validate(data, CONFIG)
        self.assertTrue(passed, reason)

    def test_fail_on_health_raw_not_json(self):
        data = self._data()
        data["nodes"]["1"]["health_raw"] = "not json"
        passed, reason = self.check.validate(data, CONFIG)
        self.assertFalse(passed)
        self.assertIn("not valid JSON", reason)


if __name__ == "__main__":
    unittest.main()
