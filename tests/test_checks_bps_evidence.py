from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ssh import SshTimeoutError


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
            "duration_range_sec": [55, 65],
        }
    }
    config["bps_evidence"].update(overrides)
    return config


def _ssh(
    *,
    listing: str | list[str],
    ffprobe: str = "1024000\n",
    durations: dict[str, str] | None = None,
    bitrates: dict[str, str] | None = None,
) -> MagicMock:
    ssh = MagicMock()
    durations = durations or {}
    bitrates = bitrates or {}
    listings = [listing] if isinstance(listing, str) else listing
    discovery_index = 0

    def run(command: str):
        nonlocal discovery_index
        if command == "cat /proc/sys/kernel/random/boot_id":
            return "boot-123\n"
        if command == "date +%s":
            return "100\n"
        if command.startswith("find -- /recordings"):
            value = listings[min(discovery_index, len(listings) - 1)]
            discovery_index += 1
            return value
        if command.startswith("ffprobe ") and "format=duration" in command:
            return next((value for path, value in durations.items() if path in command), "60\n")
        if command.startswith("ffprobe "):
            return next((value for path, value in bitrates.items() if path in command), ffprobe)
        return ""

    ssh.run.side_effect = run
    return ssh


def test_collect_polls_past_short_finalize_until_complete_recording_arrives() -> None:
    """A reboot-boundary fragment cannot end polling before a comparable sample arrives."""
    from checks.bps_evidence import BpsEvidenceCheck

    clock = _Clock()
    ssh = _ssh(
        listing=[
            "102\t4282063\t/recordings/short-ch0.mp4\n",
            (
                "102\t4282063\t/recordings/short-ch0.mp4\n"
                "103\t30606686\t/recordings/complete-ch0.mp4\n"
            ),
        ],
        durations={
            "short-ch0.mp4": "7.54\n",
            "complete-ch0.mp4": "59.95\n",
        },
        bitrates={
            "short-ch0.mp4": "4545300\n",
            "complete-ch0.mp4": "4084309\n",
        },
    )
    data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(ssh, _config())

    assert data["video"] == "/recordings/complete-ch0.mp4"
    assert data["duration_sec"] == 59.95
    assert data["actual_bps"] == 4084309
    assert clock.value == 1
    bitrate_commands = [
        call.args[0]
        for call in ssh.run.call_args_list
        if "stream=bit_rate" in call.args[0]
    ]
    assert bitrate_commands == [
        "ffprobe -v error -select_streams v:0 -show_entries stream=bit_rate "
        "-of csv=p=0 -- /recordings/complete-ch0.mp4 2>/dev/null",
    ]


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
        "duration_sec": 60.0,
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
        "duration_sec": 60.0,
        "actual_bps": 1024000,
        "errors": [],
    }

    passed, reason = BpsEvidenceCheck().validate(data, _config())

    assert not passed
    assert "fresh" in reason


@pytest.mark.parametrize("duration_sec", [None, 0, 54.99, 65.01])
def test_validate_rejects_missing_or_out_of_range_duration(duration_sec: object) -> None:
    """Only a complete fixture-length recording is comparable BPS evidence."""
    from checks.bps_evidence import BpsEvidenceCheck

    data = {
        "boot_id": "boot-123",
        "board_epoch": 100,
        "setpoint_anchor": 100,
        "video": "/recordings/case-ch0.mp4",
        "mtime": 100,
        "size_bytes": 100000,
        "duration_sec": duration_sec,
        "actual_bps": 1024000,
        "errors": [],
    }

    passed, reason = BpsEvidenceCheck().validate(data, _config())

    assert not passed
    assert "duration" in reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("board_epoch", -1),
        ("setpoint_anchor", -1),
        ("setpoint_anchor", float("nan")),
        ("setpoint_anchor", float("inf")),
    ],
)
def test_validate_rejects_non_finite_or_negative_anchors(field: str, value: object) -> None:
    """Anchor fields must be finite non-negative target time, never permissive numeric lookalikes."""
    from checks.bps_evidence import BpsEvidenceCheck

    data = {
        "boot_id": "boot-123",
        "board_epoch": 100,
        "setpoint_anchor": 100,
        "video": "/recordings/case-ch0.mp4",
        "mtime": 100,
        "size_bytes": 100000,
        "duration_sec": 60.0,
        "actual_bps": 1024000,
        "errors": [],
    }
    data[field] = value

    passed, reason = BpsEvidenceCheck().validate(data, _config())

    assert not passed
    assert "epoch" in reason or "anchor" in reason


