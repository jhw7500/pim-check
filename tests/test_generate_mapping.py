"""
tests/test_generate_mapping.py — scripts/generate_comprehensive_mapping.py 단위 테스트.

generate_scenarios import는 무거우니 mock scenario 데이터로 빌더 단위 테스트.
실 generate_scenarios는 별도 integration spot check.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from generate_comprehensive_mapping import (  # type: ignore[import-not-found]
    MULTI_COMBOS,
    build_mapping,
    scenario_active_channels,
    write_mapping,
)


def _scenario(name: str, res: str, enables: list[bool]) -> dict:
    """4채널 enable 상태로 mock scenario 생성."""
    changes = []
    for ch in range(4):
        if ch <= 1:
            path = f".VHL_CAM.i2c2.ch{ch}.enable"
        else:
            path = f".VHL_CAM.i2c1.ch{ch}.enable"
        changes.append((path, enables[ch]))
    return {"name": name, "res": res, "changes": changes}


class TestScenarioActiveChannels(unittest.TestCase):

    def test_4ch_all_active(self):
        s = _scenario("x", "720p", [True, True, True, True])
        self.assertEqual(scenario_active_channels(s), {0, 1, 2, 3})

    def test_2ch_ch01(self):
        s = _scenario("x", "720p", [True, True, False, False])
        self.assertEqual(scenario_active_channels(s), {0, 1})

    def test_1ch_ch3(self):
        s = _scenario("x", "fhd", [False, False, False, True])
        self.assertEqual(scenario_active_channels(s), {3})

    def test_no_active(self):
        s = _scenario("x", "720p", [False, False, False, False])
        self.assertEqual(scenario_active_channels(s), set())

    def test_ignores_non_enable_keys(self):
        s = {"name": "x", "res": "720p", "changes": [
            (".VHL_CAM.cam_width", 1280),
            (".VHL_CAM.i2c2.ch0.vflip", True),  # vflip은 active 신호 아님
            (".VHL_CAM.i2c2.ch0.enable", True),
        ]}
        self.assertEqual(scenario_active_channels(s), {0})


class TestBuildMapping(unittest.TestCase):

    def test_4ch_quad_maps_to_multi_4ch(self):
        scenarios = [
            _scenario("p2_quad_720p_ch0_vflip", "720p", [True]*4),
            _scenario("p2_quad_fhd_ch1_awb", "fhd", [True]*4),
        ]
        mapping, unmapped = build_mapping(scenarios)
        self.assertEqual(mapping["p2_quad_720p_ch0_vflip"], "multi_4ch_720p")
        self.assertEqual(mapping["p2_quad_fhd_ch1_awb"], "multi_4ch_fhd")
        self.assertEqual(unmapped, [])

    def test_samebus_i2c2_maps_to_multi_2ch_01(self):
        scenarios = [
            _scenario("p3_samebus_i2c2_720p_ch0_vflip", "720p",
                      [True, True, False, False]),
        ]
        mapping, _ = build_mapping(scenarios)
        self.assertEqual(mapping["p3_samebus_i2c2_720p_ch0_vflip"],
                         "multi_2ch_01_720p")

    def test_unmapped_combo_collected(self):
        # ch2+ch3 (samebus_i2c1) — multi case에 없음
        scenarios = [
            _scenario("p3_samebus_i2c1_720p_ch2_vflip", "720p",
                      [False, False, True, True]),
        ]
        mapping, unmapped = build_mapping(scenarios)
        self.assertNotIn("p3_samebus_i2c1_720p_ch2_vflip", mapping)
        self.assertEqual(len(unmapped), 1)
        self.assertEqual(unmapped[0]["active_channels"], [2, 3])
        self.assertIn("8 mandatory", unmapped[0]["reason"])

    def test_crossbus_lo_unmapped(self):
        # ch0+ch2 — multi에 없음
        s = _scenario("p3_crossbus_lo_720p_ch0_vflip", "720p",
                      [True, False, True, False])
        _, unmapped = build_mapping([s])
        self.assertEqual(len(unmapped), 1)

    def test_all_8_mandatory_combos_mapped(self):
        """8 mandatory combinations 모두 빠짐없이 매핑되는지 검증."""
        combos_to_test = [
            ([True]*4, "multi_4ch"),
            ([True, True, True, False], "multi_3ch_012"),
            ([False, True, True, True], "multi_3ch_123"),
            ([True, True, False, False], "multi_2ch_01"),
            ([True, False, False, True], "multi_2ch_03"),
            ([False, True, True, False], "multi_2ch_12"),
            ([True, False, False, False], "multi_1ch_0"),
            ([False, False, False, True], "multi_1ch_3"),
        ]
        for enables, expected_prefix in combos_to_test:
            for res in ("720p", "fhd"):
                s = _scenario("test", res, enables)
                mapping, unmapped = build_mapping([s])
                self.assertEqual(unmapped, [],
                                 f"mandatory combo {enables} unmapped (res={res})")
                self.assertEqual(mapping["test"], f"{expected_prefix}_{res}")


class TestWriteMapping(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_write_creates_dirs(self):
        path = os.path.join(self.tmpdir, "subdir", "mapping.json")
        write_mapping({"a": "b"}, path)
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data, {"a": "b"})

    def test_sorted_keys(self):
        path = os.path.join(self.tmpdir, "m.json")
        write_mapping({"z": "1", "a": "2", "m": "3"}, path)
        with open(path) as f:
            content = f.read()
        # 정렬 확인 — a가 z보다 먼저 등장
        self.assertLess(content.index('"a"'), content.index('"z"'))


class TestMultiCombosCoverage(unittest.TestCase):
    """MULTI_COMBOS dict가 사용자 명시 8 mandatory combinations 정확히 매핑."""

    def test_exactly_8_combinations(self):
        self.assertEqual(len(MULTI_COMBOS), 8)

    def test_all_combinations_distinct(self):
        # 활성 채널 조합이 모두 다른지
        keys = list(MULTI_COMBOS.keys())
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
