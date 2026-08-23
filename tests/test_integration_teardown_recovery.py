"""tests/test_integration_teardown_recovery.py — teardown.recovery_command 가 실제로 실행된다 (pim-check#75).

`run_teardown` 은 `setup:` 섹션에서만 `recovery_command` 를 읽었는데, 이 키를 가진
케이스 2건(`fault_sd_unmounted`, `fault_gstapp_crash`)은 모두 최상위 `teardown:`
아래에 두고 있었다. 그래서 **주입은 되고 복구는 안 되는** 상태가 계속됐다.
`fault_sd_unmounted` 기준으로 `/mnt/sd_cam` 이 언마운트된 채 남는다.

여기 가드는 두 층이다:

1. **단위** — teardown 섹션의 recovery 가 실제로 보드 명령까지 나간다(ssh 캡처).
2. **경로** — 실행 경로 5개(cli / web / stream / parallel / plan)가 각각 그 섹션을
   teardown 으로 전달한다. #67 에서 plan.py 만 다른 매니저를 쓰다 수정이 통째로
   무효화된 전례가 있어, 경로별로 따로 못박는다.

두 층을 나눈 이유: 경로 테스트는 SetupManager 를 mock 하므로 "전달했다" 까지만
보증하고, "전달하면 실행된다" 는 1층이 보증한다. 둘이 합쳐져야 성질이 선다.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from setup import SetupManager


def _mgr():
    mgr = SetupManager(MagicMock(), reboot_timeout=300, poll_interval=10)
    mgr._local0_log = MagicMock()
    mgr.reboot_and_wait = MagicMock()
    mgr.restore = MagicMock()
    mgr.restore_from_snapshot = MagicMock(return_value=True)
    return mgr


def _ssh_commands(mgr) -> list[str]:
    """teardown 이 보드로 내보낸 명령 전부."""
    return [c[0][0] for c in mgr.ssh.run.call_args_list]


class TestTeardownSectionRecovery(unittest.TestCase):
    """1층 — 전달하면 실제로 실행된다."""

    def test_recovery_under_teardown_section_reaches_the_board(self):
        mgr = _mgr()
        mgr.run_teardown(
            {"inject_command": "touch /tmp/fault"},
            {"recovery_command": "mount /dev/mmcblk1p1 /mnt/sd_cam"},
        )
        self.assertIn("mount /dev/mmcblk1p1 /mnt/sd_cam", _ssh_commands(mgr))

    def test_recovery_list_under_teardown_runs_in_order(self):
        mgr = _mgr()
        mgr.run_teardown(
            {"inject_command": "true"},
            {"recovery_command": ["rm -f /tmp/pim_inject_anchor", "mount -a"]},
        )
        cmds = _ssh_commands(mgr)
        self.assertEqual(cmds, ["rm -f /tmp/pim_inject_anchor", "mount -a"])

    def test_teardown_only_recovery_still_triggers_teardown(self):
        """setup 에 아무 변경도 없고 teardown 에만 recovery 가 있어도 조기 반환하면 안 된다."""
        mgr = _mgr()
        mgr.run_teardown({}, {"recovery_command": "mount -a"})
        self.assertIn("mount -a", _ssh_commands(mgr))

    def test_recovery_under_setup_section_still_works(self):
        """하위 호환 — 기존처럼 setup 아래 둔 케이스도 계속 동작한다."""
        mgr = _mgr()
        mgr.run_teardown({"inject_command": "true", "recovery_command": "mount -a"})
        self.assertIn("mount -a", _ssh_commands(mgr))

    def test_teardown_section_wins_when_both_define_recovery(self):
        """둘 다 있으면 이름과 실체가 맞는 teardown 쪽을 쓴다 (중복 실행하지 않는다)."""
        mgr = _mgr()
        mgr.run_teardown(
            {"recovery_command": "echo from-setup"},
            {"recovery_command": "echo from-teardown"},
        )
        cmds = _ssh_commands(mgr)
        self.assertEqual(cmds, ["echo from-teardown"])


# ── 2층 — 실행 경로 5개가 teardown 섹션을 전달하는가 ────────────────────────────

_PROFILE = {
    "target": {},
    "monitor": {"duration_sec": 0},
    "setup": {"inject_command": "touch /tmp/fault"},
    "teardown": {"recovery_command": "mount /dev/mmcblk1p1 /mnt/sd_cam"},
}


def _teardown_saw_recovery(mock_mgr) -> bool:
    """run_teardown 호출 인자 어딘가에 teardown 섹션의 recovery 가 들어왔는가."""
    mock_mgr.run_teardown.assert_called_once()
    args, kwargs = mock_mgr.run_teardown.call_args
    passed = list(args) + list(kwargs.values())
    return any(
        isinstance(a, dict)
        and a.get("recovery_command") == "mount /dev/mmcblk1p1 /mnt/sd_cam"
        for a in passed
    )


def _mock_setup_mgr():
    mgr = MagicMock()
    mgr.run_setup.return_value = True  # setup_changed=True → teardown 진입
    return mgr


def _mock_ssh():
    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    ssh.preflight_check.return_value = []
    return ssh


class TestEveryExecutionPathForwardsTeardown(unittest.TestCase):
    def test_parallel_path(self):
        from parallel import run_on_target
        mgr = _mock_setup_mgr()
        with patch("parallel.SshClient", return_value=_mock_ssh()), \
             patch("parallel.Engine") as MockEngine, \
             patch("parallel.SetupManager", return_value=mgr), \
             patch("parallel.load_profile", return_value=dict(_PROFILE)):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            run_on_target("192.168.0.5", "root", "root", "fault_sd_unmounted", 0)
        self.assertTrue(_teardown_saw_recovery(mgr))

    def test_cli_run_case_path(self):
        from pim_check import run_case
        mgr = _mock_setup_mgr()
        with patch("pim_check.SshClient", return_value=_mock_ssh()), \
             patch("pim_check.Engine") as MockEngine, \
             patch("pim_check.SetupManager", return_value=mgr), \
             patch("pim_check.load_profile", return_value=dict(_PROFILE)):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            run_case("fault_sd_unmounted", "192.168.0.5", "root", "root", 0, quiet=True)
        self.assertTrue(_teardown_saw_recovery(mgr))

    def test_web_path(self):
        import web
        mgr = _mock_setup_mgr()
        with patch("web.SshClient", return_value=_mock_ssh()), \
             patch("web.Engine") as MockEngine, \
             patch("web.SetupManager", return_value=mgr), \
             patch("web.load_profile", return_value=dict(_PROFILE)):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            web._run_test("fault_sd_unmounted", "192.168.0.5", "root", "root", 0)
        self.assertTrue(_teardown_saw_recovery(mgr))

    def test_stream_path(self):
        """#77 해소 후 stream 도 형제 경로들과 같은 inject-only 프로파일을 쓴다.

        (과거에는 stream 의 `if changes:` 가드 때문에 edgeconf 변경을 넣는 우회가
        필요했다 — 주입 실행 자체의 가드는 tests/test_stream.py 의
        TestSetupDelegationToRunSetup 이 진다.)
        """
        from stream import StreamRunner
        mgr = _mock_setup_mgr()
        with patch("stream.SshClient", return_value=_mock_ssh()), \
             patch("stream.Engine") as MockEngine, \
             patch("stream.SetupManager", return_value=mgr), \
             patch("stream.load_profile", return_value=dict(_PROFILE)):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            StreamRunner("fault_sd_unmounted", "192.168.0.5")._run()
        self.assertTrue(_teardown_saw_recovery(mgr))

    # plan 경로는 tmpdir + 케이스 yaml 하네스가 필요해
    # tests/test_plan_execute.py::TestExecutePlanTeardownManager 에 함께 둔다
    # (test_teardown_section_recovery_reaches_the_board).


_PROFILE_TEARDOWN_ONLY = {
    "target": {},
    "monitor": {"duration_sec": 0},
    # setup: 섹션이 아예 없다 — recovery 만 정의한 케이스
    "teardown": {"recovery_command": "mount /dev/mmcblk1p1 /mnt/sd_cam"},
}


class TestTeardownIsReachableWithoutSetup(unittest.TestCase):
    """`run_teardown` 이 지원하는 능력에 **도달할 경로가 있어야** 한다 (pim-check#75 리뷰).

    키를 읽는 쪽을 고쳐도 호출을 막는 가드(`if setup_config and setup_changed:`)가
    그대로면, `setup:` 없이 `teardown.recovery_command` 만 둔 케이스는 `run_teardown`
    자체가 호출되지 않는다. 그러면 "메서드는 된다" 를 단언하는 테스트가 초록인데
    시스템은 조용히 복구하지 않는다 — **#75 와 같은 형태**다(능력은 있는데 도달 못 함).

    오늘 코퍼스에 teardown-only 케이스는 0건이라 실피해는 없지만, 다음 사람이 하나
    쓰는 순간 발현한다.

    ⚠ 함께 지켜야 할 것: `setup_changed` 가 False 인 setup-skip 경로에서는 스냅샷이
    없으므로, 복원까지 돌리면 `.bak` 폴백이 **바꾸지도 않은 설정을 되돌린다.**
    복구는 하되 복원은 하지 않아야 한다.
    """

    def test_cli_path(self):
        from pim_check import run_case
        mgr = _mock_setup_mgr()
        with patch("pim_check.SshClient", return_value=_mock_ssh()), \
             patch("pim_check.Engine") as MockEngine, \
             patch("pim_check.SetupManager", return_value=mgr), \
             patch("pim_check.load_profile", return_value=dict(_PROFILE_TEARDOWN_ONLY)):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            run_case("teardown_only", "192.168.0.5", "root", "root", 0, quiet=True)
        self.assertTrue(_teardown_saw_recovery(mgr))

    def test_parallel_path(self):
        from parallel import run_on_target
        mgr = _mock_setup_mgr()
        with patch("parallel.SshClient", return_value=_mock_ssh()), \
             patch("parallel.Engine") as MockEngine, \
             patch("parallel.SetupManager", return_value=mgr), \
             patch("parallel.load_profile", return_value=dict(_PROFILE_TEARDOWN_ONLY)):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            run_on_target("192.168.0.5", "root", "root", "teardown_only", 0)
        self.assertTrue(_teardown_saw_recovery(mgr))

    def test_web_path(self):
        import web
        mgr = _mock_setup_mgr()
        with patch("web.SshClient", return_value=_mock_ssh()), \
             patch("web.Engine") as MockEngine, \
             patch("web.SetupManager", return_value=mgr), \
             patch("web.load_profile", return_value=dict(_PROFILE_TEARDOWN_ONLY)):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            web._run_test("teardown_only", "192.168.0.5", "root", "root", 0)
        self.assertTrue(_teardown_saw_recovery(mgr))

    def test_stream_path(self):
        from stream import StreamRunner
        mgr = _mock_setup_mgr()
        with patch("stream.SshClient", return_value=_mock_ssh()), \
             patch("stream.Engine") as MockEngine, \
             patch("stream.SetupManager", return_value=mgr), \
             patch("stream.load_profile", return_value=dict(_PROFILE_TEARDOWN_ONLY)):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            StreamRunner("teardown_only", "192.168.0.5")._run()
        self.assertTrue(_teardown_saw_recovery(mgr))

    def test_setup_skip_recovers_without_restoring(self):
        """설정이 이미 일치해 setup 이 아무것도 바꾸지 않았으면 **복원은 하면 안 된다**.

        그 경로는 스냅샷을 찍지 않으므로 복원이 `.bak` 폴백으로 떨어지고, 보드의
        `.bak` 은 config_guard 가 부팅마다 갱신한다 — 바꾸지도 않은 설정을 되돌리는
        셈이다.
        """
        from pim_check import run_case
        mgr = _mock_setup_mgr()
        mgr.run_setup.return_value = False        # setup-skip
        profile = dict(_PROFILE_TEARDOWN_ONLY,
                       setup={"edgeconf_changes": {".VHL_CAM.fps": 30}})
        with patch("pim_check.SshClient", return_value=_mock_ssh()), \
             patch("pim_check.Engine") as MockEngine, \
             patch("pim_check.SetupManager", return_value=mgr), \
             patch("pim_check.load_profile", return_value=profile):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            run_case("skipped", "192.168.0.5", "root", "root", 0, quiet=True)

        self.assertTrue(_teardown_saw_recovery(mgr), "복구가 실행되지 않았다")
        passed_setup_cfg = mgr.run_teardown.call_args[0][0]
        self.assertFalse(
            passed_setup_cfg.get("edgeconf_changes"),
            "setup 이 바꾸지 않았는데 복원 대상을 넘겼다 — .bak 폴백이 돈다")


class TestChangeSetsAreUsedAsFlagsOnly(unittest.TestCase):
    """`run_teardown` 은 `*_changes` 의 **내용**을 쓰지 않고 truthy 여부만 본다.

    plan 의 캠페인 복원(pim-check#68)이 이 성질에 기대고 있다 — 거기서 넘기는 dict 는
    케이스 간 키를 **병합한 것**이라 어느 단일 케이스의 change set 도 아니다. 나중에
    누가 "바뀐 키만 되돌리자" 로 최적화하면 그 병합본으로 조용히 틀린 복원을 한다.
    암묵 결합을 테스트로 못박는다.
    """

    def test_restore_is_driven_by_presence_not_by_contents(self):
        seen = []

        def make(changes):
            mgr = _mgr()
            mgr._restore_conf = MagicMock(side_effect=lambda p: seen.append(p))
            mgr.run_teardown({"edgeconf_changes": changes})
            return [c[0][0] for c in mgr._restore_conf.call_args_list]

        real = make({".VHL_CAM.fps": 30})
        merged = make({".VHL_CAM.fps": 30, ".VHL_CAM.bps": [1024], ".other.key": "x"})
        self.assertEqual(real, merged,
                         "changes 의 내용에 따라 복원 대상이 달라진다 — "
                         "캠페인 병합본을 넘기는 plan 경로가 깨진다")
        self.assertTrue(real, "복원이 아예 일어나지 않았다")


class TestUnknownTeardownKeysWarn(unittest.TestCase):
    """`teardown:` 아래 무시되는 키는 로드 시점에 드러나야 한다 (pim-check#75 (c)).

    이 버그의 본체는 "키를 엉뚱한 섹션에 뒀는데 아무도 말해주지 않았다" 이다.
    recovery_command 는 이제 읽히지만, 같은 착각이 `reboot_after` 등 다른 키로
    재발할 수 있어 로더가 경고한다.
    """

    def setUp(self):
        import shutil
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        import os
        os.makedirs(os.path.join(self.tmpdir, "cases"))
        with open(os.path.join(self.tmpdir, "base.yaml"), "w") as f:
            f.write("target:\n  host: 192.168.0.5\n")

    def _load(self, case_body: str) -> str:
        import contextlib
        import io
        import os
        from config import load_profile
        with open(os.path.join(self.tmpdir, "cases", "c.yaml"), "w") as f:
            f.write(case_body)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            load_profile(self.tmpdir, case="c")
        return buf.getvalue()

    def test_unknown_key_under_teardown_is_reported(self):
        out = self._load("teardown:\n  reboot_after: true\n")
        self.assertIn("reboot_after", out)
        self.assertIn("teardown", out)

    def test_recovery_command_is_not_reported(self):
        out = self._load('teardown:\n  recovery_command: "mount -a"\n')
        self.assertEqual(out, "")

    def test_no_teardown_section_is_silent(self):
        out = self._load("setup:\n  reboot_after: true\n")
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
