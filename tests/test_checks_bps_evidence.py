from __future__ import annotations

from unittest.mock import MagicMock


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _config(**overrides: object) -> dict:
    config = {
        "bps_evidence": {
            "channel": 0,
            "paths": ["/recordings"],
            "poll_timeout_sec": 2,
            "poll_interval_sec": 1,
            "min_size_bytes": 100000,
        }
    }
    config["bps_evidence"].update(overrides)
    return config


def _ssh(*, listing: str, ffprobe: str = "1024000\n") -> MagicMock:
    ssh = MagicMock()

    def run(command: str):
        if command == "cat /proc/sys/kernel/random/boot_id":
            return "boot-123\n"
        if command == "date +%s":
            return "100\n"
        if command.startswith("find -- /recordings"):
            return listing
        if command.startswith("ffprobe "):
            return ffprobe
        return ""

    ssh.run.side_effect = run
    return ssh


def test_collect_accepts_fresh_finalized_large_video_with_one_positive_bitrate() -> None:
    """A fresh completed ch0 MP4 has one authoritative numeric bitrate value."""
    from checks.bps_evidence import BpsEvidenceCheck

    clock = _Clock()
    data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(
        _ssh(listing="100\t100000\t/recordings/case-ch0.mp4\n"), _config())

    assert data == {
        "boot_id": "boot-123",
        "board_epoch": 100,
        "setpoint_anchor": 100,
        "video": "/recordings/case-ch0.mp4",
        "mtime": 100,
        "size_bytes": 100000,
        "actual_bps": 1024000,
        "errors": [],
    }
    # A collector accepts measurement integrity; policy tolerances belong to the baseline gate.
    assert BpsEvidenceCheck().validate(data, _config(tolerance_percent=0)) == (True, "OK")


def test_collect_rejects_stale_and_part_files_until_bounded_poll_expires() -> None:
    """Old output and in-progress .part output cannot be promoted to evidence."""
    from checks.bps_evidence import BpsEvidenceCheck

    clock = _Clock()
    data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(_ssh(
        listing="99\t999999\t/recordings/old-ch0.mp4\n"
                "101\t999999\t/recordings/new-ch0.mp4.part\n",
    ), _config())

    assert data["video"] is None
    assert data["actual_bps"] is None
    assert clock.value == 2
    assert BpsEvidenceCheck().validate(data, _config())[0] is False


def test_collect_rejects_missing_or_too_small_finalized_video() -> None:
    """A name match without a sufficiently large finalized artifact is not a measurement."""
    from checks.bps_evidence import BpsEvidenceCheck

    clock = _Clock()
    data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(
        _ssh(listing="100\t99999\t/recordings/tiny-ch0.mp4\n"), _config())

    assert data["video"] is None
    assert "fresh finalized" in data["errors"][0]


def test_collect_rejects_ffprobe_with_multiple_or_non_positive_values() -> None:
    """A probe must return exactly one finite positive integer, never a best-effort parse."""
    from checks.bps_evidence import BpsEvidenceCheck

    for output in ("1024000\n1024001\n", "N/A\n", "0\n", "-1\n"):
        clock = _Clock()
        data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(
            _ssh(listing="100\t100000\t/recordings/case-ch0.mp4\n", ffprobe=output), _config())

        assert data["actual_bps"] is None
        assert BpsEvidenceCheck().validate(data, _config())[0] is False


def test_validate_rejects_claimed_evidence_that_is_stale() -> None:
    """Validation must recheck freshness rather than trust a producer's empty error list."""
    from checks.bps_evidence import BpsEvidenceCheck

    data = {
        "boot_id": "boot-123",
        "board_epoch": 100,
        "setpoint_anchor": 100,
        "video": "/recordings/old-ch0.mp4",
        "mtime": 99,
        "size_bytes": 100000,
        "actual_bps": 1024000,
        "errors": [],
    }

    passed, reason = BpsEvidenceCheck().validate(data, _config())

    assert not passed
    assert "fresh" in reason
