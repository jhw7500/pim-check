"""
tests/test_config.py - config.py 단위 테스트
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import deep_merge, load_profile


BASE_YAML = """\
target:
  host: 192.168.0.5
checks:
  cpu:
    gst_range: [0, 100]
    bg_check_max_pct: 3.0
monitor:
  duration_sec: 300
  interval_sec: 5
"""

CASE_YAML = """\
name: FHD 4ch
checks:
  cpu:
    gst_range: [50, 95]
"""


class TestDeepMerge(unittest.TestCase):

    def test_shallow_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        result = deep_merge(base, override)
        self.assertEqual(result, {"a": 1, "b": 3})

    def test_nested_dict_merge(self):
        base = {"cpu": {"warn": 80, "max": 95}}
        override = {"cpu": {"warn": 85}}
        result = deep_merge(base, override)
        self.assertEqual(result["cpu"]["warn"], 85)
        self.assertEqual(result["cpu"]["max"], 95)

    def test_override_replaces_non_dict(self):
        base = {"checks": {"cpu": {"gst_range": [0, 100]}}}
        override = {"checks": {"cpu": {"gst_range": [50, 95]}}}
        result = deep_merge(base, override)
        self.assertEqual(result["checks"]["cpu"]["gst_range"], [50, 95])

    def test_base_unchanged(self):
        base = {"a": 1, "b": {"x": 10}}
        override = {"b": {"x": 99, "y": 20}}
        _ = deep_merge(base, override)
        self.assertEqual(base["b"]["x"], 10)
        self.assertNotIn("y", base["b"])

    def test_new_key_in_override(self):
        base = {"a": 1}
        override = {"b": 2}
        result = deep_merge(base, override)
        self.assertEqual(result["a"], 1)
        self.assertEqual(result["b"], 2)


class TestLoadProfile(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        cases_dir = os.path.join(self.tmpdir, "cases")
        os.makedirs(cases_dir)

        with open(os.path.join(self.tmpdir, "base.yaml"), "w") as f:
            f.write(BASE_YAML)

        with open(os.path.join(cases_dir, "fhd_4ch.yaml"), "w") as f:
            f.write(CASE_YAML)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_load_base_only(self):
        profile = load_profile(self.tmpdir, case=None)
        self.assertEqual(profile["target"]["host"], "192.168.0.5")
        self.assertEqual(profile["checks"]["cpu"]["gst_range"], [0, 100])
        self.assertEqual(profile["monitor"]["duration_sec"], 300)

    def test_load_with_case_override(self):
        profile = load_profile(self.tmpdir, case="fhd_4ch")
        self.assertEqual(profile["name"], "FHD 4ch")
        self.assertEqual(profile["checks"]["cpu"]["gst_range"], [50, 95])
        # base에서 상속된 값 확인
        self.assertEqual(profile["checks"]["cpu"]["bg_check_max_pct"], 3.0)
        self.assertEqual(profile["target"]["host"], "192.168.0.5")
        self.assertEqual(profile["monitor"]["duration_sec"], 300)

    def test_load_nonexistent_case_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_profile(self.tmpdir, case="nonexistent")

    def test_cli_overrides_host(self):
        profile = load_profile(self.tmpdir, case=None)
        profile["target"]["host"] = "10.0.0.1"
        self.assertEqual(profile["target"]["host"], "10.0.0.1")

    def test_cli_overrides_duration(self):
        profile = load_profile(self.tmpdir, case=None)
        profile["monitor"]["duration_sec"] = 60
        self.assertEqual(profile["monitor"]["duration_sec"], 60)


if __name__ == "__main__":
    unittest.main()
