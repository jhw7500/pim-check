"""tests/test_setup_snapshot.py — teardown 복원용 호스트 스냅샷 (pim-check#65).

기존 teardown 은 보드의 `/root/shared_v/backup/*.bak` 에서 복원했는데, 그 슬롯은
pim-check 전용이 아니다. 보드 FW `config_guard.sh` 가 **부팅 시 valid 한 현재본을
`.bak` 으로 복사**하기 때문에(자기 known-good 갱신), 설정 적용 → 재부팅을 거치면
`.bak` 이 이미 케이스 설정으로 덮여 있어 `restore()` 가 no-op 이 된다.

해법은 복원 원본을 보드가 아니라 **호스트가 들고 있는 것**이다. 보드 경로 계약이
없으므로 guard 와 구조적으로 경합하지 않는다. `.bak` 쓰기는 그대로 둔다 — 그건
config_guard 가 디폴트(/etc/defaultconf.json) 리셋을 막는 데 쓰는 별개 용도다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from setup import EDGECONF_PATH, ORD_VCM_PATH, SetupManager


def _mgr():
    return SetupManager(MagicMock(), reboot_timeout=300, poll_interval=10)


class TestSnapshotConfig:
    def test_stores_base64_of_remote_file(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "eyJhIjogMX0="
        assert mgr.snapshot_config(EDGECONF_PATH) is True
        cmd = mgr.ssh.run.call_args[0][0]
        assert "base64 -w0" in cmd
        assert EDGECONF_PATH in cmd
        assert mgr._config_snapshots[EDGECONF_PATH] == "eyJhIjogMX0="

    def test_empty_or_missing_output_is_failure(self):
        mgr = _mgr()
        for out in (None, "", "   "):
            mgr.ssh.run.return_value = out
            assert mgr.snapshot_config(EDGECONF_PATH) is False
            assert EDGECONF_PATH not in mgr._config_snapshots

    def test_non_base64_output_is_rejected(self):
        """잡음이 섞인 출력을 저장하면 복원 때 base64 -d 가 죽고 조용히 .bak 폴백으로
        떨어진다 — 이 기능이 없애려던 경로다. 여기서 실패로 잡는다."""
        mgr = _mgr()
        for out in ("eyJhIjogMX0=\nWarning: something",
                    "base64: /x: No such file or directory",
                    "not base64!!",
                    "eyJh IjogMX0= trailing words"):
            mgr.ssh.run.return_value = out
            assert mgr.snapshot_config(EDGECONF_PATH) is False, out
            assert EDGECONF_PATH not in mgr._config_snapshots

    def test_truncated_base64_is_rejected(self):
        """절단 출력 — 문자집합만 보면 통과하지만 디코드는 실패한다.

        길이가 4 의 배수가 아니면 base64 -d 가 복원 때 죽고 조용히 .bak 폴백으로
        떨어진다. 호스트에 바이트가 이미 있으니 실물로 확인하는 편이 싸다.
        """
        mgr = _mgr()
        mgr.ssh.run.return_value = "eyJhIjogMX0"      # '=' 패딩 잘림
        assert mgr.snapshot_config(EDGECONF_PATH) is False
        assert EDGECONF_PATH not in mgr._config_snapshots

    def test_valid_base64_of_non_json_is_rejected(self):
        """디코드는 되지만 JSON 이 아닌 내용 — 복원측 `jq -e .` 와 대칭으로 막는다."""
        mgr = _mgr()
        mgr.ssh.run.return_value = "aGVsbG8gd29ybGQ="   # 'hello world'
        assert mgr.snapshot_config(EDGECONF_PATH) is False

    def test_wrapped_base64_is_accepted_and_compacted(self):
        """-w0 미지원 보드는 76열로 접어 출력한다 — 개행을 제거하고 받아들인다."""
        mgr = _mgr()
        mgr.ssh.run.return_value = "eyJhIjog\nMX0=\n"
        assert mgr.snapshot_config(EDGECONF_PATH) is True
        assert mgr._config_snapshots[EDGECONF_PATH] == "eyJhIjogMX0="

    def test_wrapping_tolerance_does_not_apply_when_option_unsupported(self):
        """`-w` 를 모르는 구현은 exit≠0 → ssh.run 이 None → 여기까지 오지 않는다.

        폴딩 관용이 먹는 범위는 '`-w` 를 무시하고 접어서 출력하는' 구현뿐이라는
        것을 명시 고정한다(주석이 사실과 어긋나지 않도록).
        """
        mgr = _mgr()
        mgr.ssh.run.return_value = None
        assert mgr.snapshot_config(EDGECONF_PATH) is False

    def test_ssh_error_is_failure_not_raise(self):
        mgr = _mgr()
        mgr.ssh.run.side_effect = RuntimeError("boom")
        assert mgr.snapshot_config(EDGECONF_PATH) is False

    def test_snapshots_are_per_path(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "eyJhIjogMX0="      # {"a": 1}
        mgr.snapshot_config(EDGECONF_PATH)
        mgr.ssh.run.return_value = "eyJiIjogMn0="      # {"b": 2}
        mgr.snapshot_config(ORD_VCM_PATH)
        assert mgr._config_snapshots == {
            EDGECONF_PATH: "eyJhIjogMX0=", ORD_VCM_PATH: "eyJiIjogMn0="}


class TestRestoreFromSnapshot:
    def test_returns_false_without_snapshot(self):
        mgr = _mgr()
        assert mgr.restore_from_snapshot(EDGECONF_PATH) is False
        mgr.ssh.run.assert_not_called()

    def test_writes_back_via_base64_with_json_validation(self):
        mgr = _mgr()
        mgr._config_snapshots[EDGECONF_PATH] = "eyJhIjogMX0="
        mgr.ssh.run.return_value = "OK"
        assert mgr.restore_from_snapshot(EDGECONF_PATH) is True
        cmd = mgr.ssh.run.call_args[0][0]
        assert "base64 -d" in cmd
        assert "eyJhIjogMX0=" in cmd
        # 깨진 JSON 을 제자리에 쓰면 config_guard 가 디폴트 리셋을 트리거한다 —
        # 임시 파일에 쓰고 jq 로 검증한 뒤에만 원자적으로 옮긴다.
        assert "jq -e ." in cmd
        assert "mv " in cmd
        assert "sync" in cmd

    def test_board_reported_failure_is_false(self):
        mgr = _mgr()
        mgr._config_snapshots[EDGECONF_PATH] = "eyJhIjogMX0="
        for out in ("FAIL", None, "", "something else"):
            mgr.ssh.run.return_value = out
            assert mgr.restore_from_snapshot(EDGECONF_PATH) is False

    def test_ssh_error_is_false_not_raise(self):
        mgr = _mgr()
        mgr._config_snapshots[EDGECONF_PATH] = "eyJhIjogMX0="
        mgr.ssh.run.side_effect = RuntimeError("boom")
        assert mgr.restore_from_snapshot(EDGECONF_PATH) is False


class TestRestoreConfFallbackVisibility:
    """폴백의 두 경우를 구분해 남기는지 — 이상 징후가 정상 로그에 묻히면 안 된다."""

    def _mgr_with_log(self):
        mgr = _mgr()
        mgr._local0_log = MagicMock()
        mgr.restore = MagicMock()
        return mgr

    def test_missing_snapshot_is_logged_as_normal_fallback(self, capsys):
        mgr = self._mgr_with_log()
        mgr._restore_conf(EDGECONF_PATH)      # 스냅샷 없음 = 정상(setup-skip 등)
        assert "WARNING" not in capsys.readouterr().out
        assert "no snapshot" in mgr._local0_log.call_args[0][0]
        mgr.restore.assert_called_once_with(EDGECONF_PATH)

    def test_failed_restore_with_snapshot_warns(self, capsys):
        mgr = self._mgr_with_log()
        mgr._config_snapshots[EDGECONF_PATH] = "eyJhIjogMX0="
        mgr.restore_from_snapshot = MagicMock(return_value=False)   # 보드 복원 실패
        mgr._restore_conf(EDGECONF_PATH)
        assert "WARNING" in capsys.readouterr().out
        assert "RESTORE FAILED" in mgr._local0_log.call_args[0][0]
        mgr.restore.assert_called_once_with(EDGECONF_PATH)

    def test_success_path_logs_snapshot_and_skips_bak(self, capsys):
        mgr = self._mgr_with_log()
        mgr._config_snapshots[EDGECONF_PATH] = "eyJhIjogMX0="
        mgr.restore_from_snapshot = MagicMock(return_value=True)
        mgr._restore_conf(EDGECONF_PATH)
        assert "WARNING" not in capsys.readouterr().out
        assert "via host snapshot" in mgr._local0_log.call_args[0][0]
        mgr.restore.assert_not_called()


class TestRunSetupTakesSnapshot:
    def test_snapshot_is_taken_before_changes_are_applied(self):
        """순서가 핵심 — 변경 후에 찍으면 스냅샷이 케이스 설정이 돼 복원이 무의미하다."""
        mgr = _mgr()
        order = []
        mgr.check_current = MagicMock(return_value=False)
        mgr.backup = MagicMock(return_value=True)
        mgr.snapshot_config = MagicMock(
            side_effect=lambda p: order.append(("snapshot", p)) or True)
        # 람다 파라미터명을 실제 시그니처(changes, conf_path)에 맞춘다 —
        # 호출부가 키워드 인자로 바뀌어도 TypeError 가 나지 않도록.
        mgr.apply_changes = MagicMock(
            side_effect=lambda changes, conf_path=EDGECONF_PATH:
                order.append(("apply", conf_path)))
        mgr.run_setup({"edgeconf_changes": {".a": 1}})
        assert order == [("snapshot", EDGECONF_PATH), ("apply", EDGECONF_PATH)]

    def test_snapshot_failure_warns_but_does_not_abort(self):
        """스냅샷 실패는 '복원 불가'일 뿐 오늘 동작과 같다 — 케이스를 죽이지 않는다."""
        mgr = _mgr()
        mgr.check_current = MagicMock(return_value=False)
        mgr.backup = MagicMock(return_value=True)
        mgr.snapshot_config = MagicMock(return_value=False)
        mgr.apply_changes = MagicMock()
        assert mgr.run_setup({"edgeconf_changes": {".a": 1}}) is True
        mgr.apply_changes.assert_called_once()

    def test_previous_snapshots_are_cleared_at_setup_start(self):
        mgr = _mgr()
        mgr._config_snapshots[EDGECONF_PATH] = "stale"
        mgr.run_setup({})   # 변경 없음 → 조기 반환
        assert mgr._config_snapshots == {}

    def test_ord_vcm_is_snapshotted_too(self):
        mgr = _mgr()
        mgr.check_current = MagicMock(return_value=False)
        mgr.backup = MagicMock(return_value=True)
        mgr.snapshot_config = MagicMock(return_value=True)
        mgr.apply_changes = MagicMock()
        mgr.run_setup({"edgeconf_changes": {".a": 1}, "ord_vcm_changes": {".b": 2}})
        paths = [c[0][0] for c in mgr.snapshot_config.call_args_list]
        assert paths == [EDGECONF_PATH, ORD_VCM_PATH]


class TestRunTeardownPrefersSnapshot:
    def _mgr_for_teardown(self):
        mgr = _mgr()
        mgr._local0_log = MagicMock()
        mgr.reboot_and_wait = MagicMock()
        mgr.restore = MagicMock()
        mgr.restore_from_snapshot = MagicMock(return_value=True)
        return mgr

    def test_uses_snapshot_and_skips_bak_restore(self):
        mgr = self._mgr_for_teardown()
        mgr.run_teardown({"edgeconf_changes": {".a": 1}})
        mgr.restore_from_snapshot.assert_called_once_with(EDGECONF_PATH)
        # 보드 .bak 는 config_guard 가 이미 케이스 설정으로 덮었을 수 있다 — 안 쓴다.
        mgr.restore.assert_not_called()

    def test_falls_back_to_bak_when_snapshot_missing(self):
        """스냅샷이 없으면(설정 실패 등) 기존 경로라도 시도한다 — 오늘과 동일."""
        mgr = self._mgr_for_teardown()
        mgr.restore_from_snapshot = MagicMock(return_value=False)
        mgr.run_teardown({"edgeconf_changes": {".a": 1}})
        mgr.restore.assert_called_once_with(EDGECONF_PATH)

    def test_both_paths_restored_when_both_changed(self):
        mgr = self._mgr_for_teardown()
        mgr.run_teardown({"edgeconf_changes": {".a": 1}, "ord_vcm_changes": {".b": 2}})
        paths = [c[0][0] for c in mgr.restore_from_snapshot.call_args_list]
        assert paths == [EDGECONF_PATH, ORD_VCM_PATH]

    def test_inject_only_teardown_does_not_restore(self):
        mgr = self._mgr_for_teardown()
        mgr.run_teardown({"inject_command": "true", "recovery_command": "true"})
        mgr.restore_from_snapshot.assert_not_called()
        mgr.restore.assert_not_called()


class TestSnapshotFailureLedger:
    """#82 — 스냅샷 실패가 인스턴스에 남아야 plan 이 기준선 밀림을 판정한다.

    `_snapshot_or_warn` 의 경고는 그 케이스 시점에 흘러가고, 캠페인 복원은 수천 줄
    뒤에 성공으로 찍힌다 — 둘을 잇는 것이 이 원장(ledger)이다.
    """

    def test_failure_records_path(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = ""          # 빈 출력 = snapshot_config 실패
        mgr._snapshot_or_warn(EDGECONF_PATH)
        assert mgr._snapshot_failures == {EDGECONF_PATH}

    def test_success_records_nothing(self):
        import base64
        mgr = _mgr()
        mgr.ssh.run.return_value = base64.b64encode(b'{"a": 1}').decode()
        mgr._snapshot_or_warn(EDGECONF_PATH)
        assert mgr._snapshot_failures == set()

    def test_run_setup_resets_the_ledger(self):
        """이전 케이스의 실패가 다음 케이스로 새면 plan 이 엉뚱한 케이스를 의심한다."""
        mgr = _mgr()
        mgr._snapshot_failures.add(EDGECONF_PATH)
        mgr.run_setup({})                      # 변경도 inject 도 없음 — 조기 False
        assert mgr._snapshot_failures == set()
