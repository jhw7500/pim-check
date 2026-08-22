"""stream.py 테스트"""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from stream import StreamRunner, format_sse


class TestFormatSse(unittest.TestCase):
    def test_basic_format(self):
        result = format_sse("check_result", {"check": "thermal", "passed": True})
        self.assertTrue(result.startswith("event: check_result\n"))
        self.assertIn("data: ", result)
        self.assertTrue(result.endswith("\n\n"))

    def test_unicode(self):
        result = format_sse("phase", {"message": "연결 중..."})
        self.assertIn("연결 중...", result)


class TestStreamRunner(unittest.TestCase):
    def test_emit_puts_to_queue(self):
        runner = StreamRunner("test", "192.168.0.5", profiles_dir="profiles")
        runner._emit("test_event", {"key": "value"})
        event = runner.events.get(timeout=1)
        self.assertEqual(event["event"], "test_event")
        self.assertEqual(event["data"]["key"], "value")
        self.assertIn("timestamp", event["data"])

    @patch("stream.load_profile")
    @patch("stream.SshClient")
    def test_unreachable_emits_error_done(self, mock_ssh_cls, mock_load):
        mock_load.return_value = {
            "target": {"host": "192.168.99.99", "user": "root", "password": "root"},
            "monitor": {"duration_sec": 0},
        }
        mock_ssh = MagicMock()
        mock_ssh.check_connectivity.return_value = False
        mock_ssh_cls.return_value = mock_ssh

        runner = StreamRunner("test", "192.168.99.99", profiles_dir="profiles")
        runner._run()

        events = []
        while not runner.events.empty():
            events.append(runner.events.get())

        event_types = [e["event"] for e in events]
        self.assertIn("start", event_types)
        self.assertIn("error", event_types)
        self.assertIn("done", event_types)
        done = next(e for e in events if e["event"] == "done")
        self.assertEqual(done["data"]["status"], "ERROR")

    @patch("stream.load_profile")
    @patch("stream.SshClient")
    @patch("stream.Engine")
    @patch("stream.SetupManager")
    @patch("stream.append_result")
    @patch("stream.save_dashboard")
    def test_success_emits_all_phases(self, mock_save, mock_append,
                                      mock_setup_cls, mock_engine_cls,
                                      mock_ssh_cls, mock_load):
        mock_load.return_value = {
            "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
            "monitor": {"duration_sec": 0},
            "checks": {},
        }
        mock_ssh = MagicMock()
        mock_ssh.check_connectivity.return_value = True
        mock_ssh.preflight_check.return_value = []
        mock_ssh_cls.return_value = mock_ssh

        mock_engine = MagicMock()
        mock_check = MagicMock()
        mock_check.name = "test_check"
        mock_check.collect.return_value = {}
        mock_check.validate.return_value = (True, "OK")
        mock_engine.checks = [mock_check]
        mock_engine_cls.return_value = mock_engine

        runner = StreamRunner("test", "192.168.0.5",
                              profiles_dir="profiles", reports_dir="/tmp")
        runner._run()

        events = []
        while not runner.events.empty():
            events.append(runner.events.get())

        event_types = [e["event"] for e in events]
        self.assertIn("start", event_types)
        self.assertIn("phase", event_types)
        self.assertIn("check_start", event_types)
        self.assertIn("check_result", event_types)
        self.assertIn("done", event_types)

        done = next(e for e in events if e["event"] == "done")
        self.assertEqual(done["data"]["status"], "PASS")

        check_result = next(e for e in events if e["event"] == "check_result")
        self.assertTrue(check_result["data"]["passed"])
        self.assertIn("duration_ms", check_result["data"])


