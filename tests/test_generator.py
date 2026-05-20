"""generator.py 테스트"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import yaml

from generator import (
    build_case,
    generate_combinations,
    generate_cases,
    resolve_rule,
)


class TestResolveRule(unittest.TestCase):
    def test_exact_match(self):
        rules = {"720p+2ch": [5, 60], "fhd+4ch": [50, 95]}
        self.assertEqual(resolve_rule(rules, "720p+2ch"), [5, 60])

    def test_wildcard_match(self):
        rules = {"fhd+*": 82}
        self.assertEqual(resolve_rule(rules, "fhd+2ch"), 82)
        self.assertEqual(resolve_rule(rules, "fhd+4ch"), 82)

    def test_no_match(self):
        rules = {"fhd+*": 82}
        self.assertIsNone(resolve_rule(rules, "720p+2ch"))

    def test_exact_beats_wildcard(self):
        rules = {"fhd+*": 80, "fhd+4ch": 90}
        self.assertEqual(resolve_rule(rules, "fhd+4ch"), 90)

    def test_specific_wildcard_beats_generic(self):
        rules = {"*+*": 30, "fhd+*": 40}
        self.assertEqual(resolve_rule(rules, "fhd+2ch"), 40)


class TestGenerateCombinations(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "sources": {
                "edgeconf": {
                    "axes": {
                        "resolution": {
                            "combinations": [
                                {"name": "720p", "values": {".w": 1280}},
                                {"name": "fhd", "values": {".w": 1920}},
                            ]
                        },
                        "channels": {
                            "combinations": [
                                {"name": "2ch", "values": {".ch2": False}, "expect": {"channel_count": 2}},
                                {"name": "4ch", "values": {".ch2": True}, "expect": {"channel_count": 4}},
                            ]
                        },
                    }
                }
            },
            "generation": {"cross": ["resolution", "channels"]},
        }

    def test_generates_all_combinations(self):
        axes = self.schema["sources"]["edgeconf"]["axes"]
        combos = list(generate_combinations(axes, ["resolution", "channels"]))
        self.assertEqual(len(combos), 4)

    def test_combo_structure(self):
        axes = self.schema["sources"]["edgeconf"]["axes"]
        combos = list(generate_combinations(axes, ["resolution", "channels"]))
        first = combos[0]
        self.assertEqual(first[0][0], "resolution")
        self.assertEqual(first[1][0], "channels")


class TestBuildCase(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "expectations": {
                "cpu": {"gst_range": {"720p+2ch": [5, 60]}},
                "thermal": {"warn_temp_c": {"fhd+*": 82}},
                "cam_state": {
                    "dir": "/tmp/cam_state",
                    "expected_state": "healthy",
                    "max_streak": 0,
                },
                "stabilize_sec": {"720p+2ch": 30, "fhd+2ch": 40},
            }
        }

    def test_build_basic_case(self):
        combo = (
            ("resolution", {"name": "720p", "values": {".w": 1280, ".h": 720}}),
            ("channels", {"name": "2ch", "values": {".ch2": False}, "expect": {"channel_count": 2}}),
        )
        case, slug = build_case(combo, self.schema)
        self.assertEqual(slug, "720p_2ch")
        self.assertEqual(case["name"], "[auto] 720p_2ch")
        self.assertEqual(case["setup"]["edgeconf_changes"][".w"], 1280)
        self.assertEqual(case["setup"]["stabilize_sec"], 30)
        self.assertEqual(case["checks"]["cpu"]["gst_range"], [5, 60])
        self.assertEqual(case["checks"]["recording"]["expected_channels"], 2)
        self.assertEqual(case["checks"]["cam_state"]["expected_state"], "healthy")

    def test_thermal_only_for_fhd(self):
        combo_720p = (
            ("resolution", {"name": "720p", "values": {".w": 1280}}),
            ("channels", {"name": "2ch", "values": {".ch2": False}, "expect": {"channel_count": 2}}),
        )
        case, _ = build_case(combo_720p, self.schema)
        self.assertNotIn("thermal", case["checks"])

        combo_fhd = (
            ("resolution", {"name": "fhd", "values": {".w": 1920}}),
            ("channels", {"name": "2ch", "values": {".ch2": False}, "expect": {"channel_count": 2}}),
        )
        case, _ = build_case(combo_fhd, self.schema)
        self.assertEqual(case["checks"]["thermal"]["warn_temp_c"], 82)


class TestGenerateCases(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.profiles_dir = os.path.join(self.tmpdir, "profiles")
        os.makedirs(os.path.join(self.profiles_dir, "cases"))

        # base.yaml
        base = {"target": {"host": "192.168.0.5"}, "monitor": {"duration_sec": 300}}
        with open(os.path.join(self.profiles_dir, "base.yaml"), "w") as f:
            yaml.dump(base, f)

        # schema.yaml
        schema = {
            "version": 1,
            "sources": {
                "edgeconf": {
                    "path": "/root/shared_v/edgeconf_pim.json",
                    "axes": {
                        "resolution": {
                            "combinations": [
                                {"name": "720p", "values": {".w": 1280}},
                                {"name": "fhd", "values": {".w": 1920}},
                            ]
                        },
                        "channels": {
                            "combinations": [
                                {"name": "2ch", "values": {".ch2": False}, "expect": {"channel_count": 2}},
                                {"name": "4ch", "values": {".ch2": True}, "expect": {"channel_count": 4}},
                            ]
                        },
                    }
                }
            },
            "expectations": {
                "cpu": {"gst_range": {"720p+2ch": [5, 60], "fhd+4ch": [50, 95]}},
                "cam_state": {"dir": "/tmp/cam_state", "expected_state": "healthy", "max_streak": 0},
                "stabilize_sec": {"*+*": 30},
            },
            "generation": {
                "cross": ["resolution", "channels"],
                "mode": "all",
                "output_dir": os.path.join(self.profiles_dir, "generated"),
                "filename_pattern": "gen_{resolution}_{channels}.yaml",
            },
        }
        with open(os.path.join(self.profiles_dir, "schema.yaml"), "w") as f:
            yaml.dump(schema, f)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_generates_files(self):
        generated = generate_cases(self.profiles_dir)
        self.assertEqual(len(generated), 4)
        for path in generated:
            self.assertTrue(os.path.exists(path))

    def test_skips_manual_case(self):
        # 수동 케이스 추가 (720p+2ch와 동일 edgeconf_changes).
        # build_case 가 모든 generated case 에 capture.enable=false 기본값을 넣으므로
        # 수동 케이스도 동일하게 포함해야 dedup(중복 스킵)이 성립한다.
        manual = {
            "name": "manual",
            "setup": {"edgeconf_changes": {
                ".VHL_CAM.capture.enable": False, ".w": 1280, ".ch2": False,
            }},
        }
        with open(os.path.join(self.profiles_dir, "cases", "manual_720p.yaml"), "w") as f:
            yaml.dump(manual, f)

        generated = generate_cases(self.profiles_dir)
        slugs = [os.path.basename(p) for p in generated]
        self.assertNotIn("gen_720p_2ch.yaml", slugs)
        self.assertEqual(len(generated), 3)

    def test_generated_yaml_is_valid(self):
        generated = generate_cases(self.profiles_dir)
        for path in generated:
            with open(path) as f:
                data = yaml.safe_load(f)
            self.assertIn("name", data)
            self.assertIn("setup", data)
            self.assertIn("edgeconf_changes", data["setup"])
            self.assertIn("checks", data)


if __name__ == "__main__":
    unittest.main()
