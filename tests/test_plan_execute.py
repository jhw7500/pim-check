"""
tests/test_plan_execute.py — plan.resolve_runtime_profile + plan.execute_plan 단위 테스트.

execute_plan은 ssh_factory / setup_factory / engine_factory를 inject 받아
mock 가능. 실 SSH/타겟 없이 동작 검증.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from plan import (
    Plan,
    DEFAULT_EXECUTION,
    DEFAULT_GATE,
    execute_plan,
    resolve_runtime_profile,
)


class TestResolveRuntimeProfile(unittest.TestCase):
    """4단계 머지 우선순위 — case_profile < plan_global < cli_args."""

    def test_no_overrides_returns_copy(self):
        base = {"target": {"host": "192.168.0.5"}, "monitor": {"duration_sec": 300}}
        result = resolve_runtime_profile(base)
        self.assertEqual(result, base)
        # 원본 보존
        result["target"]["host"] = "changed"
        self.assertEqual(base["target"]["host"], "192.168.0.5")

    def test_cli_args_override_case_profile(self):
        base = {"target": {"host": "192.168.0.5"}, "monitor": {"duration_sec": 300}}
        cli = {"target": {"host": "10.0.0.10"}}
        result = resolve_runtime_profile(base, cli_args=cli)
        self.assertEqual(result["target"]["host"], "10.0.0.10")
        # 다른 키는 보존
        self.assertEqual(result["monitor"]["duration_sec"], 300)

    def test_plan_global_between_case_and_cli(self):
        base = {"monitor": {"duration_sec": 300, "interval_sec": 5}}
        plan_global = {"monitor": {"duration_sec": 60}}
        cli = {"monitor": {"duration_sec": 30}}
        result = resolve_runtime_profile(base, plan_global=plan_global, cli_args=cli)
        # CLI가 가장 우선
        self.assertEqual(result["monitor"]["duration_sec"], 30)
        # plan_global이 case_profile 덮어씀 (CLI 없을 때)
        result2 = resolve_runtime_profile(base, plan_global=plan_global)
        self.assertEqual(result2["monitor"]["duration_sec"], 60)
        # interval은 base 유지
        self.assertEqual(result["monitor"]["interval_sec"], 5)

    def test_nested_dict_merge(self):
        base = {"checks": {"cpu": {"gst_range": [0, 100], "bg_check_max_pct": 3.0}}}
        cli = {"checks": {"cpu": {"gst_range": [50, 95]}}}
        result = resolve_runtime_profile(base, cli_args=cli)
        self.assertEqual(result["checks"]["cpu"]["gst_range"], [50, 95])
        # base의 다른 키 유지
        self.assertEqual(result["checks"]["cpu"]["bg_check_max_pct"], 3.0)

    def test_input_unchanged(self):
        base = {"a": {"b": 1}}
        plan_global = {"a": {"b": 2}}
        result = resolve_runtime_profile(base, plan_global=plan_global)
        self.assertEqual(result["a"]["b"], 2)
        self.assertEqual(base["a"]["b"], 1)
        self.assertEqual(plan_global["a"]["b"], 2)


class TestExecutePlan(unittest.TestCase):
    """execute_plan 동작 — mock SSH/Engine/Setup으로 실 타겟 없이 검증."""

    def setUp(self):
        # 가짜 profiles 디렉토리
        self.tmpdir = tempfile.mkdtemp()
        self.cases_dir = os.path.join(self.tmpdir, "cases")
        os.makedirs(self.cases_dir)

        # base.yaml
        with open(os.path.join(self.tmpdir, "base.yaml"), "w") as f:
            f.write(
                "target:\n  host: 192.168.0.5\n  user: root\n  password: root\n"
                "monitor:\n  duration_sec: 0\n  interval_sec: 5\n"
                "checks:\n  cpu:\n    gst_range: [0, 100]\n"
            )

        # 더미 case 파일들
        for name in ("case_a", "case_b", "case_c"):
            with open(os.path.join(self.cases_dir, f"{name}.yaml"), "w") as f:
                f.write(f"name: {name}\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _plan(self, cases: dict, **execution_overrides) -> Plan:
        """테스트용 Plan 생성."""
        execution = {**DEFAULT_EXECUTION, **execution_overrides}
        return Plan(
            name="test", description="test", version=1,
            cases=cases, execution=execution,
            gate=dict(DEFAULT_GATE), reports=[],
        )

    def _ssh_factory(self, connectivity=True, host_seen=None):
        """check_connectivity가 connectivity 반환하는 mock SshClient."""
        def factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = connectivity
            if host_seen is not None:
                host_seen.append(host)
            return ssh
        return factory

    def _engine_factory(self, results_per_case=None):
        """run_snapshot이 results_per_case[case_name] 반환하는 mock Engine.

        results_per_case = {case_name: list-of-result-dicts}
        """
        def factory(ssh, profile):
            engine = MagicMock()
            # case_name은 profile['name']에서 추출 (case_yaml의 name 필드)
            case_name = profile.get("name", "unknown")
            results = (results_per_case or {}).get(case_name, [
                {"name": "dummy", "passed": True, "reason": "OK", "data": {}, "duration_ms": 1},
            ])
            engine.run_snapshot.return_value = results
            return engine
        return factory

    def _setup_factory(self):
        """run_setup이 항상 False(설정 변경 없음) 반환하는 mock SetupManager."""
        def factory(ssh):
            mgr = MagicMock()
            mgr.run_setup.return_value = False
            return mgr
        return factory

    def test_single_case_pass(self):
        plan = self._plan({"regression": ["case_a"]})
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(),
        )
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0].case_name, "case_a")
        self.assertEqual(execs[0].section, "regression")
        self.assertTrue(execs[0].passed)
        self.assertEqual(execs[0].retries_used, 0)
        self.assertIsNone(execs[0].error)

    def test_multiple_cases_run_in_order(self):
        plan = self._plan({
            "regression": ["case_a", "case_b"],
            "delta": ["case_c"],
        })
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(),
        )
        self.assertEqual(len(execs), 3)
        # regression 먼저, delta 나중
        names_sections = [(e.section, e.case_name) for e in execs]
        self.assertEqual(names_sections, [
            ("regression", "case_a"),
            ("regression", "case_b"),
            ("delta", "case_c"),
        ])

    def test_on_case_start_fires_before_each_case_in_order(self):
        plan = self._plan({
            "regression": ["case_a", "case_b"],
            "delta": ["case_c"],
        })
        starts: list = []
        ends: list = []
        execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(),
            progress=lambda idx, total, name, ex: ends.append(name),
            on_case_start=lambda idx, total, name, section: starts.append((idx, total, name, section)),
        )
        # case_start 가 plan 순서대로, 각 case 당 한 번 호출된다.
        assert [s[2] for s in starts] == ["case_a", "case_b", "case_c"]
        assert starts[0] == (1, 3, "case_a", "regression")
        assert starts[-1] == (3, 3, "case_c", "delta")
        # 각 case 는 start 후 end 가 따른다 (start 가 end 보다 먼저 누적).
        assert ends == ["case_a", "case_b", "case_c"]

    def test_no_ssh_marks_error(self):
        plan = self._plan({"regression": ["case_a"]})
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(connectivity=False),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(),
        )
        self.assertEqual(execs[0].error, "NO_SSH")
        self.assertFalse(execs[0].passed)

    def test_stop_on_fail_aborts(self):
        plan = self._plan(
            {"regression": ["case_a", "case_b", "case_c"]},
            stop_on_fail=True,
        )
        # case_a는 fail
        results_map = {
            "case_a": [{"name": "x", "passed": False, "reason": "F", "data": {}, "duration_ms": 1}],
        }
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(results_map),
        )
        # case_a 한 번 + stop → b/c 실행 안 됨
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0].case_name, "case_a")
        self.assertFalse(execs[0].passed)

    def test_stop_on_fail_continues_when_pass(self):
        plan = self._plan(
            {"regression": ["case_a", "case_b"]},
            stop_on_fail=True,
        )
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(),
        )
        # 모두 pass → 모두 실행
        self.assertEqual(len(execs), 2)

    def test_case_retry_eventually_passes(self):
        """case_retry로 N번째 시도에서 pass하는 시나리오 (실 mock에선 결과 동일하므로
        retry 동작만 카운트로 검증)."""
        plan = self._plan(
            {"regression": ["case_a"]},
            case_retry=2,
        )
        # 항상 fail하는 engine (retry 효과 검증용)
        results_map = {
            "case_a": [{"name": "x", "passed": False, "reason": "F", "data": {}, "duration_ms": 1}],
        }
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(results_map),
        )
        self.assertFalse(execs[0].passed)
        # max_retry+1번 시도 — retries_used = case_retry (마지막 attempt 인덱스)
        self.assertEqual(execs[0].retries_used, 2)

    def test_case_retry_no_retry_when_passes(self):
        plan = self._plan(
            {"regression": ["case_a"]},
            case_retry=2,
        )
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(),
        )
        # 1번에 pass → retries_used = 0
        self.assertEqual(execs[0].retries_used, 0)

    def test_cli_args_override_target_host(self):
        plan = self._plan({"regression": ["case_a"]})
        host_seen: list[str] = []
        cli_args = {"target": {"host": "10.0.0.99"}}
        execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(host_seen=host_seen),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(),
            cli_args=cli_args,
        )
        self.assertEqual(host_seen, ["10.0.0.99"])

    def test_known_issue_pass_through(self):
        """known_issue 매칭된 FAIL은 passed=True로 분류."""
        # case에 known_issue 패턴 추가
        with open(os.path.join(self.cases_dir, "case_known.yaml"), "w") as f:
            f.write(
                "name: case_known\n"
                "known_issues:\n"
                "  - check: thermal\n"
                "    reason_contains: HOT\n"
                "    label: known thermal issue\n"
            )
        plan = self._plan({"regression": ["case_known"]})
        results_map = {
            "case_known": [
                {"name": "thermal", "passed": False, "reason": "HOT 95C",
                 "data": {}, "duration_ms": 1},
            ],
        }
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(results_map),
        )
        # FAIL이지만 known_issue로 분류 → passed=True
        self.assertTrue(execs[0].passed)

    def test_progress_callback_invoked(self):
        plan = self._plan({"regression": ["case_a", "case_b"]})
        seen: list[tuple] = []

        def on_progress(idx, total, case_name, execution):
            seen.append((idx, total, case_name, execution.passed))

        execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=self._setup_factory(),
            engine_factory=self._engine_factory(),
            progress=on_progress,
        )
        self.assertEqual(seen, [(1, 2, "case_a", True), (2, 2, "case_b", True)])

    def test_setup_factory_none_skips_setup(self):
        """setup_factory=None이면 setup 단계 스킵 (mock SSH면 setup 호출 안 됨)."""
        plan = self._plan({"regression": ["case_a"]})
        execs = execute_plan(
            plan, self.tmpdir,
            ssh_factory=self._ssh_factory(),
            setup_factory=None,
            engine_factory=self._engine_factory(),
        )
        self.assertTrue(execs[0].passed)


class TestMonitorCap(unittest.TestCase):
    """plan-level monitor_cap_sec: 긴 모니터를 cap(min)하되 snapshot(0)은 보존."""

    def _effective_duration(self, case_duration, cap):
        from unittest.mock import MagicMock, patch
        from plan import _run_single_case
        ssh = MagicMock()
        ssh.check_connectivity.return_value = True
        profile = {"monitor": {"duration_sec": case_duration}, "checks": {}}
        with patch("verify_retry.run_verify_with_retry") as mock_rvr:
            mock_rvr.return_value = ([{"name": "x", "passed": True, "reason": "OK"}], 1, 1)
            _run_single_case(ssh, profile, "c", lambda s, p: MagicMock(),
                             None, monitor_cap_sec=cap)
            # run_verify_with_retry(engine, ssh, effective_duration, log=...)
            return mock_rvr.call_args[0][2]

    def test_cap_shortens_long_monitor(self):
        self.assertEqual(self._effective_duration(300, 150), 150)

    def test_cap_preserves_snapshot_zero(self):
        # snapshot case(0)은 cap이 있어도 0 유지 (min 의미).
        self.assertEqual(self._effective_duration(0, 150), 0)

    def test_cap_none_leaves_duration_unchanged(self):
        self.assertEqual(self._effective_duration(300, None), 300)

    def test_cap_does_not_increase_short_monitor(self):
        # 이미 cap보다 짧으면 그대로 (cap은 상한이지 하한 아님).
        self.assertEqual(self._effective_duration(100, 150), 100)


if __name__ == "__main__":
    unittest.main()
