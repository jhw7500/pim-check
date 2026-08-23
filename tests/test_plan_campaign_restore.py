"""tests/test_plan_campaign_restore.py — plan 은 캠페인 시작 전 상태로 복원한다 (pim-check#68).

`plan.py` 는 케이스마다 teardown 하지 않고 **플랜 끝에서 한 번만** 정리한다. 그래서
복원 도달점이 "플랜 시작 전"이 아니라 **"마지막 케이스 직전"** 이었다:

    시작    보드 = X
    case_a  A 적용   (스냅샷 = X)
    case_b  B 적용   (스냅샷 = A)
    case_c  C 적용   (스냅샷 = B)   ← 마지막 케이스
    finally teardown → 보드 = B     ← X 가 아니다

25 케이스짜리 `comprehensive` 를 돌리면 보드는 24번째 케이스 설정을 안고 끝나고,
다음 플랜·수동 검사의 출발점이 그만큼 비결정적이 된다. `edgeconf_changes` 를 가진
케이스 27개가 쓰는 키 62개 중 **모든 케이스가 공통으로 설정하는 키는 0개**라, 남는
상태는 "마지막에서 두 번째 케이스가 무엇이었나"에 따라 달라진다.

기존 `.bak` 방식으로는 불가능한 얘기였다(보드의 단일 슬롯). #67 의 호스트 스냅샷은
호스트가 dict 로 들고 있으므로 **최초 스냅샷을 캠페인 내내 유지할 수 있다.**

복원 **대상**도 캠페인 기준이어야 한다 — `ord_vcm_changes` 를 쓰는 케이스가 6건
있으므로, 중간 케이스가 ord_vcm 을 바꾸고 마지막 케이스가 edgeconf 만 바꾸면
ord_vcm 이 되돌려지지 않는다.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from plan import DEFAULT_EXECUTION, DEFAULT_GATE, Plan, execute_plan
from setup import EDGECONF_PATH, ORD_VCM_PATH, SetupManager


class TestCampaignWideRestore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.cases_dir = os.path.join(self.tmpdir, "cases")
        os.makedirs(self.cases_dir)
        with open(os.path.join(self.tmpdir, "base.yaml"), "w") as f:
            f.write(
                "target:\n  host: 192.168.0.5\n  user: root\n  password: root\n"
                "monitor:\n  duration_sec: 0\n  interval_sec: 5\n"
            )

    def _case(self, name: str, body: str):
        with open(os.path.join(self.cases_dir, f"{name}.yaml"), "w") as f:
            f.write(f"name: {name}\nsetup:\n{body}")

    def _plan(self, cases: dict) -> Plan:
        return Plan(name="t", description="t", version=1, cases=cases,
                    execution=dict(DEFAULT_EXECUTION), gate=dict(DEFAULT_GATE),
                    reports=[])

    def _run(self, case_names: list[str]):
        """플랜을 실제 SetupManager 로 돌리고, 보드로 나간 복원 페이로드를 돌려준다.

        보드의 현재 설정은 케이스가 적용할 때마다 바뀐다고 보고, `base64 -w0` 읽기에
        그 시점 값을 돌려준다 — 스냅샷이 **언제 찍혔는지**가 복원 결과로 드러난다.
        """
        # 스냅샷은 저장 전에 실제로 디코드해 **JSON 파싱까지** 검증한다 — 값이 JSON 이어야 한다.
        state = {EDGECONF_PATH: '{"state": "STATE0-EDGE"}',
                 ORD_VCM_PATH: '{"state": "STATE0-ORD"}'}
        applied: list[str] = []
        restored: list[tuple[str, str]] = []

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True

            def run(cmd, *a, **kw):
                import base64
                import re
                for path in (EDGECONF_PATH, ORD_VCM_PATH):
                    if cmd.startswith("base64 -w0") and path in cmd:
                        # 스냅샷 읽기 — 그 시점의 보드 상태
                        return base64.b64encode(state[path].encode()).decode()
                    if "base64 -d" in cmd and path in cmd:
                        # 복원 쓰기 — 실제 페이로드는 `printf '%s' '<b64>'` 로 실린다
                        m = re.search(r"printf '%s' '([^']+)'", cmd)
                        if m:
                            restored.append((path, base64.b64decode(m.group(1)).decode()))
                        return "OK"
                if cmd.lstrip().startswith("jq "):   # apply_changes — 설정이 바뀐다
                    for path in (EDGECONF_PATH, ORD_VCM_PATH):
                        if path in cmd:
                            applied.append(path)
                            state[path] = f'{{"state": "STATE{len(applied)}"}}'
                return "OK"

            ssh.run.side_effect = run
            return ssh

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "d", "passed": True, "reason": "OK", "data": {}, "duration_ms": 1},
            ]
            return engine

        def setup_factory(ssh):
            mgr = SetupManager(ssh)
            mgr.check_current = MagicMock(return_value=False)   # 항상 적용해야 함
            mgr.backup = MagicMock(return_value=True)
            mgr.reboot_and_wait = MagicMock()
            return mgr

        execute_plan(self._plan({"regression": case_names}), self.tmpdir,
                     ssh_factory=ssh_factory, setup_factory=setup_factory,
                     engine_factory=engine_factory)
        return restored

    def test_restores_to_the_state_before_the_first_case(self):
        for name, fps in (("case_a", 15), ("case_b", 20), ("case_c", 30)):
            self._case(name, f"  edgeconf_changes:\n    .VHL_CAM.fps: {fps}\n")

        restored = self._run(["case_a", "case_b", "case_c"])

        self.assertTrue(restored, "복원이 아예 일어나지 않았다")
        payloads = [p for path, p in restored if path == EDGECONF_PATH]
        self.assertEqual(
            payloads, ['{"state": "STATE0-EDGE"}'],
            "캠페인 시작 전 상태가 아니라 마지막 케이스 직전 상태로 되돌렸다")

    def test_restore_runs_even_when_the_last_case_has_no_setup(self):
        """캠페인 복원 **진입 조건**도 캠페인 기준이어야 한다 (보드 실측으로 드러남).

        `smoke` 플랜은 `config_integrity`(setup 섹션 없음)로 끝난다. 진입 조건이
        `last_setup_cfg or last_teardown_cfg` — 즉 **마지막 케이스** 기준이라, 앞선
        6개 케이스가 설정을 바꿨는데도 복원이 통째로 건너뛰어졌다.

        보드 로그가 그대로 보여줬다 — 플랜 종료 시각 이후 `PIM_CHECK` 항목이 하나도
        없고, `edgeconf` 는 캠페인 시작 전 값이 아니라 마지막으로 설정을 바꾼
        케이스의 값으로 남았다.
        """
        self._case("case_cfg", "  edgeconf_changes:\n    .VHL_CAM.fps: 30\n"
                               "  reboot_after: true\n")
        # 마지막 케이스는 setup 도 teardown 도 없다 (config_integrity 형태)
        with open(os.path.join(self.cases_dir, "case_readonly.yaml"), "w") as f:
            f.write("name: case_readonly\n")

        restored = self._run(["case_cfg", "case_readonly"])

        self.assertTrue(
            restored,
            "마지막 케이스에 setup 이 없다는 이유로 캠페인 복원이 건너뛰어졌다")
        self.assertEqual(dict(restored)[EDGECONF_PATH], '{"state": "STATE0-EDGE"}')

    def test_no_snapshot_means_no_restore_and_no_reboot(self):
        """복원할 원본이 없으면 복원도 재부팅도 하지 않는다.

        모든 케이스가 setup-skip 이면(설정이 이미 일치) `changes` 는 누적되지만
        스냅샷은 찍히지 않는다. 그 상태로 복원을 돌리면 보드 `.bak` 폴백으로 떨어져
        **바꾸지도 않은 설정을 되돌리고**, `reboot_after` 까지 붙어 재부팅 한 번을
        낭비한다. `.bak` 은 config_guard 가 부팅마다 갱신하는 자리라 무엇으로
        되돌아갈지도 보장이 없다.

        #75 리뷰에서 4경로에 적용한 것과 같은 논리다 — 복원과 복구를 가르고,
        복원은 되돌릴 원본이 있을 때만 한다.
        """
        self._case("case_skip", "  edgeconf_changes:\n    .VHL_CAM.fps: 30\n"
                                "  reboot_after: true\n")

        created: list = []
        bak_restores: list[str] = []

        def setup_factory(ssh):
            mgr = SetupManager(ssh)
            mgr.check_current = MagicMock(return_value=True)   # 이미 일치 → setup-skip
            mgr.backup = MagicMock(return_value=True)
            mgr.reboot_and_wait = MagicMock()
            mgr.restore = MagicMock(side_effect=lambda p: bak_restores.append(p))
            created.append(mgr)
            return mgr

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True
            ssh.run.side_effect = lambda cmd, *a, **kw: "OK"
            return ssh

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "d", "passed": True, "reason": "OK", "data": {}, "duration_ms": 1},
            ]
            return engine

        execute_plan(self._plan({"regression": ["case_skip"]}), self.tmpdir,
                     ssh_factory=ssh_factory, setup_factory=setup_factory,
                     engine_factory=engine_factory)

        teardown_mgr = created[-1]
        self.assertEqual(teardown_mgr._config_snapshots, {}, "전제: 스냅샷이 없어야 한다")
        self.assertEqual(bak_restores, [],
                         "스냅샷이 없는데 보드 .bak 으로 되돌렸다")
        self.assertFalse(teardown_mgr.reboot_and_wait.called,
                         "되돌릴 것이 없는데 재부팅했다")

    def test_campaign_reboot_is_decided_by_the_campaign_not_the_last_case(self):
        """마지막 케이스가 fault 케이스면 `reboot_after` 가 없다 — 그렇다고 복원이
        파일만 되돌리고 재부팅을 건너뛰면 보드는 앞 케이스 설정으로 계속 돈다.

        "복원을 적용하러 재부팅할 것인가" 는 캠페인 단위 결정이다.
        """
        self._case("case_cfg", "  edgeconf_changes:\n    .VHL_CAM.fps: 30\n"
                               "  reboot_after: true\n")
        self._case("case_fault", '  inject_command: "true"\n')   # reboot_after 없음

        created: list = []

        def setup_factory(ssh):
            mgr = SetupManager(ssh)
            mgr.check_current = MagicMock(return_value=False)
            mgr.backup = MagicMock(return_value=True)
            mgr.reboot_and_wait = MagicMock()
            created.append(mgr)
            return mgr

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True
            ssh.run.side_effect = lambda cmd, *a, **kw: (
                "eyJhIjogMX0=" if cmd.startswith("base64 -w0") else "OK")
            return ssh

        def engine_factory(ssh, profile):
            engine = MagicMock()
            engine.run_snapshot.return_value = [
                {"name": "d", "passed": True, "reason": "OK", "data": {}, "duration_ms": 1},
            ]
            return engine

        execute_plan(self._plan({"regression": ["case_cfg", "case_fault"]}), self.tmpdir,
                     ssh_factory=ssh_factory, setup_factory=setup_factory,
                     engine_factory=engine_factory)

        # 총 재부팅 횟수가 아니라 **teardown 을 돌린 매니저**를 본다 — plan 경로는
        # setup 재부팅을 따로 하지 않으므로 총계로 세면 전제가 어긋난다(실측으로 확인).
        teardown_mgr = created[-1]
        self.assertTrue(
            teardown_mgr.reboot_and_wait.called,
            "캠페인이 설정을 바꿨는데 복원이 재부팅 없이 끝났다 — "
            "마지막 fault 케이스의 reboot_after 부재를 그대로 물려받았다")

    def test_restores_a_file_only_an_earlier_case_touched(self):
        """중간 케이스가 ord_vcm 을 바꾸고 마지막이 edgeconf 만 바꿔도 둘 다 되돌아와야 한다."""
        self._case("case_ord", "  ord_vcm_changes:\n    .ord.vcm: 1\n")
        self._case("case_edge", "  edgeconf_changes:\n    .VHL_CAM.fps: 30\n")

        restored = self._run(["case_ord", "case_edge"])

        paths = {path for path, _ in restored}
        self.assertIn(ORD_VCM_PATH, paths,
                      "앞 케이스가 건드린 ord_vcm 이 복원되지 않았다")
        self.assertIn(EDGECONF_PATH, paths)
        self.assertEqual(dict(restored)[ORD_VCM_PATH], '{"state": "STATE0-ORD"}')


class TestBaselineSuspectSurfacing(unittest.TestCase):
    """#82 — 채택 전 스냅샷 실패는 기준선을 밀고, 복원은 성공처럼 보인다.

    복원 동작은 바꾸지 않는다(가장 이른 가용 스냅샷으로 최선 복원). 바꾸는 것은
    가시성이다 — 실패를 아는 곳(setup, `_snapshot_failures`)이 기록하고, 그 영향을
    아는 곳(plan)이 채택 시점과 대조해 캠페인 끝에 BASELINE SUSPECT 를 정상 복원
    로그와 **다른 모양으로** 한 번 더 알린다. 채택 이후의 실패는 기준선에 무해하므로
    조용해야 한다 — 헛경보는 경보를 죽인다.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.cases_dir = os.path.join(self.tmpdir, "cases")
        os.makedirs(self.cases_dir)
        with open(os.path.join(self.tmpdir, "base.yaml"), "w") as f:
            f.write(
                "target:\n  host: 192.168.0.5\n  user: root\n  password: root\n"
                "monitor:\n  duration_sec: 0\n  interval_sec: 5\n"
            )

    def _case(self, name: str, body: str):
        with open(os.path.join(self.cases_dir, f"{name}.yaml"), "w") as f:
            f.write(f"name: {name}\nsetup:\n{body}")

    def _plan(self, cases: dict) -> Plan:
        return Plan(name="t", description="t", version=1, cases=cases,
                    execution=dict(DEFAULT_EXECUTION), gate=dict(DEFAULT_GATE),
                    reports=[])

    def _run(self, case_names: list[str], fail_edge_snapshot_read: int,
             interrupt_in_engine: bool = False):
        """`fail_edge_snapshot_read` 번째 edgeconf 스냅샷 읽기만 실패시키고 돌린다.

        반환: (restored, board_cmds, stdout, snap_ok) — suspect 경고는 stdout
        (운영자)과 local0(ssh 로 나가는 logger 명령) 양쪽에서 관찰하고,
        snap_ok 는 **성공한** edge 스냅샷 읽기가 그 순간 본 보드 상태다
        (기대 복원값은 이 캡처와 대조한다 — 케이스당 jq 호출 수·retry 에 무관).
        """
        state = {EDGECONF_PATH: '{"state": "STATE0-EDGE"}',
                 ORD_VCM_PATH: '{"state": "STATE0-ORD"}'}
        applied: list[str] = []
        restored: list[tuple[str, str]] = []
        board_cmds: list[str] = []
        snap_reads = {"n": 0}
        snap_ok: list[str] = []
        written: dict[str, str] = {}   # read-back verify 가 돌려줄 마지막 쓴 값

        def ssh_factory(host, user, password):
            ssh = MagicMock()
            ssh.check_connectivity.return_value = True

            def run(cmd, *a, **kw):
                import base64
                import re
                board_cmds.append(cmd)
                for path in (EDGECONF_PATH, ORD_VCM_PATH):
                    if cmd.startswith("base64 -w0") and path in cmd:
                        if path == EDGECONF_PATH:
                            snap_reads["n"] += 1
                            if snap_reads["n"] == fail_edge_snapshot_read:
                                return ""   # 빈 출력 = snapshot_config 실패
                            snap_ok.append(state[path])
                        return base64.b64encode(state[path].encode()).decode()
                    if "base64 -d" in cmd and path in cmd:
                        m = re.search(r"printf '%s' '([^']+)'", cmd)
                        if m:
                            restored.append(
                                (path, base64.b64decode(m.group(1)).decode()))
                        return "OK"
                if cmd.lstrip().startswith("jq "):
                    if "&& mv" in cmd:   # apply 쓰기 — 보드 상태가 바뀐다
                        for path in (EDGECONF_PATH, ORD_VCM_PATH):
                            if path in cmd:
                                applied.append(path)
                                state[path] = f'{{"state": "STATE{len(applied)}"}}'
                                m = re.search(r"--argjson v (\S+) ", cmd)
                                if m:
                                    written[path] = m.group(1)
                        return "OK"
                    # read-back verify — 읽기는 변이하지 않고 방금 쓴 값을 본다
                    for path in (EDGECONF_PATH, ORD_VCM_PATH):
                        if path in cmd:
                            return written.get(path, "OK")
                return "OK"

            ssh.run.side_effect = run
            return ssh

        def engine_factory(ssh, profile):
            engine = MagicMock()
            if interrupt_in_engine:
                engine.run_snapshot.side_effect = KeyboardInterrupt()
            else:
                engine.run_snapshot.return_value = [
                    {"name": "d", "passed": True, "reason": "OK", "data": {},
                     "duration_ms": 1},
                ]
            return engine

        def setup_factory(ssh):
            mgr = SetupManager(ssh)
            mgr.check_current = MagicMock(return_value=False)
            mgr.backup = MagicMock(return_value=True)
            mgr.reboot_and_wait = MagicMock()
            return mgr

        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                execute_plan(self._plan({"regression": case_names}), self.tmpdir,
                             ssh_factory=ssh_factory, setup_factory=setup_factory,
                             engine_factory=engine_factory)
            except KeyboardInterrupt:
                pass    # graceful shutdown 경로 — finally 정리는 이미 돌았다
        return restored, board_cmds, out.getvalue(), snap_ok

    def test_failure_before_adoption_is_surfaced(self):
        """1케이스 실패 → 이후 스냅샷 채택 — 기준선은 이미 변경된 상태다."""
        self._case("case_a", "  edgeconf_changes:\n    .VHL_CAM.fps: 15\n")
        self._case("case_b", "  edgeconf_changes:\n    .VHL_CAM.fps: 30\n")

        restored, cmds, out, snap_ok = self._run(["case_a", "case_b"],
                                                 fail_edge_snapshot_read=1)

        # 복원은 여전히 최선(가장 이른 **가용** 스냅샷 = 첫 성공 읽기)으로 돈다 —
        # 다만 그 값은 캠페인 시작 전(STATE0)이 아니라 case_a 가 이미 변경한 뒤다.
        # 이 결함이 조용히 지나가지 않는 것이 이 테스트의 요점이다.
        payloads = [p for path, p in restored if path == EDGECONF_PATH]
        self.assertTrue(snap_ok, "성공한 스냅샷이 없다 — 시나리오가 깨졌다")
        self.assertEqual(payloads, [snap_ok[0]])
        self.assertNotEqual(snap_ok[0], '{"state": "STATE0-EDGE"}',
                            "기준선이 밀리지 않았다 — 시나리오가 깨졌다")
        self.assertIn("campaign baseline suspect", out)
        self.assertIn("case_a", out, "어느 케이스에서 실패했는지 알려주지 않는다")
        self.assertTrue(any("BASELINE SUSPECT" in c for c in cmds),
                        "local0 로그에 suspect 가 남지 않았다")

    def test_failure_after_adoption_is_harmless_and_silent(self):
        """기준선 채택 뒤의 실패는 캠페인 복원에 무해하다 — 경보를 울리면 안 된다."""
        self._case("case_a", "  edgeconf_changes:\n    .VHL_CAM.fps: 15\n")
        self._case("case_b", "  edgeconf_changes:\n    .VHL_CAM.fps: 30\n")

        restored, cmds, out, snap_ok = self._run(["case_a", "case_b"],
                                                 fail_edge_snapshot_read=2)

        payloads = [p for path, p in restored if path == EDGECONF_PATH]
        self.assertEqual(snap_ok[0], '{"state": "STATE0-EDGE"}')
        self.assertEqual(payloads, ['{"state": "STATE0-EDGE"}'],
                         "기준선이 무사한데 복원이 달라졌다")
        self.assertNotIn("campaign baseline suspect", out)
        self.assertFalse(any("BASELINE SUSPECT" in c for c in cmds))

    def test_interrupt_after_failed_snapshot_still_surfaces(self):
        """#100 Codex P2 — SIGINT 가 케이스 실행 도중에 오면(KeyboardInterrupt)
        루프의 수확 블록에 도달하지 못한다. finally 가 마지막 시도의 매니저를
        회수해야 중단 시에도 suspect 가 침묵하지 않는다."""
        self._case("case_a", "  edgeconf_changes:\n    .VHL_CAM.fps: 15\n")

        restored, cmds, out, _snap_ok = self._run(
            ["case_a"], fail_edge_snapshot_read=1, interrupt_in_engine=True)

        self.assertIn("campaign baseline suspect", out,
                      "중단 경로에서 suspect 가 침묵한다")
        self.assertTrue(any("BASELINE SUSPECT" in c for c in cmds))

    def test_never_adopted_failure_is_also_surfaced(self):
        """실패한 파일을 이후 아무 케이스도 스냅샷하지 못하면 복원 자체가 빠진다 —
        이것도 같은 suspect 로 알린다."""
        self._case("case_a", "  edgeconf_changes:\n    .VHL_CAM.fps: 15\n")

        restored, cmds, out, _snap_ok = self._run(
            ["case_a"], fail_edge_snapshot_read=1)

        self.assertEqual([p for path, p in restored if path == EDGECONF_PATH], [],
                         "스냅샷 없는 파일이 복원됐다(.bak 폴백이 아니어야 한다)")
        self.assertIn("campaign baseline suspect", out)
        self.assertTrue(any("BASELINE SUSPECT" in c for c in cmds))


if __name__ == "__main__":
    unittest.main()
