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
                                {"name": "2ch", "values": {
                                    ".VHL_CAM.i2c2.ch0.enable": True, ".VHL_CAM.i2c2.ch1.enable": True,
                                    ".VHL_CAM.i2c1.ch2.enable": False, ".VHL_CAM.i2c1.ch3.enable": False,
                                }, "expect": {"channel_count": 2}},
                                {"name": "4ch", "values": {
                                    ".VHL_CAM.i2c2.ch0.enable": True, ".VHL_CAM.i2c2.ch1.enable": True,
                                    ".VHL_CAM.i2c1.ch2.enable": True, ".VHL_CAM.i2c1.ch3.enable": True,
                                }, "expect": {"channel_count": 4}},
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
                ".VHL_CAM.capture.enable": False, ".w": 1280,
                ".VHL_CAM.i2c2.ch0.enable": True, ".VHL_CAM.i2c2.ch1.enable": True,
                ".VHL_CAM.i2c1.ch2.enable": False, ".VHL_CAM.i2c1.ch3.enable": False,
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

    def test_session_progress_deduped_by_channel_count(self):
        """session_progress 는 채널수별 1개 대표 케이스에만 남는다(축소).

        스키마: 2ch/4ch × 720p/fhd = 4 케이스, 채널수 2종 → session_progress 는 2개만.
        expected_channels 는 모든 케이스에 유지된다(infer_agent 사용).
        """
        generated = generate_cases(self.profiles_dir)
        ch_with_sp = set()
        n_with_sp = 0
        n_with_expected = 0
        for path in generated:
            with open(path) as f:
                rec = (yaml.safe_load(f).get("checks", {}) or {}).get("recording", {}) or {}
            if rec.get("expected_channels") is not None:
                n_with_expected += 1
            if rec.get("session_progress") is not None:
                n_with_sp += 1
                ch_with_sp.add(rec.get("expected_channels"))
        self.assertEqual(n_with_expected, 4)        # 전 케이스 expected_channels 유지
        self.assertEqual(n_with_sp, 2)              # 채널수(2,4)별 1개씩만
        self.assertEqual(ch_with_sp, {2, 4})

    def test_no_channel_keys_keeps_session_progress(self):
        """채널 enable 키가 없는 케이스(actual_ch=0)는 dedup 되지 않고 session_progress 를
        유지한다 — 무채널 케이스가 조용히 누락되는 silent failure 방지 가드."""
        pdir = os.path.join(self.tmpdir, "profiles2")
        os.makedirs(os.path.join(pdir, "cases"))
        with open(os.path.join(pdir, "base.yaml"), "w") as f:
            yaml.dump({"monitor": {"duration_sec": 300}}, f)
        schema = {
            "version": 1,
            "sources": {"edgeconf": {"path": "/x", "axes": {
                "resolution": {"combinations": [
                    {"name": "720p", "values": {".w": 1280}},
                    {"name": "fhd", "values": {".w": 1920}},
                ]},
                # enable 키 없이 channel_count 만 → build_case 는 session_progress 를 넣지만
                # dedup 의 actual_ch 는 0 이 된다.
                "channels": {"combinations": [
                    {"name": "1ch", "values": {".dummy": 1}, "expect": {"channel_count": 1}},
                ]},
            }}},
            "expectations": {
                "cam_state": {"dir": "/tmp/cam_state", "expected_state": "healthy", "max_streak": 0},
                "stabilize_sec": {"*+*": 30},
            },
            "generation": {"cross": ["resolution", "channels"],
                           "output_dir": os.path.join(pdir, "generated"),
                           "filename_pattern": "gen_{resolution}_{channels}.yaml"},
        }
        with open(os.path.join(pdir, "schema.yaml"), "w") as f:
            yaml.dump(schema, f)

        generated = generate_cases(pdir)
        n_sp = sum(
            1 for p in generated
            if ((yaml.safe_load(open(p)).get("checks", {}) or {}).get("recording", {}) or {}).get("session_progress") is not None
        )
        # 720p/fhd × 1ch = 2 케이스, 둘 다 actual_ch=0 → 가드로 둘 다 유지(=2).
        # 가드 없으면 0 이 seen 에 들어가 두 번째가 silent 누락(=1)된다.
        self.assertEqual(n_sp, 2)


if __name__ == "__main__":
    unittest.main()
