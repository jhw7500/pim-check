from __future__ import annotations

import contextlib
import io
import types
import unittest
from unittest.mock import patch

import pim_check


_MOCK_PROFILE = {
    "target": {"host": "192.168.0.5", "user": "root", "password": "root"},
    "monitor": {"duration_sec": 0, "interval_sec": 5},
    "checks": {},
}


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        args = pim_check.parse_args([])
        self.assertIsNone(args.case)
        self.assertIsNone(args.host)
        self.assertIsNone(args.user)
        self.assertIsNone(args.password)
        self.assertIsNone(args.duration)
        self.assertFalse(args.all)

    def test_user_password_flags(self):
        args = pim_check.parse_args(["--user", "admin", "--password", "secret"])
        self.assertEqual(args.user, "admin")
        self.assertEqual(args.password, "secret")

    def test_case_flag(self):
        args = pim_check.parse_args(["--case", "fhd_4ch"])
        self.assertEqual(args.case, "fhd_4ch")

    def test_host_override(self):
        args = pim_check.parse_args(["--host", "192.168.0.10"])
        self.assertEqual(args.host, "192.168.0.10")

    def test_all_flag(self):
        args = pim_check.parse_args(["--all"])
        self.assertTrue(args.all)

    def test_list_flag(self):
        args = pim_check.parse_args(["--list"])
        self.assertTrue(args.list)

    def test_learn_flag(self):
        args = pim_check.parse_args(["--learn"])
        self.assertTrue(args.learn)

    def test_until_pass_flag(self):
        args = pim_check.parse_args(["--plan", "comprehensive", "--until-pass"])
        self.assertTrue(args.until_pass)

    def test_until_pass_default_false(self):
        args = pim_check.parse_args(["--plan", "comprehensive"])
        self.assertFalse(args.until_pass)

    def test_reset_mode_flag(self):
        args = pim_check.parse_args(
            ["--plan", "comprehensive", "--reset-mode", "camera_hard"]
        )
        self.assertEqual(args.reset_mode, "camera_hard")

    def test_reset_mode_default_none(self):
        args = pim_check.parse_args(["--plan", "comprehensive"])
        self.assertIsNone(args.reset_mode)


class TestPlanResetMode(unittest.TestCase):
    @patch("event_session.EventSession")
    @patch("plan.render_reports", return_value=[])
    @patch("plan.evaluate_gate")
    @patch("plan.execute_plan", return_value=[])
    @patch("plan.resolve_cases", return_value=[])
    @patch("plan.load_plan")
    @patch("pim_check.os.path.exists", return_value=True)
    def test_run_plan_applies_cli_reset_mode_override(
        self, _exists, mock_load_plan, _resolve_cases, mock_execute_plan,
        mock_evaluate_gate, _render_reports, mock_event_session,
    ):
        loaded_plan = types.SimpleNamespace(
            name="comprehensive",
            description="d",
            execution={
                "stop_on_fail": False,
                "case_retry": 0,
                "monitor_until_pass": False,
                "reset_mode": "reboot",
            },
            gate={},
        )
        mock_load_plan.return_value = loaded_plan
        mock_evaluate_gate.return_value = types.SimpleNamespace(
            verdict="PASS", pass_rate=1.0, regressions=[], fixed=[],
            new_cases=[], known_warns=[],
        )
        mock_event_session.return_value.__enter__.return_value = (
            mock_event_session.return_value
        )

        args = pim_check.parse_args(
            ["--plan", "comprehensive", "--reset-mode", "camera_hard", "--quiet"]
        )
        self.assertEqual(pim_check._run_plan(args), 0)
        self.assertEqual(loaded_plan.execution["reset_mode"], "camera_hard")
        self.assertIs(mock_execute_plan.call_args.args[0], loaded_plan)

    @patch("pim_check.list_cases", return_value=[])
    def test_non_plan_reset_mode_warns(self, _list_cases):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                pim_check.main(["--list", "--reset-mode", "camera_hard"]), 0
            )
        text = output.getvalue()
        self.assertIn("--reset-mode", text)
        self.assertIn("--plan", text)


