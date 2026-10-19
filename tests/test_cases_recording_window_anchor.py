"""Recording-file presence/absence windows are scoped to the session anchor (#175).

`-mmin -N` 창은 setup 이 빨라지자 직전 설정의 녹화 파일(.srt 등)을 같이 셌다. 이 항목들은
세션 앵커(`/tmp/pim_check_anchor`, 1행 = 앵커 시각, 2행 = boot_id) **이후** 파일만 세고,
앵커가 없거나 다른 부팅의 것이면 0 대신 FAIL 토큰을 내야 한다 — 0 은 "0건" 부재 항목을
거짓 통과시킨다.

명령은 실제 케이스 YAML 에서 읽어 임시 트리에서 bash 로 실제 실행한다.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from checks.custom import CustomCommandCheck

CASES_DIR = Path(__file__).resolve().parent.parent / "profiles" / "cases"
IN_SCOPE_CASES = ("720p_2ch", "fhd_2ch_03", "fhd_3ch_012", "fhd_4ch")
# 녹화 경로를 패턴 묶음으로 세는 항목 (세션 prefix 로 고르는 finalize 항목과 구분)
COUNT_FIND = r"find /mnt/sd_cam /dev/shm/recording \("
EXPECTED_ITEMS = {
    ("720p_2ch", "최근 2분 내 .srt 파일 0건 (srt_enable=false 실측)"),
    ("720p_2ch", "ch2 disabled (i2c1.ch2.enable=false → mp4 0건)"),
    ("720p_2ch", "ch3 disabled (i2c1.ch3.enable=false → mp4 0건)"),
    ("fhd_2ch_03", "ts 파일 생성 (직전 2분)"),
    ("fhd_2ch_03", "최근 2분 내 .srt 파일 ≥1 (srt_enable=true 실측)"),
    ("fhd_2ch_03", "ch1 disabled (i2c2.ch1.enable=false → ts 0건)"),
    ("fhd_2ch_03", "ch2 disabled (i2c1.ch2.enable=false → ts 0건)"),
    ("fhd_3ch_012", "최근 2분 내 .srt 파일 ≥1 (srt_enable=true 실측)"),
    ("fhd_3ch_012", "ch3 disabled (i2c1.ch3.enable=false → mp4 0건)"),
    ("fhd_4ch", "최근 2분 내 .srt 파일 ≥1 (srt_enable=true 실측)"),
}
BOOT_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
OTHER_BOOT_ID = "11111111-2222-3333-4444-555555555555"


def _items():
    out = []
    for case in IN_SCOPE_CASES:
        profile = yaml.safe_load((CASES_DIR / f"{case}.yaml").read_text()) or {}
        for item in (profile.get("checks") or {}).get("custom_commands") or []:
            if COUNT_FIND in (item.get("command") or ""):
                out.append((case, item))
    return out


ITEMS = _items()
IDS = [f"{case}:{item['name']}" for case, item in ITEMS]


def test_in_scope_items_are_the_ten_recording_file_windows():
    assert {(case, item["name"]) for case, item in ITEMS} == EXPECTED_ITEMS
    assert len(ITEMS) == len(EXPECTED_ITEMS)


def test_no_minute_window_left_in_in_scope_case_commands():
    leftovers = []
    for case in IN_SCOPE_CASES:
        profile = yaml.safe_load((CASES_DIR / f"{case}.yaml").read_text()) or {}
        for item in (profile.get("checks") or {}).get("custom_commands") or []:
            if re.search(r"-mmin\s+-?\d", item.get("command") or ""):
                leftovers.append(f"{case}: {item['name']}")
    assert not leftovers, leftovers


class _Board:
    """Temp tree standing in for the board paths a check command touches."""

    def __init__(self, root: Path):
        self.sd = root / "sd_cam"
        self.shm = root / "shm_recording"
        self.anchor = root / "pim_check_anchor"
        self.boot_id = root / "boot_id"
        self.sd.mkdir()
        self.shm.mkdir()
        self.boot_id.write_text(BOOT_ID + "\n")

    def write_anchor(self, when: int, boot_id: str = BOOT_ID):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
        self.anchor.write_text(f"{stamp}\n{boot_id}\n")
        os.utime(self.anchor, (when, when))

    def touch(self, directory: Path, name: str, when: int):
        path = directory / name
        path.write_text("x")
        os.utime(path, (when, when))

    def run(self, command: str) -> subprocess.CompletedProcess:
        local = (command
                 .replace("/proc/sys/kernel/random/boot_id", str(self.boot_id))
                 .replace("/tmp/pim_check_anchor", str(self.anchor))
                 .replace("/dev/shm/recording", str(self.shm))
                 .replace("/mnt/sd_cam", str(self.sd)))
        for board_path in ("/proc/", "/tmp/pim_check_anchor", "/dev/shm", "/mnt/"):
            assert board_path not in local, f"unsubstituted board path {board_path}"
        return subprocess.run(["bash", "-c", local], capture_output=True, text=True,
                              timeout=30, check=False)


def _names(command: str) -> list[str]:
    patterns = re.findall(r"-name '([^']+)'", command)
    assert patterns, command
    return [p.replace("*", "20261019_1500_x") for p in patterns]


def _passes(item: dict, output: str) -> bool:
    result = {
        "name": item["name"],
        "output": output,
        "expected": item.get("expected"),
        "expected_min": item.get("expected_min"),
        "on_fail": item.get("on_fail", "failed"),
    }
    ok, _msg = CustomCommandCheck().validate({"results": [result], "skipped": False}, {})
    return ok


@pytest.fixture
def board(tmp_path):
    return _Board(tmp_path)


@pytest.mark.parametrize("case,item", ITEMS, ids=IDS)
def test_file_older_than_anchor_is_not_counted(board, case, item):
    """#175 — 직전 설정의 파일은 앵커보다 오래됐지만 옛 2~3분 창 안에 있다."""
    now = int(time.time())
    board.write_anchor(now - 30)
    for name in _names(item["command"]):
        board.touch(board.sd, name, now - 60)
    proc = board.run(item["command"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "0", proc.stdout
    # 부재 항목은 통과, 존재 항목은 실패해야 한다
    assert _passes(item, proc.stdout) is (item.get("expected") == "0")


@pytest.mark.parametrize("case,item", ITEMS, ids=IDS)
def test_file_newer_than_anchor_is_counted(board, case, item):
    now = int(time.time())
    board.write_anchor(now - 30)
    for name in _names(item["command"]):
        board.touch(board.shm, name, now)
        board.touch(board.sd, name, now - 60)
    proc = board.run(item["command"])
    assert proc.returncode == 0, proc.stderr
    assert int(proc.stdout.strip()) >= 1, proc.stdout
    # 존재 항목은 통과, 부재 항목은 실패해야 한다
    assert _passes(item, proc.stdout) is (item.get("expected_min") is not None)


@pytest.mark.parametrize("case,item", ITEMS, ids=IDS)
def test_missing_anchor_prints_fail_token_not_zero(board, case, item):
    proc = board.run(item["command"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().startswith("FAIL:"), proc.stdout
    assert _passes(item, proc.stdout) is False


@pytest.mark.parametrize("case,item", ITEMS, ids=IDS)
def test_anchor_from_another_boot_prints_fail_token(board, case, item):
    now = int(time.time())
    board.write_anchor(now - 30, boot_id=OTHER_BOOT_ID)
    proc = board.run(item["command"])
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().startswith("FAIL:"), proc.stdout
    assert _passes(item, proc.stdout) is False
