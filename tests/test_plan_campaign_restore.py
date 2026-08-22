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


if __name__ == "__main__":
    unittest.main()
