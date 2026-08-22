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


class TestMonitorUntilPass(unittest.TestCase):
    """plan-level monitor_until_pass: run_verify_with_retry 로 until_pass 전달."""

    def _until_pass_kwarg(self, flag):
        from unittest.mock import MagicMock, patch
        from plan import _run_single_case
        ssh = MagicMock()
        ssh.check_connectivity.return_value = True
        profile = {"monitor": {"duration_sec": 300}, "checks": {}}
        with patch("verify_retry.run_verify_with_retry") as mock_rvr:
            mock_rvr.return_value = ([{"name": "x", "passed": True, "reason": "OK"}], 1, 1)
            _run_single_case(ssh, profile, "c", lambda s, p: MagicMock(),
                             None, monitor_until_pass=flag)
            return mock_rvr.call_args.kwargs.get("until_pass")

    def test_until_pass_forwarded_true(self):
        self.assertTrue(self._until_pass_kwarg(True))

    def test_until_pass_default_false(self):
        self.assertFalse(self._until_pass_kwarg(False))


if __name__ == "__main__":
    unittest.main()


class TestExecutePlanTeardownManager(unittest.TestCase):
    """plan 의 teardown 이 setup 과 **같은 SetupManager 인스턴스**를 써야 한다.

    회귀 방지 (pim-check#67 리뷰 적발): teardown 이 `setup_factory` 로 새 매니저를
    만들면, 인스턴스 속성에 보관되는 상태(`_config_snapshots` — teardown 복원 원본)가
    빈 채로 시작해 복원이 항상 폴백으로 떨어진다. `--case` 단일 실행·parallel·stream·
    web 은 모두 동일 인스턴스를 쓰는데 plan.py 만 예외였다.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cases_dir = os.path.join(self.tmpdir, "cases")
        os.makedirs(self.cases_dir)
        with open(os.path.join(self.tmpdir, "base.yaml"), "w") as f:
            f.write(
                "target:\n  host: 192.168.0.5\n  user: root\n  password: root\n"
                "monitor:\n  duration_sec: 0\n  interval_sec: 5\n"
            )
        # setup 섹션이 있어야 finally 의 teardown 이 돈다.
        for name in ("case_a", "case_b"):
            with open(os.path.join(self.cases_dir, f"{name}.yaml"), "w") as f:
                f.write(
                    f"name: {name}\n"
                    "setup:\n"
                    "  edgeconf_changes:\n"
                    "    .VHL_CAM.fps: 30\n"
                    "  reboot_after: true\n"
                )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _plan(self, cases: dict, **execution_overrides) -> Plan:
        execution = {**DEFAULT_EXECUTION, **execution_overrides}
        return Plan(
            name="test", description="test", version=1,
            cases=cases, execution=execution,
            gate=dict(DEFAULT_GATE), reports=[],
        )

    def _factories(self):
        created = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True
            return ssh

        def setup_factory(ssh):
            mgr = MagicMock()
            mgr.run_setup.return_value = True
            created.append(mgr)
            return mgr

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "dummy", "passed": True, "reason": "OK",
                 "data": {}, "duration_ms": 1},
            ]
            return engine

        return created, ssh_factory, setup_factory, engine_factory

    def test_teardown_runs_on_the_manager_that_ran_setup(self):
        created, ssh_f, setup_f, engine_f = self._factories()
        plan = self._plan({"regression": ["case_a"]})
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_f,
                     setup_factory=setup_f, engine_factory=engine_f)

        ran_setup = [m for m in created if m.run_setup.called]
        ran_teardown = [m for m in created if m.run_teardown.called]
        self.assertEqual(len(ran_setup), 1, "setup 은 한 번만 돌아야 한다")
        self.assertEqual(len(ran_teardown), 1, "teardown 은 한 번만 돌아야 한다")
        # 핵심 단언 — 같은 객체여야 인스턴스 상태(스냅샷)가 이어진다.
        self.assertIs(ran_teardown[0], ran_setup[0])

    def test_snapshot_taken_at_setup_survives_to_teardown(self):
        """인스턴스 상태가 실제로 이어지는지 — 매니저 동일성의 목적을 직접 확인."""
        created, ssh_f, setup_f, engine_f = self._factories()
        seen = {}

        def setup_factory(ssh):
            mgr = MagicMock()
            mgr._config_snapshots = {}
            # 실제 SetupManager 처럼 setup 때 인스턴스 상태를 채운다.
            def _run_setup(cfg, **kw):
                mgr._config_snapshots["/root/shared_v/edgeconf_pim.json"] = "SNAP"
                return True
            mgr.run_setup.side_effect = _run_setup
            mgr.run_teardown.side_effect = (
                lambda cfg, td=None: seen.update(snapshots=dict(mgr._config_snapshots)))
            created.append(mgr)
            return mgr

        plan = self._plan({"regression": ["case_a"]})
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_f,
                     setup_factory=setup_factory, engine_factory=engine_f)
        self.assertEqual(
            seen.get("snapshots"),
            {"/root/shared_v/edgeconf_pim.json": "SNAP"},
            "teardown 이 setup 에서 찍은 스냅샷을 볼 수 없다 — 별개 인스턴스다")

    def test_last_case_manager_is_used_when_multiple_cases(self):
        """teardown 대상은 **마지막** 케이스의 매니저다(그 케이스 설정이 보드에 남으므로)."""
        created, ssh_f, setup_f, engine_f = self._factories()
        plan = self._plan({"regression": ["case_a", "case_b"]})
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_f,
                     setup_factory=setup_f, engine_factory=engine_f)
        ran_teardown = [m for m in created if m.run_teardown.called]
        self.assertEqual(len(ran_teardown), 1)
        self.assertIs(ran_teardown[0], created[-1])

    def test_teardown_section_recovery_reaches_the_board(self):
        """plan 경로도 `teardown:` 섹션의 recovery_command 를 실행해야 한다 (pim-check#75).

        plan 은 마지막 케이스의 설정을 자기 지역변수로 따로 들고 종료 cleanup 을 돈다.
        그래서 다른 경로를 전부 고쳐도 여기만 누락되는 조합이 실제로 있었다(#67 의
        매니저 분리와 같은 계열). 실제 SetupManager 로 돌려 복구 명령이 보드로
        나가는지를 직접 본다 — 인자 전달만 보면 그 조합을 놓친다.
        """
        from setup import SetupManager

        with open(os.path.join(self.cases_dir, "case_fault.yaml"), "w") as f:
            f.write(
                "name: case_fault\n"
                "setup:\n"
                '  inject_command: "umount -l /mnt/sd_cam"\n'
                "teardown:\n"
                '  recovery_command: "mount /dev/mmcblk1p1 /mnt/sd_cam"\n'
            )

        sent: list[str] = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True

            def run(cmd, *a, **kw):
                sent.append(cmd)
                return "OK"

            ssh.run.side_effect = run
            return ssh

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "dummy", "passed": True, "reason": "OK",
                 "data": {}, "duration_ms": 1},
            ]
            return engine

        plan = self._plan({"regression": ["case_fault"]})
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_factory,
                     setup_factory=SetupManager, engine_factory=engine_factory)

        self.assertIn(
            "mount /dev/mmcblk1p1 /mnt/sd_cam", sent,
            "plan 경로가 teardown: 섹션의 recovery_command 를 실행하지 않았다")

    def test_teardown_only_case_recovers_without_a_setup_section(self):
        """`setup:` 이 없는 케이스도 복구가 도달해야 한다 (pim-check#75 리뷰).

        plan 은 `last_setup_cfg = runtime.get("setup")` 이라 setup 섹션이 없으면
        **None** 이 담긴다. 가드를 `(last_setup_cfg or last_teardown_cfg)` 로 넓혀
        놓고 그 None 을 그대로 넘기면 `run_teardown` 이 `.get()` 에서 죽고,
        `except Exception` 이 삼켜 **가드가 열어준 바로 그 경로가 조용히 죽는다** —
        #75 가 다른 옷을 입은 형태다. 다른 4경로는 인자를 방어하는데 plan 만 빠졌다.
        """
        from setup import SetupManager

        with open(os.path.join(self.cases_dir, "case_td_only.yaml"), "w") as f:
            f.write('name: case_td_only\n'
                    'teardown:\n'
                    '  recovery_command: "mount /dev/mmcblk1p1 /mnt/sd_cam"\n')

        sent: list[str] = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True

            def run(cmd, *a, **kw):
                sent.append(cmd)
                return "OK"

            ssh.run.side_effect = run
            return ssh

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "dummy", "passed": True, "reason": "OK",
                 "data": {}, "duration_ms": 1},
            ]
            return engine

        plan = self._plan({"regression": ["case_td_only"]})
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_factory,
                     setup_factory=SetupManager, engine_factory=engine_factory)

        self.assertIn(
            "mount /dev/mmcblk1p1 /mnt/sd_cam", sent,
            "setup: 없는 케이스의 복구가 plan 경로에서 실행되지 않았다")

    def test_teardown_actually_takes_the_snapshot_path_not_bak_fallback(self):
        """행위 고정 — plan 을 한 바퀴 돌린 뒤 복원이 **스냅샷 경로**를 탔는지 본다.

        객체 동일성(`is`)만 고정하면, 나중에 매니저를 다시 분리했을 때 "같은 객체"
        단언만 우회하고 복원은 죽는 조합이 생길 수 있다. 실제 SetupManager 로
        plan 을 돌려 `.bak` 폴백(`restore`)이 아니라 `restore_from_snapshot` 이
        성공했는지를 직접 확인한다.
        """
        from setup import SetupManager

        calls = {"snapshot_restore": 0, "bak_restore": 0}

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True

            def run(cmd, *a, **kw):
                if cmd.startswith("base64 -w0"):
                    return "eyJhIjogMX0="          # {"a": 1}
                if "base64 -d" in cmd:
                    calls["snapshot_restore"] += 1
                    return "OK"
                if cmd.startswith("cp ") and ".bak" in cmd:
                    calls["bak_restore"] += 1
                    return "OK"
                return "OK"

            ssh.run.side_effect = run
            return ssh

        def setup_factory(ssh):
            mgr = SetupManager(ssh)
            # 보드 왕복이 필요한 부분만 차단 — 스냅샷/복원 경로는 실제 코드로 탄다.
            mgr.check_current = MagicMock(return_value=False)
            mgr.backup = MagicMock(return_value=True)
            mgr.apply_changes = MagicMock()
            mgr.reboot_and_wait = MagicMock()
            mgr._local0_log = MagicMock()
            return mgr

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "dummy", "passed": True, "reason": "OK",
                 "data": {}, "duration_ms": 1},
            ]
            return engine

        plan = self._plan({"regression": ["case_a"]})
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_factory,
                     setup_factory=setup_factory, engine_factory=engine_factory)

        self.assertEqual(calls["snapshot_restore"], 1,
                         "teardown 이 호스트 스냅샷으로 복원하지 않았다")
        self.assertEqual(calls["bak_restore"], 0,
                         "teardown 이 .bak 폴백으로 떨어졌다 — 이 PR 이 없애려던 경로")

    def test_setup_exception_still_tears_down_on_the_same_manager(self):
        """setup 이 예외로 죽어도 teardown 은 **같은 인스턴스**로 시도된다.

        `_run_single_case` 는 `setup_factory` 직후·`run_setup` 이전에 `mgr_holder` 를
        채운다(의도적). 따라서 run_setup 이 던져도 그 매니저가 holder 에 남아
        teardown 이 재사용한다 — 그때까지 찍힌 스냅샷이 있으면 복원에 쓸 수 있고,
        보드 잔재 정리도 건너뛰지 않는다.
        """
        created = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True
            return ssh

        def setup_factory(ssh):
            mgr = MagicMock()
            mgr.run_setup.side_effect = RuntimeError("boom")
            created.append(mgr)
            return mgr

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = []
            return engine

        plan = self._plan({"regression": ["case_a"]}, case_retry=0)
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_factory,
                     setup_factory=setup_factory, engine_factory=engine_factory)
        # setup 이 터져도 teardown cleanup 은 시도돼야 한다(보드 잔재 방지).
        self.assertTrue(any(m.run_teardown.called for m in created))
        # 폴백으로 새로 만들지 않았음을 고정 — factory 는 정확히 한 번만 불린다.
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].run_teardown.called)

    def test_factory_fallback_when_case_never_created_a_manager(self):
        """factory 호출 전에 탈출한 경로(SSH 불통 등)에서는 기존 폴백이 유지된다.

        `_run_single_case` 는 `check_connectivity` 실패 시 setup_factory 를 부르지
        않고 반환하므로 holder 가 비고, finally 는 새 매니저를 만들어 cleanup 한다.
        """
        created = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = False   # factory 호출 전 탈출
            return ssh

        def setup_factory(ssh):
            mgr = MagicMock()
            created.append(mgr)
            return mgr

        def engine_factory(ssh, profile):
            return MagicMock()

        plan = self._plan({"regression": ["case_a"]}, case_retry=0)
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_factory,
                     setup_factory=setup_factory, engine_factory=engine_factory)
        # case 가 매니저를 만들지 않았으므로 finally 가 폴백으로 하나 만든다.
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].run_teardown.called)
        self.assertFalse(created[0].run_setup.called)


class TestPerCaseRecovery(unittest.TestCase):
    """복구(recovery_command)는 **케이스마다**, 복원(edge/ord)은 캠페인 끝에 (pim-check#95).

    plan 은 teardown 을 플랜 끝에서 한 번만 돌렸다. `last_teardown_cfg` 는 매 시도·
    케이스마다 덮어써지므로 **마지막 것만** 실행되고, 앞선 케이스의 fault 는 복구되지
    않은 채 다음 fault 가 주입됐다.

    `fault_injection` 플랜이 정확히 그 형태다 — `fault_gstapp_crash` →
    `fault_sd_unmounted` 순이고 **둘 다 recovery_command 를 갖는다**. 즉 gstApp 이
    죽어 있는 상태에서 SD 언마운트 반응을 재게 된다. `case_retry: 1` 이면 실패한
    첫 시도도 복구 없이 재시도된다.

    두 동작은 주기가 다르다:
      - **복구** — fault 해제. 재부팅을 수반하지 않으므로 케이스마다 즉시.
      - **복원** — edgeconf/ord_vcm. 재부팅이 붙으므로 캠페인 끝에 한 번(#68).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.cases_dir = os.path.join(self.tmpdir, "cases")
        os.makedirs(self.cases_dir)
        with open(os.path.join(self.tmpdir, "base.yaml"), "w") as f:
            f.write("target:\n  host: 192.168.0.5\n  user: root\n  password: root\n"
                    "monitor:\n  duration_sec: 0\n  interval_sec: 5\n")

    def _fault_case(self, name: str, marker: str):
        with open(os.path.join(self.cases_dir, f"{name}.yaml"), "w") as f:
            f.write(f'name: {name}\n'
                    f'setup:\n  inject_command: "inject-{marker}"\n'
                    f'teardown:\n  recovery_command: "recover-{marker}"\n')

    def _plan(self, cases, **execution):
        return Plan(name="t", description="t", version=1, cases=cases,
                    execution={**DEFAULT_EXECUTION, **execution},
                    gate=dict(DEFAULT_GATE), reports=[])

    def _run(self, case_names, *, all_pass=True, **execution):
        """실제 SetupManager 로 돌리고 보드로 나간 명령을 돌려준다."""
        from setup import SetupManager
        sent: list[str] = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True
            ssh.run.side_effect = lambda cmd, *a, **kw: (sent.append(cmd) or "OK")
            return ssh

        def setup_factory(ssh):
            mgr = SetupManager(ssh)
            mgr.reboot_and_wait = MagicMock()
            return mgr

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "d", "passed": all_pass, "reason": "x",
                 "data": {}, "duration_ms": 1},
            ]
            return engine

        execute_plan(self._plan({"regression": case_names}, **execution), self.tmpdir,
                     ssh_factory=ssh_factory, setup_factory=setup_factory,
                     engine_factory=engine_factory)
        return sent

    def test_each_case_is_recovered_before_the_next_one_runs(self):
        self._fault_case("fault_a", "a")
        self._fault_case("fault_b", "b")

        sent = self._run(["fault_a", "fault_b"])

        self.assertIn("recover-a", sent, "첫 케이스의 복구가 실행되지 않았다")
        self.assertIn("recover-b", sent)
        # 순서: a 복구가 b 주입보다 먼저여야 잔존 fault 가 섞이지 않는다
        self.assertLess(sent.index("recover-a"), sent.index("inject-b"),
                        "앞 fault 를 복구하기 전에 다음 fault 를 주입했다")

    def test_failed_attempt_is_recovered_before_retry(self):
        """실패한 시도도 fault 를 남긴다 — 재시도 전에 복구해야 한다."""
        self._fault_case("fault_a", "a")

        sent = self._run(["fault_a"], all_pass=False, case_retry=1)

        self.assertEqual(sent.count("recover-a"), 2,
                         f"시도마다 복구되지 않았다 (실행 {sent.count('recover-a')}회)")

    def test_recovery_is_not_run_twice_for_the_last_case(self):
        """케이스별 복구를 도입했으면 캠페인 teardown 은 복구를 중복 실행하면 안 된다."""
        self._fault_case("fault_a", "a")

        sent = self._run(["fault_a"])

        self.assertEqual(sent.count("recover-a"), 1,
                         f"마지막 케이스 복구가 중복 실행됐다 ({sent.count('recover-a')}회)")

    def test_per_case_recovery_does_not_reboot(self):
        """복구는 fault 해제일 뿐 — 케이스마다 재부팅이 붙으면 플랜 비용이 폭증한다."""
        self._fault_case("fault_a", "a")
        self._fault_case("fault_b", "b")

        sent = self._run(["fault_a", "fault_b"])

        self.assertFalse([c for c in sent if "reboot" in c.lower()],
                         "케이스별 복구가 재부팅을 유발했다")


class TestInterruptedRecoveryFallback(unittest.TestCase):
    """중단으로 케이스별 복구에 도달하지 못하면 finally 가 대신 복구한다 (pim-check#95 리뷰).

    `pim_check.py::_install_graceful_exit_handlers` 는 SIGINT/SIGTERM 을
    `KeyboardInterrupt` 로 바꿔 **finally 의 teardown 이 돌게 하는 것이 목적**이다
    (docstring: "강제 종료 시 보드 conf 잔재로 인한 reboot loop 방지").

    복구를 케이스 단위로 옮기면서 finally 에서 복구를 빼면, `_run_single_case`
    실행 중에 신호가 오는 경우 **주입된 fault 가 보드에 남는다** — 언마운트된 SD,
    죽은 gstApp. 정확히 graceful shutdown 이 막으려던 상황이다.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.cases_dir = os.path.join(self.tmpdir, "cases")
        os.makedirs(self.cases_dir)
        with open(os.path.join(self.tmpdir, "base.yaml"), "w") as f:
            f.write("target:\n  host: 192.168.0.5\n  user: root\n  password: root\n"
                    "monitor:\n  duration_sec: 0\n  interval_sec: 5\n")
        with open(os.path.join(self.cases_dir, "fault_a.yaml"), "w") as f:
            f.write('name: fault_a\n'
                    'setup:\n  inject_command: "inject-a"\n'
                    'teardown:\n  recovery_command: "recover-a"\n')

    def test_recovery_runs_when_the_case_is_interrupted(self):
        from setup import SetupManager
        sent: list[str] = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True
            ssh.run.side_effect = lambda cmd, *a, **kw: (sent.append(cmd) or "OK")
            return ssh

        def setup_factory(ssh):
            mgr = SetupManager(ssh)
            mgr.reboot_and_wait = MagicMock()
            return mgr

        def engine_factory(ssh, profile):
            # 주입은 끝났고 검사 도중 신호가 온 상황
            raise KeyboardInterrupt("SIGINT received — running teardown")

        plan = Plan(name="t", description="t", version=1,
                    cases={"regression": ["fault_a"]},
                    execution=dict(DEFAULT_EXECUTION), gate=dict(DEFAULT_GATE),
                    reports=[])
        with self.assertRaises(KeyboardInterrupt):
            execute_plan(plan, self.tmpdir, ssh_factory=ssh_factory,
                         setup_factory=setup_factory, engine_factory=engine_factory)

        self.assertIn("recover-a", sent,
                      "중단됐는데 복구가 실행되지 않았다 — fault 가 보드에 남는다")

    def test_no_duplicate_recovery_when_the_case_completed(self):
        """정상 종료면 케이스별 복구가 이미 돌았으므로 finally 가 또 하면 안 된다."""
        from setup import SetupManager
        sent: list[str] = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True
            ssh.run.side_effect = lambda cmd, *a, **kw: (sent.append(cmd) or "OK")
            return ssh

        def setup_factory(ssh):
            mgr = SetupManager(ssh)
            mgr.reboot_and_wait = MagicMock()
            return mgr

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "d", "passed": True, "reason": "OK", "data": {}, "duration_ms": 1},
            ]
            return engine

        plan = Plan(name="t", description="t", version=1,
                    cases={"regression": ["fault_a"]},
                    execution=dict(DEFAULT_EXECUTION), gate=dict(DEFAULT_GATE),
                    reports=[])
        execute_plan(plan, self.tmpdir, ssh_factory=ssh_factory,
                     setup_factory=setup_factory, engine_factory=engine_factory)

        self.assertEqual(sent.count("recover-a"), 1,
                         f"복구가 중복 실행됐다 ({sent.count('recover-a')}회)")
