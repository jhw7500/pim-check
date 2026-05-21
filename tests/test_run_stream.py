"""run_stream.py 테스트 — run-scoped JSONL 파일 + current.jsonl 원자적 심링크.

AC: pim_check.py atomically creates or updates the events/current.jsonl symlink
to point at the new run file at run start.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_stream import (  # noqa: E402
    CURRENT_SYMLINK_NAME,
    run_file_name,
    start_run_file,
    update_current_symlink,
)


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported on this platform")
class TestRunStreamSymlink(unittest.TestCase):
    def _events_dir(self) -> str:
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        return os.path.join(base, "events")

    def test_run_file_name_layout(self):
        # <ts>_<plan>_<board>.jsonl, parseable by splitting on "_".
        name = run_file_name("comprehensive", "board-A", ts="20260520T101500")
        self.assertEqual(name, "20260520T101500_comprehensive_board-A.jsonl")

    def test_run_file_name_default_has_microseconds(self):
        # 기본 ts 는 마이크로초 포함(YYYYmmddTHHMMSSffffff = 21자) — 같은 초에 시작한
        # 두 run 이 파일을 공유해 이벤트가 섞이는 것을 방지한다.
        name = run_file_name("smoke", "board-A")  # ts=None → 기본값
        ts_seg = name.split("_")[0]
        self.assertEqual(len(ts_seg), 21, ts_seg)
        self.assertTrue(name.endswith("_smoke_board-A.jsonl"))

    def test_run_file_name_rapid_calls_unique(self):
        # 빠른 연속 호출도 마이크로초 해상도로 서로 다른 파일명을 받는다.
        names = [run_file_name("smoke", "b") for _ in range(20)]
        self.assertGreaterEqual(len(set(names)), 2)

    def test_start_creates_run_file_and_symlink(self):
        events_dir = self._events_dir()
        run_path = start_run_file("smoke", "board-A", events_dir=events_dir, ts="T1")

        # Run-scoped file exists and lives under events/.
        self.assertTrue(os.path.isfile(run_path))
        self.assertEqual(os.path.dirname(run_path), events_dir)

        # current.jsonl is a symlink with a RELATIVE target (basename only).
        current = os.path.join(events_dir, CURRENT_SYMLINK_NAME)
        self.assertTrue(os.path.islink(current))
        self.assertEqual(os.readlink(current), os.path.basename(run_path))

        # ... and it resolves to the run file.
        self.assertEqual(os.path.realpath(current), os.path.realpath(run_path))

    def test_atomic_update_overwrites_existing_symlink(self):
        events_dir = self._events_dir()
        first = start_run_file("smoke", "board-A", events_dir=events_dir, ts="T1")
        second = start_run_file("comprehensive", "board-A", events_dir=events_dir, ts="T2")

        current = os.path.join(events_dir, CURRENT_SYMLINK_NAME)
        self.assertTrue(os.path.islink(current))
        # current.jsonl now points at the NEW run file, not the old one.
        self.assertEqual(os.readlink(current), os.path.basename(second))
        self.assertNotEqual(os.path.basename(first), os.path.basename(second))

        # No leftover temp symlinks from the atomic swap.
        leftovers = [f for f in os.listdir(events_dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_update_symlink_directly_is_idempotent(self):
        events_dir = self._events_dir()
        os.makedirs(events_dir)
        # Pre-create a current.jsonl so update must overwrite an existing link.
        open(os.path.join(events_dir, "a.jsonl"), "w").close()
        open(os.path.join(events_dir, "b.jsonl"), "w").close()
        update_current_symlink(events_dir, "a.jsonl")
        update_current_symlink(events_dir, "b.jsonl")
        current = os.path.join(events_dir, CURRENT_SYMLINK_NAME)
        self.assertEqual(os.readlink(current), "b.jsonl")

    def test_symlink_readthrough(self):
        events_dir = self._events_dir()
        run_path = start_run_file("smoke", "board-A", events_dir=events_dir, ts="T1")
        with open(run_path, "a", encoding="utf-8") as fh:
            fh.write('{"event_type": "run_start"}\n')
        current = os.path.join(events_dir, CURRENT_SYMLINK_NAME)
        # Reading through the symlink yields the run file's contents.
        with open(current, "r", encoding="utf-8") as fh:
            self.assertIn("run_start", fh.read())


if __name__ == "__main__":
    unittest.main()
