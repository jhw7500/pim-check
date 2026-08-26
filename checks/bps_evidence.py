from __future__ import annotations

import math
import re
import shlex
import time
from typing import Callable, Dict, List, Optional, Tuple

from checks.base_check import BaseCheck


_POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")


def _finite_epoch(value: str) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


class BpsEvidenceCheck(BaseCheck):
    """Collect one finalized, post-setpoint channel MP4 and its numeric bitrate."""

    name = "bps_evidence"
    scope = "hardware_evidence"

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self._clock = clock
        self._sleeper = sleeper

    @staticmethod
    def _settings(config: dict) -> dict:
        settings = config.get("bps_evidence") or {}
        return settings if isinstance(settings, dict) else {}

    @staticmethod
    def _paths(settings: dict) -> List[str]:
        paths = settings.get("paths", ["/mnt/sd_cam", "/dev/shm"])
        if not isinstance(paths, (list, tuple)):
            return []
        return [path for path in paths if isinstance(path, str) and path.startswith("/")]

    @staticmethod
    def _parse_candidates(output: object, channel: int) -> List[Tuple[float, int, str]]:
        candidates: List[Tuple[float, int, str]] = []
        if not isinstance(output, str):
            return candidates
        suffix = "-ch{0}.mp4".format(channel)
        for line in output.splitlines():
            fields = line.split("\t", 2)
            if len(fields) != 3 or not fields[1].isdigit():
                continue
            mtime = _finite_epoch(fields[0])
            path = fields[2]
            if mtime is None or not path.endswith(suffix) or path.endswith(".part"):
                continue
            candidates.append((mtime, int(fields[1]), path))
        return candidates

    @staticmethod
    def _positive_probe_value(output: object) -> Optional[int]:
        if not isinstance(output, str):
            return None
        lines = output.strip().splitlines()
        if len(lines) != 1 or not _POSITIVE_INTEGER_RE.fullmatch(lines[0].strip()):
            return None
        return int(lines[0].strip())

    def _discover(self, ssh, paths: List[str], channel: int) -> List[Tuple[float, int, str]]:
        candidates: List[Tuple[float, int, str]] = []
        for path in paths:
            command = (
                "find -- {0} -type f -name '*-ch{1}.mp4' ! -name '*.part' "
                "-printf '%T@\\t%s\\t%p\\n' 2>/dev/null"
            ).format(shlex.quote(path), channel)
            candidates.extend(self._parse_candidates(ssh.run(command), channel))
        return candidates

    def collect(self, ssh, config: dict) -> dict:
        settings = self._settings(config)
        channel = settings.get("channel", 0)
        paths = self._paths(settings)
        boot_id_raw = ssh.run("cat /proc/sys/kernel/random/boot_id")
        epoch_raw = ssh.run("date +%s")
        boot_id = boot_id_raw.strip() if isinstance(boot_id_raw, str) else ""
        board_epoch = int(epoch_raw.strip()) if isinstance(epoch_raw, str) and epoch_raw.strip().isdigit() else None
        anchor = settings.get("setpoint_anchor", board_epoch)
        result: Dict[str, object] = {
            "boot_id": boot_id,
            "board_epoch": board_epoch,
            "setpoint_anchor": anchor,
            "video": None,
            "mtime": None,
            "size_bytes": None,
            "actual_bps": None,
            "errors": [],
        }
        if not isinstance(channel, int) or isinstance(channel, bool) or channel < 0:
            result["errors"] = ["channel is invalid"]
            return result
        if not boot_id or board_epoch is None or not isinstance(anchor, (int, float)) or isinstance(anchor, bool):
            result["errors"] = ["boot identity or setpoint anchor is invalid"]
            return result
        if not paths:
            result["errors"] = ["no recording paths configured"]
            return result
        timeout = settings.get("poll_timeout_sec", 120)
        interval = settings.get("poll_interval_sec", 5)
        minimum_size = settings.get("min_size_bytes", 100000)
        if not isinstance(timeout, (int, float)) or timeout < 0 or not isinstance(interval, (int, float)) or interval <= 0:
            result["errors"] = ["poll settings are invalid"]
            return result
        if not isinstance(minimum_size, int) or isinstance(minimum_size, bool) or minimum_size < 0:
            result["errors"] = ["minimum file size is invalid"]
            return result
        deadline = self._clock() + timeout
        last_probe_error = ""
        while self._clock() <= deadline:
            fresh = [candidate for candidate in self._discover(ssh, paths, channel)
                     if candidate[0] >= anchor and candidate[1] >= minimum_size]
            if fresh:
                mtime, size_bytes, video = max(fresh, key=lambda candidate: candidate[0])
                probe = ssh.run(
                    "ffprobe -v error -select_streams v:0 -show_entries stream=bit_rate "
                    "-of csv=p=0 -- {0} 2>/dev/null".format(shlex.quote(video)))
                actual_bps = self._positive_probe_value(probe)
                if actual_bps is not None:
                    result.update({
                        "video": video,
                        "mtime": int(mtime) if mtime.is_integer() else mtime,
                        "size_bytes": size_bytes,
                        "actual_bps": actual_bps,
                    })
                    return result
                last_probe_error = "ffprobe did not return exactly one finite positive integer"
            if self._clock() >= deadline:
                break
            self._sleeper(interval)
        result["errors"] = [last_probe_error or "no fresh finalized sufficiently large channel video found"]
        return result

    def validate(self, data: dict, config: dict) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "BPS evidence is not an object"
        errors = data.get("errors")
        if not isinstance(errors, list):
            return False, "BPS evidence errors are malformed"
        if errors:
            return False, "; ".join(str(error) for error in errors)
        if not isinstance(data.get("boot_id"), str) or not data["boot_id"]:
            return False, "boot id is missing"
        if not isinstance(data.get("board_epoch"), int) or isinstance(data["board_epoch"], bool):
            return False, "board epoch is invalid"
        if not isinstance(data.get("setpoint_anchor"), (int, float)) or isinstance(data["setpoint_anchor"], bool):
            return False, "setpoint anchor is invalid"
        settings = self._settings(config)
        channel = settings.get("channel", 0)
        if not isinstance(channel, int) or isinstance(channel, bool) or channel < 0:
            return False, "channel is invalid"
        video = data.get("video")
        if not isinstance(video, str) or not video.endswith("-ch{0}.mp4".format(channel)) or video.endswith(".part"):
            return False, "finalized video is missing"
        mtime = data.get("mtime")
        if not isinstance(mtime, (int, float)) or isinstance(mtime, bool) or not math.isfinite(mtime):
            return False, "video mtime is invalid"
        if mtime < data["setpoint_anchor"]:
            return False, "video is not fresh for the setpoint anchor"
        if not isinstance(data.get("size_bytes"), int) or data["size_bytes"] < 100000:
            return False, "video size is invalid"
        actual_bps = data.get("actual_bps")
        if not isinstance(actual_bps, int) or isinstance(actual_bps, bool) or actual_bps <= 0:
            return False, "ffprobe bitrate is invalid"
        return True, "OK"
