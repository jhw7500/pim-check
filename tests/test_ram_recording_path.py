"""
tests/test_ram_recording_path.py — SD 불량(Mode B) RAM 녹화 경로는 /dev/shm/recordings (#177).

보드 패키지(camera14)의 RAM 녹화 경로는 `/opt/pim/bin/chk_cam_operate.sh`
RAM_ONLY_FINAL_PATH_DEFAULT="/dev/shm/recordings" 하나다. s 가 빠진
`/dev/shm/recording` 을 보면 SD 불량 시 "파일 있어야 함" 항목은 거짓 실패, "0건"
항목은 거짓 통과가 된다. 활성 케이스(profiles/cases 최상위)와 테스트 밖 코드에 남지
않게 막는다 (`.deprecated/`, profiles/generated 는 범위 밖).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_PATH = re.compile(r"/dev/shm/recording(?!s)")
_SKIP_DIRS = {"tests", ".git", "__pycache__", ".deprecated", ".venv", "venv"}


def _hits(paths) -> list[str]:
    out = []
    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if OLD_PATH.search(line):
                out.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return out


def test_active_cases_use_ram_recordings_path():
    cases = sorted((ROOT / "profiles" / "cases").glob("*.yaml"))
    assert len(cases) > 50  # 범위가 비면 이 검사는 아무것도 보지 않는다
    assert _hits(cases) == []


def test_production_code_uses_ram_recordings_path():
    sources = sorted(p for p in ROOT.rglob("*.py")
                     if not _SKIP_DIRS.intersection(p.relative_to(ROOT).parts))
    assert any(p.name == "setup.py" for p in sources)
    assert _hits(sources) == []


def test_matcher_flags_old_path_but_not_new():
    assert OLD_PATH.search("find /mnt/sd_cam /dev/shm/recording \\(")
    assert OLD_PATH.search('RECORDING_DIRS = ["/dev/shm", "/dev/shm/recording"]')
    assert not OLD_PATH.search("find /dev/shm/recordings /mnt/sd_cam")
