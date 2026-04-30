"""
tests/test_plan_resolve.py — plan.resolve_cases 단위 테스트.

매칭/glob/차집합/빈 매칭 reject 동작 검증.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from plan import Plan, DEFAULT_EXECUTION, DEFAULT_GATE, resolve_cases


def _make_plan(cases: dict) -> Plan:
    """테스트용 Plan 생성."""
    return Plan(
        name="test",
        description="test plan",
        version=1,
        cases=cases,
        execution=dict(DEFAULT_EXECUTION),
        gate=dict(DEFAULT_GATE),
        reports=[],
    )


class TestResolveCases(unittest.TestCase):
    """name 정확 일치 / glob / regression∩delta 차집합 / 빈 매칭."""

    def setUp(self):
        # 가상 profiles 디렉토리 만들기
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "cases"))
        os.makedirs(os.path.join(self.tmpdir, "generated"))

        # 더미 case 파일 (내용은 중요하지 않음, stem만 사용)
        for name in ("720p_2ch", "fhd_4ch", "fault_cam_disconnect"):
            with open(os.path.join(self.tmpdir, "cases", f"{name}.yaml"), "w") as f:
                f.write("name: x\n")

        for name in ("gen_720p_2ch_15fps", "gen_720p_2ch_30fps",
                     "gen_fhd_4ch_15fps", "gen_hflip_ch0", "gen_hflip_ch1",
                     "gen_ord_vcm_a", "gen_ord_vcm_b"):
            with open(os.path.join(self.tmpdir, "generated", f"{name}.yaml"), "w") as f:
                f.write("name: x\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_exact_name_match(self):
        plan = _make_plan({"regression": ["720p_2ch"]})
        result = resolve_cases(plan, self.tmpdir)
        self.assertEqual(result, [("regression", "720p_2ch")])

    def test_glob_match(self):
        plan = _make_plan({"regression": ["gen_hflip_*"]})
        result = resolve_cases(plan, self.tmpdir)
        names = [n for _, n in result]
        self.assertEqual(names, ["gen_hflip_ch0", "gen_hflip_ch1"])
        for section, _ in result:
            self.assertEqual(section, "regression")

    def test_glob_searches_both_cases_and_generated(self):
        plan = _make_plan({"regression": ["*720p*"]})
        result = resolve_cases(plan, self.tmpdir)
        names = [n for _, n in result]
        # cases/720p_2ch + generated/gen_720p_2ch_15fps + gen_720p_2ch_30fps
        self.assertIn("720p_2ch", names)
        self.assertIn("gen_720p_2ch_15fps", names)
        self.assertIn("gen_720p_2ch_30fps", names)

    def test_unmatched_selector_raises(self):
        plan = _make_plan({"regression": ["nonexistent_case"]})
        with self.assertRaises(ValueError) as ctx:
            resolve_cases(plan, self.tmpdir)
        self.assertIn("nonexistent_case", str(ctx.exception))

    def test_unmatched_glob_raises(self):
        plan = _make_plan({"regression": ["xyz_*"]})
        with self.assertRaises(ValueError) as ctx:
            resolve_cases(plan, self.tmpdir)
        self.assertIn("xyz_*", str(ctx.exception))

    def test_delta_difference_excludes_regression_overlap(self):
        # regression이 720p_2ch을 가지면 delta의 *720p* glob에서 720p_2ch는 제외
        plan = _make_plan({
            "regression": ["720p_2ch"],
            "delta": ["*720p*"],
        })
        result = resolve_cases(plan, self.tmpdir)
        regression_names = [n for s, n in result if s == "regression"]
        delta_names = [n for s, n in result if s == "delta"]

        self.assertEqual(regression_names, ["720p_2ch"])
        self.assertNotIn("720p_2ch", delta_names)
        # generated의 720p 케이스는 여전히 delta에 포함
        self.assertIn("gen_720p_2ch_15fps", delta_names)
        self.assertIn("gen_720p_2ch_30fps", delta_names)

    def test_within_section_dedup(self):
        # 같은 case가 두 번 들어와도 한 번만 등장
        plan = _make_plan({
            "regression": ["720p_2ch", "720p_2ch", "*720p_2ch*"],
        })
        result = resolve_cases(plan, self.tmpdir)
        names = [n for _, n in result]
        # 720p_2ch는 정확히 한 번만
        self.assertEqual(names.count("720p_2ch"), 1)

    def test_section_order_regression_first(self):
        plan = _make_plan({
            "delta": ["fhd_4ch"],
            "regression": ["720p_2ch"],
        })
        result = resolve_cases(plan, self.tmpdir)
        # regression이 항상 delta보다 앞
        sections = [s for s, _ in result]
        self.assertEqual(sections, ["regression", "delta"])

    def test_only_delta_section(self):
        plan = _make_plan({"delta": ["fhd_4ch"]})
        result = resolve_cases(plan, self.tmpdir)
        self.assertEqual(result, [("delta", "fhd_4ch")])

    def test_only_regression_section(self):
        plan = _make_plan({"regression": ["720p_2ch"]})
        result = resolve_cases(plan, self.tmpdir)
        self.assertEqual(result, [("regression", "720p_2ch")])

    def test_multiple_unmatched_collected(self):
        plan = _make_plan({
            "regression": ["nonexistent1", "nonexistent2"],
        })
        with self.assertRaises(ValueError) as ctx:
            resolve_cases(plan, self.tmpdir)
        msg = str(ctx.exception)
        self.assertIn("nonexistent1", msg)
        self.assertIn("nonexistent2", msg)

    def test_glob_alphabetic_sort(self):
        plan = _make_plan({"regression": ["gen_ord_vcm_*"]})
        result = resolve_cases(plan, self.tmpdir)
        names = [n for _, n in result]
        # 알파벳 정렬 보장
        self.assertEqual(names, sorted(names))


if __name__ == "__main__":
    unittest.main()
