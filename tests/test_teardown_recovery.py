"""tests/test_teardown_recovery.py — teardown.recovery_command 가 실제로 실행된다 (pim-check#75).

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
        """stream 경로는 edgeconf 변경이 있는 케이스로만 teardown 에 도달한다.

        stream 은 `edgeconf_changes` 가 있을 때만 `run_setup` 을 부르기 때문에
        inject-only fault 케이스는 주입도 복구도 하지 않는다 — **이 경로만의 별개
        결함**이고 #75 범위 밖이라 여기서는 고치지 않는다(별도 이슈). 그래서 이
        가드는 teardown 에 도달하는 조합으로 "그 경로가 teardown 섹션을 전달하는가"
        만 못박는다.
        """
        from stream import StreamRunner
        mgr = _mock_setup_mgr()
        mgr.check_current.return_value = False   # 설정이 다르다 → run_setup 진입
        profile = dict(_PROFILE, setup={"edgeconf_changes": {".VHL_CAM.fps": 30}})
        with patch("stream.SshClient", return_value=_mock_ssh()), \
             patch("stream.Engine") as MockEngine, \
             patch("stream.SetupManager", return_value=mgr), \
             patch("stream.load_profile", return_value=profile):
            MockEngine.return_value.run_snapshot.return_value = [
                {"name": "cpu", "passed": True, "reason": "OK"},
            ]
            StreamRunner("fault_sd_unmounted", "192.168.0.5")._run()
        self.assertTrue(_teardown_saw_recovery(mgr))

    # plan 경로는 tmpdir + 케이스 yaml 하네스가 필요해
    # tests/test_plan_execute.py::TestExecutePlanTeardownManager 에 함께 둔다
    # (test_teardown_section_recovery_reaches_the_board).


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
