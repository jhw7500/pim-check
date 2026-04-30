"""
tests/test_plan_load.py — plan.load_plan / plan.lint_plan 단위 테스트.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from plan import (
    DEFAULT_EXECUTION,
    DEFAULT_GATE,
    SCHEMA_VERSION,
    lint_plan,
    list_plans,
    load_plan,
)


# 최소 valid plan
VALID_PLAN = {
    "name": "Smoke Test",
    "description": "smoke 회귀 보호",
    "version": SCHEMA_VERSION,
    "cases": {"regression": ["720p_2ch"]},
    "gate": {"threshold_pass_rate": 1.0, "allow_known_issue": True},
}


class TestLintPlan(unittest.TestCase):
    """필수 키, 타입, 값 범위, deprecated 키 reject 검증."""

    def test_valid_minimal_plan_passes(self):
        errors = lint_plan(VALID_PLAN)
        self.assertEqual(errors, [], f"unexpected errors: {errors}")

    def test_non_dict_input_returns_error(self):
        errors = lint_plan("not a dict")  # type: ignore[arg-type]
        self.assertEqual(len(errors), 1)
        self.assertIn("dict이어야", errors[0])

    def test_missing_required_keys(self):
        errors = lint_plan({})
        self.assertGreaterEqual(len(errors), 5)
        for key in ("name", "description", "version", "cases", "gate"):
            self.assertTrue(any(key in e for e in errors), f"'{key}' 메시지 누락")

    def test_empty_name_rejected(self):
        plan = {**VALID_PLAN, "name": "  "}
        errors = lint_plan(plan)
        self.assertTrue(any("name" in e for e in errors))

    def test_wrong_version_rejected(self):
        plan = {**VALID_PLAN, "version": 99}
        errors = lint_plan(plan)
        self.assertTrue(any("version" in e and "99" in e for e in errors))

    def test_version_must_be_int(self):
        plan = {**VALID_PLAN, "version": "1"}
        errors = lint_plan(plan)
        self.assertTrue(any("version" in e and "정수" in e for e in errors))

    def test_threshold_out_of_range(self):
        plan = {**VALID_PLAN, "gate": {"threshold_pass_rate": 1.5}}
        errors = lint_plan(plan)
        self.assertTrue(any("threshold_pass_rate" in e for e in errors))

    def test_threshold_negative(self):
        plan = {**VALID_PLAN, "gate": {"threshold_pass_rate": -0.1}}
        errors = lint_plan(plan)
        self.assertTrue(any("threshold_pass_rate" in e for e in errors))

    def test_threshold_non_numeric(self):
        plan = {**VALID_PLAN, "gate": {"threshold_pass_rate": "high"}}
        errors = lint_plan(plan)
        self.assertTrue(any("threshold_pass_rate" in e for e in errors))

    def test_deprecated_gate_mode_rejected(self):
        plan = {**VALID_PLAN, "gate": {"mode": "strict", "threshold_pass_rate": 1.0}}
        errors = lint_plan(plan)
        self.assertTrue(any("gate.mode" in e and "deprecated" in e for e in errors))

    def test_typo_threshold_passes_silently_when_unknown_key(self):
        # "thresold_pass_rate" 같은 오타는 알려지지 않은 키 → lint가 강제 reject 안 함
        # 단 threshold_pass_rate 누락 시 default 1.0이 적용됨 (load 시점에)
        plan = {**VALID_PLAN, "gate": {"thresold_pass_rate": 0.5}}
        errors = lint_plan(plan)
        # 일반 키 추가는 reject 안 함 (확장성). default 1.0 적용 대상.
        self.assertEqual(errors, [])

    def test_unknown_cases_section_rejected(self):
        plan = {**VALID_PLAN, "cases": {"baseline": ["720p_2ch"]}}
        errors = lint_plan(plan)
        self.assertTrue(any("baseline" in e for e in errors))

    def test_empty_cases_rejected(self):
        plan = {**VALID_PLAN, "cases": {"regression": [], "delta": []}}
        errors = lint_plan(plan)
        self.assertTrue(any("비어있지 않은" in e for e in errors))

    def test_tag_selector_rejected_with_helpful_message(self):
        plan = {**VALID_PLAN, "cases": {"regression": [{"tag": "smoke"}]}}
        errors = lint_plan(plan)
        self.assertTrue(any("v1.1" in e for e in errors),
                        f"v1.1 안내 메시지 누락: {errors}")

    def test_dict_selector_rejected_general(self):
        plan = {**VALID_PLAN, "cases": {"regression": [{"foo": "bar"}]}}
        errors = lint_plan(plan)
        self.assertTrue(any("dict selector" in e for e in errors))

    def test_empty_string_selector_rejected(self):
        plan = {**VALID_PLAN, "cases": {"regression": [""]}}
        errors = lint_plan(plan)
        self.assertTrue(any("빈 문자열" in e for e in errors))

    def test_baseline_ref_missing_file(self):
        plan = {
            **VALID_PLAN,
            "gate": {
                "threshold_pass_rate": 1.0,
                "baseline_ref": {"fail_on_new_failure": True},
            },
        }
        errors = lint_plan(plan)
        self.assertTrue(any("baseline_ref.file" in e for e in errors))

    def test_baseline_ref_invalid_policy(self):
        plan = {
            **VALID_PLAN,
            "gate": {
                "threshold_pass_rate": 1.0,
                "baseline_ref": {"file": "x.json", "new_case_policy": "ignore"},
            },
        }
        errors = lint_plan(plan)
        self.assertTrue(any("new_case_policy" in e for e in errors))

    def test_baseline_ref_valid(self):
        plan = {
            **VALID_PLAN,
            "gate": {
                "threshold_pass_rate": 1.0,
                "baseline_ref": {
                    "file": "reports/v1_1.json",
                    "fail_on_new_failure": True,
                    "new_case_policy": "warn",
                },
            },
        }
        errors = lint_plan(plan)
        self.assertEqual(errors, [])

    def test_invalid_report_format_rejected(self):
        plan = {**VALID_PLAN, "reports": [{"format": "fromat_typo", "path": "x"}]}
        errors = lint_plan(plan)
        self.assertTrue(any("format" in e for e in errors))

    def test_report_missing_path_rejected(self):
        plan = {**VALID_PLAN, "reports": [{"format": "json"}]}
        errors = lint_plan(plan)
        self.assertTrue(any("path" in e for e in errors))

    def test_negative_execution_value_rejected(self):
        plan = {**VALID_PLAN, "execution": {"reboot_wait_sec": -10}}
        errors = lint_plan(plan)
        self.assertTrue(any("reboot_wait_sec" in e for e in errors))

    def test_stop_on_fail_must_be_bool(self):
        plan = {**VALID_PLAN, "execution": {"stop_on_fail": "yes"}}
        errors = lint_plan(plan)
        self.assertTrue(any("stop_on_fail" in e for e in errors))


class TestLoadPlan(unittest.TestCase):
    """load_plan 통합 — 디스크 파일 로드, 기본값 채우기, 에러 raise."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.plan_path = os.path.join(self.tmpdir, "smoke.yaml")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write(self, content: str):
        with open(self.plan_path, "w") as f:
            f.write(content)

    def test_load_minimal_plan(self):
        self._write(
            "name: Smoke\n"
            "description: smoke desc\n"
            "version: 1\n"
            "cases:\n"
            "  regression: [720p_2ch]\n"
            "gate:\n"
            "  threshold_pass_rate: 1.0\n"
        )
        plan = load_plan(self.plan_path)
        self.assertEqual(plan.name, "Smoke")
        self.assertEqual(plan.version, 1)
        self.assertEqual(plan.cases["regression"], ["720p_2ch"])

    def test_defaults_applied(self):
        self._write(
            "name: Smoke\n"
            "description: smoke desc\n"
            "version: 1\n"
            "cases:\n"
            "  regression: [720p_2ch]\n"
            "gate: {}\n"
        )
        plan = load_plan(self.plan_path)
        # gate defaults
        self.assertEqual(plan.gate["threshold_pass_rate"],
                         DEFAULT_GATE["threshold_pass_rate"])
        self.assertEqual(plan.gate["allow_known_issue"],
                         DEFAULT_GATE["allow_known_issue"])
        # execution defaults
        for key, val in DEFAULT_EXECUTION.items():
            self.assertEqual(plan.execution[key], val)
        # reports defaults
        self.assertEqual(plan.reports, [])

    def test_user_execution_overrides_default(self):
        self._write(
            "name: Smoke\n"
            "description: smoke desc\n"
            "version: 1\n"
            "cases:\n"
            "  regression: [720p_2ch]\n"
            "gate: {}\n"
            "execution:\n"
            "  reboot_wait_sec: 60\n"
        )
        plan = load_plan(self.plan_path)
        self.assertEqual(plan.execution["reboot_wait_sec"], 60)
        # 다른 키는 default 유지
        self.assertEqual(plan.execution["stop_on_fail"], DEFAULT_EXECUTION["stop_on_fail"])

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_plan(os.path.join(self.tmpdir, "missing.yaml"))

    def test_empty_file_raises(self):
        self._write("")
        with self.assertRaises(ValueError) as ctx:
            load_plan(self.plan_path)
        self.assertIn("비어있", str(ctx.exception))

    def test_invalid_plan_raises_with_all_errors(self):
        self._write(
            "name: ''\n"
            "description: x\n"
            "version: 99\n"
            "cases:\n"
            "  regression: []\n"
            "  delta: []\n"
            "gate:\n"
            "  threshold_pass_rate: 2.0\n"
        )
        with self.assertRaises(ValueError) as ctx:
            load_plan(self.plan_path)
        msg = str(ctx.exception)
        # 여러 에러가 한 메시지에 모여야 함
        self.assertIn("name", msg)
        self.assertIn("99", msg)
        self.assertIn("threshold_pass_rate", msg)
        self.assertIn("비어있지 않은", msg)

    def test_source_path_is_absolute(self):
        self._write(
            "name: Smoke\n"
            "description: smoke desc\n"
            "version: 1\n"
            "cases:\n"
            "  regression: [720p_2ch]\n"
            "gate: {}\n"
        )
        plan = load_plan(self.plan_path)
        self.assertTrue(os.path.isabs(plan.source_path))