@pytest.mark.parametrize("anchor", [float("nan"), float("inf"), -1, "100"])
def test_collect_rejects_invalid_setpoint_anchor_before_polling(anchor: object) -> None:
    """Invalid configuration anchors must stop collection before a candidate can become evidence."""
    from checks.bps_evidence import BpsEvidenceCheck

    clock = _Clock()
    data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(
        _ssh(listing="100\t100000\t/recordings/case-ch0.mp4\n"), _config(setpoint_anchor=anchor))

    assert data["video"] is None
    assert data["actual_bps"] is None
    assert "anchor" in data["errors"][0]


def test_collect_rejects_minimum_size_config_below_hard_floor() -> None:
    """A caller cannot weaken the evidence floor below 100000 bytes."""
    from checks.bps_evidence import BpsEvidenceCheck

    clock = _Clock()
    data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(
        _ssh(listing="100\t99999\t/recordings/tiny-ch0.mp4\n"), _config(min_size_bytes=0))

    assert data["video"] is None
    assert data["actual_bps"] is None
    assert "minimum file size" in data["errors"][0]


@pytest.mark.parametrize(
    "duration_range_sec",
    [None, [], [65, 55], [float("nan"), 65], [55, float("inf")], [55, 10**10000]],
)
def test_collect_rejects_invalid_duration_range_before_polling(
    duration_range_sec: object,
) -> None:
    """Malformed duration bounds cannot weaken sample-integrity filtering."""
    from checks.bps_evidence import BpsEvidenceCheck

    clock = _Clock()
    data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(
        _ssh(listing="100\t100000\t/recordings/case-ch0.mp4\n"),
        _config(duration_range_sec=duration_range_sec),
    )

    assert data["video"] is None
    assert data["actual_bps"] is None
    assert data["errors"] == ["duration range is invalid"]
    assert clock.value == 0


def test_collect_returns_structured_error_on_ssh_timeout() -> None:
    """A transport timeout must become a failed BPS evidence payload rather than an exception leak."""
    from checks.bps_evidence import BpsEvidenceCheck

    ssh = MagicMock()
    ssh.run.side_effect = SshTimeoutError("timed out")

    data = BpsEvidenceCheck().collect(ssh, _config())

    assert data["video"] is None
    assert data["actual_bps"] is None
    assert data["errors"] == ["SSH_ERROR: timed out"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("poll_timeout_sec", float("nan")),
        ("poll_timeout_sec", float("inf")),
        ("poll_timeout_sec", float("-inf")),
        ("poll_interval_sec", float("nan")),
        ("poll_interval_sec", float("inf")),
        ("poll_interval_sec", float("-inf")),
    ],
)
def test_collect_rejects_non_finite_poll_controls_before_polling(field: str, value: object) -> None:
    """NaN and infinity cannot enter deadline arithmetic or trigger a poll/sleep loop."""
    from checks.bps_evidence import BpsEvidenceCheck

    clock = _Clock()
    data = BpsEvidenceCheck(clock=clock, sleeper=clock.sleep).collect(
        _ssh(listing="100\t100000\t/recordings/case-ch0.mp4\n"), _config(**{field: value}))

    assert data["video"] is None
    assert data["actual_bps"] is None
    assert data["errors"] == ["poll settings are invalid"]
    assert clock.value == 0
