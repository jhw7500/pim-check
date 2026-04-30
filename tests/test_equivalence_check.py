"""
tests/test_equivalence_check.py — scripts/equivalence_check.py 단위 테스트.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from equivalence_check import (  # type: ignore[import-not-found]
    CaseStatus,
    EquivalenceReport,
    compare,
    normalize_left,
    normalize_right,
    main as eq_main,
)


class TestNormalizeLeft(unittest.TestCase):
    """run_*.py 결과 형식 다양성 처리."""

    def test_run_comprehensive_format(self):
        # 'result' 키에 PASS/FAIL string
        data = [
            {"name": "scenario_a", "result": "PASS", "elapsed": 1.0},
            {"name": "scenario_b", "result": "FAIL", "elapsed": 2.0},
            {"name": "scenario_c", "result": "NO_SSH"},
        ]
        out = normalize_left(data)
        self.assertEqual(len(out), 3)
        self.assertTrue(out[0].passed)
        self.assertFalse(out[1].passed)
        self.assertFalse(out[2].passed)  # NO_SSH는 PASS 아님

    def test_run_channel_verify_format(self):
        # 'passed': "PASS"/"FAIL" string + 'case' 키
        data = [
            {"case": "gen_720p_ch0_vflip_off", "passed": "PASS"},
            {"case": "gen_720p_ch0_vflip_on", "passed": "FAIL"},
        ]
        out = normalize_left(data)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].name, "gen_720p_ch0_vflip_off")
        self.assertTrue(out[0].passed)
        self.assertFalse(out[1].passed)

    def test_pass_field_bool(self):
        # 'pass': True/False (mixed_combo 형식)
        data = [
            {"name": "test1", "pass": True},
            {"name": "test2", "pass": False},
        ]
        out = normalize_left(data)
        self.assertTrue(out[0].passed)
        self.assertFalse(out[1].passed)

    def test_unrecognized_entry_skipped(self):
        data = [
            {"name": "valid", "result": "PASS"},
            {"foo": "bar"},  # name + passed/result/pass 모두 없음
            {"name": "also_invalid"},  # name 있지만 passed/result 없음
        ]
        out = normalize_left(data)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].name, "valid")

    def test_non_list_raises(self):
        with self.assertRaises(ValueError):
            normalize_left({"executions": []})


class TestNormalizeRight(unittest.TestCase):

    def test_plan_format(self):
        data = {
            "plan": {"name": "x"},
            "executions": [
                {"case_name": "a", "passed": True},
                {"case_name": "b", "passed": False},
            ],
        }
        out = normalize_right(data)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].name, "a")
        self.assertTrue(out[0].passed)

    def test_no_executions_raises(self):
        with self.assertRaises(ValueError):
            normalize_right({"plan": {"name": "x"}})

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            normalize_right([])

    def test_skip_invalid_entries(self):
        data = {"executions": [
            {"case_name": "a", "passed": True},
            "string_not_dict",
            {"passed": True},  # case_name 없음
            {"case_name": "b", "passed": False},
        ]}
        out = normalize_right(data)
        self.assertEqual(len(out), 2)
        self.assertEqual([s.name for s in out], ["a", "b"])


class TestCompare(unittest.TestCase):

    def test_all_matched(self):
        left = [CaseStatus("a", True), CaseStatus("b", False)]
        right = [CaseStatus("a", True), CaseStatus("b", False)]
        report = compare(left, right)
        self.assertEqual(len(report.matched), 2)
        self.assertEqual(report.mismatched, [])
        self.assertEqual(report.left_only, [])
        self.assertEqual(report.right_only, [])

    def test_mismatch_detected(self):
        left = [CaseStatus("a", True)]
        right = [CaseStatus("a", False)]
        report = compare(left, right)
        self.assertEqual(len(report.mismatched), 1)
        l, r, lp, rp = report.mismatched[0]
        self.assertEqual(l, "a")
        self.assertEqual(r, "a")
        self.assertTrue(lp)
        self.assertFalse(rp)

    def test_left_only(self):
        left = [CaseStatus("a", True), CaseStatus("b", True)]
        right = [CaseStatus("a", True)]
        report = compare(left, right)
        self.assertEqual(report.left_only, ["b"])
        self.assertEqual(report.right_only, [])

    def test_right_only(self):
        left = [CaseStatus("a", True)]
        right = [CaseStatus("a", True), CaseStatus("c", False)]
        report = compare(left, right)
        self.assertEqual(report.right_only, ["c"])

    def test_mapping_translates_left_name(self):
        left = [CaseStatus("scenario_a", True), CaseStatus("scenario_b", True)]
        right = [CaseStatus("multi_4ch_720p", True)]
        # 두 left scenario가 한 plan case에 매핑됨
        mapping = {
            "scenario_a": "multi_4ch_720p",
            "scenario_b": "multi_4ch_720p",
        }
        report = compare(left, right, mapping=mapping)
        # 둘 다 right의 multi_4ch_720p와 매칭되고 passed 일치 → matched
        self.assertEqual(len(report.matched), 2)
        self.assertEqual(report.left_only, [])

    def test_mapping_with_mismatch(self):
        left = [CaseStatus("scenario_a", True)]
        right = [CaseStatus("multi_4ch_720p", False)]
        mapping = {"scenario_a": "multi_4ch_720p"}
        report = compare(left, right, mapping=mapping)
        self.assertEqual(len(report.mismatched), 1)


class TestMainCLI(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write(self, name: str, payload):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as f:
            json.dump(payload, f)
        return path

    def test_main_equivalent_returns_zero(self):
        left = self._write("L.json", [{"name": "a", "result": "PASS"}])
        right = self._write("R.json",
                            {"executions": [{"case_name": "a", "passed": True}]})
        rc = eq_main(["--left", left, "--right", right])
        self.assertEqual(rc, 0)

    def test_main_diverged_returns_one(self):
        left = self._write("L.json", [{"name": "a", "result": "PASS"}])
        right = self._write("R.json",
                            {"executions": [{"case_name": "a", "passed": False}]})
        rc = eq_main(["--left", left, "--right", right])
        self.assertEqual(rc, 1)

    def test_main_missing_file_returns_three(self):
        rc = eq_main(["--left", "/nonexistent.json", "--right", "/nonexistent.json"])
        self.assertEqual(rc, 3)

    def test_main_with_mapping(self):
        left = self._write("L.json", [{"name": "old_a", "result": "PASS"}])
        right = self._write("R.json",
                            {"executions": [{"case_name": "new_a", "passed": True}]})
        mapping_path = self._write("M.json", {"old_a": "new_a"})
        rc = eq_main(["--left", left, "--right", right, "--mapping", mapping_path])
        self.assertEqual(rc, 0)

    def test_main_invalid_left_format(self):
        # plan format을 left로 줌 (list 아닌 dict)
        left = self._write("L.json", {"executions": []})
        right = self._write("R.json", {"executions": []})
        rc = eq_main(["--left", left, "--right", right])
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