class TestListPlans(unittest.TestCase):
    """list_plans — profiles/plans/*.yaml 발견."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.plans_dir = os.path.join(self.tmpdir, "plans")
        os.makedirs(self.plans_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_dir_returns_empty(self):
        self.assertEqual(list_plans(self.tmpdir), [])

    def test_no_plans_dir_returns_empty(self):
        empty = tempfile.mkdtemp()
        try:
            self.assertEqual(list_plans(empty), [])
        finally:
            shutil.rmtree(empty)

    def test_lists_yaml_files_sorted(self):
        for name in ("zebra.yaml", "apple.yaml", "mango.yaml"):
            with open(os.path.join(self.plans_dir, name), "w") as f:
                f.write("name: x\n")
        result = list_plans(self.tmpdir)
        self.assertEqual(result, ["apple", "mango", "zebra"])

    def test_ignores_non_yaml(self):
        with open(os.path.join(self.plans_dir, "smoke.yaml"), "w") as f:
            f.write("name: x")
        with open(os.path.join(self.plans_dir, "smoke.yml"), "w") as f:
            f.write("name: x")
        with open(os.path.join(self.plans_dir, "README.md"), "w") as f:
            f.write("# readme")
        result = list_plans(self.tmpdir)
        self.assertEqual(result, ["smoke"])

    def test_ignores_underscore_prefix(self):
        with open(os.path.join(self.plans_dir, "_template.yaml"), "w") as f:
            f.write("name: x")
        with open(os.path.join(self.plans_dir, "smoke.yaml"), "w") as f:
            f.write("name: x")
        result = list_plans(self.tmpdir)
        self.assertEqual(result, ["smoke"])


if __name__ == "__main__":
    unittest.main()
