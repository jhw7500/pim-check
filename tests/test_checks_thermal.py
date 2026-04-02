"""
tests/test_checks_thermal.py - ThermalCheck 단위 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.thermal import ThermalCheck


class TestThermalCheckCollect(unittest.TestCase):
    def setUp(self):
        self.check = ThermalCheck()
        self.config = {"thermal": {"max_temp_c": 85, "warn_temp_c": 80}}

    def test_collect_reads_thermal_zones(self):
        """zone0=65000, zone1=70000 → 65.0, 70.0 변환 확인"""
        ssh = MagicMock()
        ssh.run.side_effect = lambda cmd: "65000" if "zone0" in cmd else "70000"

        data = self.check.collect(ssh, self.config)

        self.assertAlmostEqual(data["temps"][0], 65.0)
        self.assertAlmostEqual(data["temps"][1], 70.0)
        self.assertAlmostEqual(data["max_temp"], 70.0)

    def test_collect_handles_missing_zone(self):
        """zone1이 None 반환 시 temps에 zone1 항목 없음 확인"""
        ssh = MagicMock()
        ssh.run.side_effect = lambda cmd: "65000" if "zone0" in cmd else None

        data = self.check.collect(ssh, self.config)

        self.assertIn(0, data["temps"])
        self.assertNotIn(1, data["temps"])
        self.assertAlmostEqual(data["max_temp"], 65.0)


class TestThermalCheckValidate(unittest.TestCase):
    def setUp(self):
        self.check = ThermalCheck()
        self.config = {"thermal": {"max_temp_c": 85, "warn_temp_c": 80}}

    def test_validate_normal_temp_passes(self):
        """max_temp=70.0 < warn(80) → True, OK"""
        data = {"temps": {0: 65.0, 1: 70.0}, "max_temp": 70.0}
        passed, reason = self.check.validate(data, self.config)
        self.assertTrue(passed)
        self.assertEqual(reason, "OK")

    def test_validate_over_max_fails(self):
        """max_temp=90.0 > max_allowed(85) → False"""
        data = {"temps": {0: 90.0}, "max_temp": 90.0}
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("90", reason)
        self.assertIn("85", reason)

    def test_validate_warn_zone_passes_with_warning(self):
        """max_temp=82.0 > warn(80) but < max(85) → True + WARN 포함"""
        data = {"temps": {0: 82.0}, "max_temp": 82.0}
        passed, reason = self.check.validate(data, self.config)
        self.assertTrue(passed)
        self.assertIn("WARN", reason)
        self.assertIn("82", reason)
        self.assertIn("80", reason)


if __name__ == "__main__":
    unittest.main()
