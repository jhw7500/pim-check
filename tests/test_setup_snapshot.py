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

    def test_wrapped_base64_is_accepted_and_compacted(self):
        """-w0 미지원 보드는 76열로 접어 출력한다 — 개행을 제거하고 받아들인다."""
        mgr = _mgr()
        mgr.ssh.run.return_value = "eyJhIjog\nMX0=\n"
        assert mgr.snapshot_config(EDGECONF_PATH) is True
        assert mgr._config_snapshots[EDGECONF_PATH] == "eyJhIjogMX0="

    def test_ssh_error_is_failure_not_raise(self):
        mgr = _mgr()
        mgr.ssh.run.side_effect = RuntimeError("boom")
        assert mgr.snapshot_config(EDGECONF_PATH) is False

    def test_snapshots_are_per_path(self):
        mgr = _mgr()
        mgr.ssh.run.return_value = "AAA"
        mgr.snapshot_config(EDGECONF_PATH)
        mgr.ssh.run.return_value = "BBB"
        mgr.snapshot_config(ORD_VCM_PATH)
        assert mgr._config_snapshots == {EDGECONF_PATH: "AAA", ORD_VCM_PATH: "BBB"}


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