class TestSetupDelegationToRunSetup(unittest.TestCase):
    """#77 — 실행 결정은 다른 4개 경로처럼 run_setup 이 한다.

    stream 이 자체 `if changes:` 가드로 케이스를 거르면 inject-only/ord-only
    케이스는 주입 없이 "결함 없는 보드" 를 검사해 무의미하게 PASS 한다.
    edgeconf 변경 유무는 phase 메시지 분기에만 쓴다.
    """

    def _run(self, profile, mgr):
        with patch("stream.load_profile", return_value=profile), \
             patch("stream.SshClient") as mock_ssh_cls, \
             patch("stream.Engine") as mock_engine_cls, \
             patch("stream.SetupManager", return_value=mgr):
            mock_ssh = MagicMock()
            mock_ssh.check_connectivity.return_value = True
            mock_ssh.preflight_check.return_value = []
            mock_ssh_cls.return_value = mock_ssh
            mock_engine_cls.return_value.checks = []
            runner = StreamRunner("case", "192.168.0.5")
            runner._run()
        events = []
        while not runner.events.empty():
            events.append(runner.events.get())
        return events

    @staticmethod
    def _phase_messages(events):
        return [e["data"].get("message", "") for e in events if e["event"] == "phase"]

    def test_inject_only_setup_reaches_run_setup(self):
        """edgeconf 변경 없이 inject 만 있어도 주입이 실행된다."""
        setup = {"inject_command": "umount -l /mnt/sd_cam"}
        profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
                   "setup": setup,
                   "teardown": {"recovery_command": "mount -a"}}
        mgr = MagicMock()
        mgr.run_setup.return_value = True
        events = self._run(profile, mgr)
        mgr.run_setup.assert_called_once_with(setup)
        setup_phases = [e for e in events
                        if e["event"] == "phase" and e["data"].get("phase") == "setup"]
        self.assertTrue(setup_phases, "inject-only 인데 setup phase 메시지가 없다")
        # 주입됐으면(True) teardown 이 setup_config 를 받아 복원 판정을 한다
        mgr.run_teardown.assert_called_once()

    def test_matching_config_still_delegates_to_run_setup(self):
        """edge 가 일치해도 skip 판정은 run_setup 이 한다 — ord/inject 동반 케이스가
        stream 사전 체크에 걸러지면 안 된다. 기존 메시지(skip reboot)는 유지된다."""
        setup = {"edgeconf_changes": {".VHL_CAM.fps": 30}}
        profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
                   "setup": setup}
        mgr = MagicMock()
        mgr.check_current.return_value = True
        mgr.run_setup.return_value = False
        events = self._run(profile, mgr)
        mgr.run_setup.assert_called_once_with(setup)
        msgs = self._phase_messages(events)
        self.assertIn("Config matches, skip reboot", msgs)
        self.assertNotIn("Setup complete", msgs)

    def test_differing_config_announces_apply_before_setup(self):
        """변경 적용 경로의 기존 UX 보존 — 리부트 전 'Applying...' 예고가 먼저 온다."""
        setup = {"edgeconf_changes": {".VHL_CAM.fps": 30}}
        profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
                   "setup": setup}
        mgr = MagicMock()
        mgr.check_current.return_value = False
        mgr.run_setup.return_value = True
        events = self._run(profile, mgr)
        mgr.run_setup.assert_called_once_with(setup)
        msgs = self._phase_messages(events)
        self.assertIn("Applying 1 changes + reboot...", msgs)
        self.assertIn("Setup complete", msgs)
        self.assertLess(msgs.index("Applying 1 changes + reboot..."),
                        msgs.index("Setup complete"))

    def test_skip_announcement_considers_ord_state(self):
        """#99 Codex P2 — edge 일치 + ord 상이면 'skip reboot' 예고가 거짓이 된다.

        run_setup 은 ord 를 적용하며 재부팅하므로, 예고도 run_setup 과 같은 기준
        (edge+ord 모두 일치)으로 판단해야 한다."""
        setup = {"edgeconf_changes": {".VHL_CAM.fps": 30},
                 "ord_vcm_changes": {".ord.focus": 1}}
        profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
                   "setup": setup}
        mgr = MagicMock()
        mgr.check_current.side_effect = [True, False]   # edge 일치, ord 상이
        mgr.run_setup.return_value = True
        events = self._run(profile, mgr)
        msgs = self._phase_messages(events)
        self.assertNotIn("Config matches, skip reboot", msgs)
        self.assertIn("Applying 2 changes + reboot...", msgs)

    def test_skip_announcement_when_edge_and_ord_both_match(self):
        setup = {"edgeconf_changes": {".VHL_CAM.fps": 30},
                 "ord_vcm_changes": {".ord.focus": 1}}
        profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
                   "setup": setup}
        mgr = MagicMock()
        mgr.check_current.side_effect = [True, True]
        mgr.run_setup.return_value = False
        events = self._run(profile, mgr)
        mgr.run_setup.assert_called_once_with(setup)   # 예고와 무관하게 위임은 유지
        msgs = self._phase_messages(events)
        self.assertIn("Config matches, skip reboot", msgs)

    def test_ssh_error_during_setup_recovers_and_closes_stream(self):
        """#99 Codex P1 — Ssh 예외는 TimeoutError 계열이 아니라서 러너 스레드가
        죽고, fault 가 보드에 남은 채 스트림이 done 없이 매달렸다.
        복구를 시도하고 done(ERROR) 로 닫아야 한다."""
        from ssh import SshConnectionError
        setup = {"inject_command": "umount -l /mnt/sd_cam"}
        teardown = {"recovery_command": "mount -a"}
        profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
                   "setup": setup, "teardown": teardown}
        mgr = MagicMock()
        mgr.run_setup.side_effect = SshConnectionError("connection lost")
        events = self._run(profile, mgr)
        mgr.run_teardown.assert_called_once_with(setup, teardown)
        done = [e for e in events if e["event"] == "done"]
        self.assertEqual(len(done), 1, "done 이벤트가 없다 — 스트림이 매달린다")
        self.assertEqual(done[0]["data"]["status"], "ERROR")

    def test_setup_timeout_also_recovers_before_closing(self):
        """기존 TimeoutError 경로도 부분 적용을 남길 수 있다 — 복구 후 닫는다."""
        setup = {"edgeconf_changes": {".VHL_CAM.fps": 30}}
        teardown = {"recovery_command": "mount -a"}
        profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
                   "setup": setup, "teardown": teardown}
        mgr = MagicMock()
        mgr.check_current.return_value = False
        mgr.run_setup.side_effect = TimeoutError("reboot timeout")
        events = self._run(profile, mgr)
        mgr.run_teardown.assert_called_once_with(setup, teardown)
        done = [e for e in events if e["event"] == "done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["data"]["status"], "ERROR")

    def test_recovery_failure_still_closes_stream(self):
        """복구까지 실패해도(보드 다운 등) 스트림은 반드시 닫힌다."""
        from ssh import SshTimeoutError
        setup = {"inject_command": "true"}
        profile = {"target": {}, "monitor": {"duration_sec": 0}, "checks": {},
                   "setup": setup, "teardown": {"recovery_command": "mount -a"}}
        mgr = MagicMock()
        mgr.run_setup.side_effect = SshTimeoutError("board down")
        mgr.run_teardown.side_effect = SshTimeoutError("still down")
        events = self._run(profile, mgr)
        types = [e["event"] for e in events]
        self.assertIn("warning", types)
        done = [e for e in events if e["event"] == "done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["data"]["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
