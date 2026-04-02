"""
tests/test_checks_legacy.py - LegacyFileCheck 단위 테스트
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from checks.legacy import LegacyFileCheck


class TestLegacyFileCheckCollect(unittest.TestCase):
    def setUp(self):
        self.check = LegacyFileCheck()
        self.config = {
            "legacy_files": {
                "must_not_exist": [
                    "/tmp/bg_cam_err_streak",
                    "/tmp/pim_cam_start_ts",
                    "/tmp/cam_state.json",
                ]
            }
        }

    def test_collect_no_legacy_files(self):
        """모든 파일이 없을 때 found 목록이 비어 있어야 함"""
        ssh = MagicMock()
        ssh.run.return_value = None

        data = self.check.collect(ssh, self.config)

        self.assertEqual(data["found"], [])

    def test_collect_legacy_files_exist(self):
        """한 파일이 EXISTS를 반환할 때 found 목록에 포함되어야 함"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "/tmp/bg_cam_err_streak" in cmd:
                return "EXISTS"
            return None

        ssh.run.side_effect = side_effect

        data = self.check.collect(ssh, self.config)

        self.assertIn("/tmp/bg_cam_err_streak", data["found"])
        self.assertNotIn("/tmp/pim_cam_start_ts", data["found"])
        self.assertNotIn("/tmp/cam_state.json", data["found"])


class TestLegacyFileCheckValidate(unittest.TestCase):
    def setUp(self):
        self.check = LegacyFileCheck()
        self.config = {
            "legacy_files": {
                "must_not_exist": [
                    "/tmp/bg_cam_err_streak",
                    "/tmp/pim_cam_start_ts",
                    "/tmp/cam_state.json",
                ]
            }
        }

    def test_validate_clean_passes(self):
        """found가 비어 있으면 PASS"""
        data = {"found": []}
        passed, reason = self.check.validate(data, self.config)
        self.assertTrue(passed)
        self.assertEqual(reason, "OK")

    def test_validate_legacy_present_fails(self):
        """found에 파일이 있으면 FAIL, 파일명이 reason에 포함되어야 함"""
        data = {"found": ["/tmp/bg_cam_err_streak", "/tmp/cam_state.json"], "missing": []}
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("bg_cam_err_streak", reason)
        self.assertIn("cam_state.json", reason)


class TestMustExist(unittest.TestCase):
    def setUp(self):
        self.check = LegacyFileCheck()
        self.config = {
            "legacy_files": {
                "must_not_exist": [],
                "must_exist": [
                    "/tmp/cam_state/recording/start_video_time",
                    "/root/shared_v/edgeconf_pim.json",
                ],
            }
        }

    def test_collect_must_exist_all_present(self):
        """모든 필수 파일이 있으면 missing 비어 있음"""
        ssh = MagicMock()
        ssh.run.return_value = "EXISTS"
        data = self.check.collect(ssh, self.config)
        self.assertEqual(data["missing"], [])

    def test_collect_must_exist_one_missing(self):
        """하나가 없으면 missing에 포함"""
        ssh = MagicMock()

        def side_effect(cmd):
            if "start_video_time" in cmd:
                return None
            return "EXISTS"

        ssh.run.side_effect = side_effect
        data = self.check.collect(ssh, self.config)
        self.assertIn("/tmp/cam_state/recording/start_video_time", data["missing"])

    def test_validate_missing_required_fails(self):
        """missing이 있으면 FAIL"""
        data = {"found": [], "missing": ["/root/shared_v/edgeconf_pim.json"]}
        passed, reason = self.check.validate(data, self.config)
        self.assertFalse(passed)
        self.assertIn("edgeconf_pim.json", reason)

    def test_validate_both_issues(self):
        """must_not_exist 위반 + must_exist 위반 동시"""
        config = {
            "legacy_files": {
                "must_not_exist": ["/tmp/bad_file"],
                "must_exist": ["/tmp/good_file"],
            }
        }
        data = {"found": ["/tmp/bad_file"], "missing": ["/tmp/good_file"]}
        passed, reason = self.check.validate(data, config)
        self.assertFalse(passed)
        self.assertIn("bad_file", reason)
        self.assertIn("good_file", reason)


if __name__ == "__main__":
    unittest.main()