class TestMainFlow(unittest.TestCase):
    @patch("pim_check.Reporter")
    @patch("pim_check.Engine")
    @patch("pim_check.load_profile")
    @patch("pim_check.SshClient")
    def test_basic_healthcheck(self, MockSsh, mock_load, MockEngine, MockReporter):
        mock_load.return_value = _MOCK_PROFILE.copy()
        MockSsh.return_value.check_connectivity.return_value = True
        MockSsh.return_value.preflight_check.return_value = []

        mock_results = [{"name": "cpu", "passed": True, "reason": "OK", "data": {}}]
        MockEngine.return_value.run_snapshot.return_value = mock_results

        MockReporter.return_value.format.return_value = "PASS report"

        # duration=0 → run_snapshot 경로
        ret = pim_check.run_case("healthcheck", "192.168.0.5", None, None, 0)
        self.assertEqual(ret, 0)
        MockEngine.return_value.run_snapshot.assert_called_once()
        MockEngine.return_value.run_monitor.assert_not_called()

    @patch("pim_check.Reporter")
    @patch("pim_check.Engine")
    @patch("pim_check.load_profile")
    @patch("pim_check.SshClient")
    def test_fail_returns_nonzero(self, MockSsh, mock_load, MockEngine, MockReporter):
        mock_load.return_value = _MOCK_PROFILE.copy()
        MockSsh.return_value.check_connectivity.return_value = True
        MockSsh.return_value.preflight_check.return_value = []

        mock_results = [{"name": "cpu", "passed": False, "reason": "too high", "data": {}}]
        MockEngine.return_value.run_snapshot.return_value = mock_results

        MockReporter.return_value.format.return_value = "FAIL report"

        ret = pim_check.run_case("healthcheck", "192.168.0.5", None, None, 0)
        self.assertEqual(ret, 1)


class TestEdgeconfValueMatches(unittest.TestCase):
    """--dry-run 이 jq 출력(JSON 텍스트)과 목표값을 JSON 기준으로 비교한다."""

    def test_booleans_compare_as_json(self):
        self.assertTrue(pim_check._edgeconf_value_matches("false", False))
        self.assertTrue(pim_check._edgeconf_value_matches("true\n", True))
        self.assertFalse(pim_check._edgeconf_value_matches("true", False))

    def test_bool_is_not_number(self):
        self.assertFalse(pim_check._edgeconf_value_matches("1", True))
        self.assertFalse(pim_check._edgeconf_value_matches("true", 1))

    @patch("setup.SetupManager")
    @patch("pim_check.load_profile")
    @patch("pim_check.SshClient")
    def test_dry_run_lists_only_real_differences(self, mock_ssh_cls, mock_profile, mock_setup):
        mock_profile.return_value = {"setup": {"edgeconf_changes": {".a": False, ".b": 1280}}}
        ssh = mock_ssh_cls.return_value
        ssh.check_connectivity.return_value = True
        ssh.run.side_effect = lambda cmd: "true" if "'.a'" in cmd else "1280"
        mock_setup.return_value.check_current.return_value = False
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ret = pim_check.main(["--dry-run", "--case", "x", "--host", "h"])
        self.assertEqual(ret, 0)
        self.assertIn(".a: true → false", out.getvalue())
        self.assertNotIn(".b:", out.getvalue())

    def test_strings_numbers_and_missing(self):
        self.assertTrue(pim_check._edgeconf_value_matches('"mp4"', "mp4"))
        self.assertTrue(pim_check._edgeconf_value_matches("15", 15))
        self.assertFalse(pim_check._edgeconf_value_matches("null", 15))
        self.assertFalse(pim_check._edgeconf_value_matches(None, 15))
        self.assertFalse(pim_check._edgeconf_value_matches("not json", "x"))


if __name__ == "__main__":
    unittest.main()
