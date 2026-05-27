"""run_stream.py per-target 경로 라우팅 + active.json 인덱스 테스트.

multi-target viewer 가 한 events/ 안에서 여러 타겟 런을 동시에 가질 수 있도록
host 인자로 events/by-target/<slug>/ 로 라우팅한다. 단일 타겟(host=None)은
기존 events/ 경로 동작을 그대로 유지(backward compat — TUI viewer 영향 없음).

핵심 불변:
  1. host=None → 기존 동작 (events/<file>.jsonl + events/current.jsonl)
  2. host="..." → events/by-target/<slug>/<file>.jsonl + per-host current.jsonl
                  + 기존 events/current.jsonl 도 함께 last-started 로 갱신 (TUI 호환)
                  + events/active.json 에 host 등록
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_stream import (  # noqa: E402
    ACTIVE_HOSTS_NAME,
    BY_TARGET_DIR,
    CURRENT_SYMLINK_NAME,
    host_slug,
    read_active_hosts,
    start_run_file,
    target_events_dir,
)


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported on this platform")
class TestHostSlug(unittest.TestCase):
    def test_host_slug_ipv4(self):
        # IP 점은 path 에 안전하지만 디렉터리/시각적 모호성을 줄이려 '-' 로 치환.
        self.assertEqual(host_slug("192.168.0.5"), "192-168-0-5")

    def test_host_slug_hostname(self):
        self.assertEqual(host_slug("imx8mp-dev.lan"), "imx8mp-dev-lan")

    def test_host_slug_strips_unsafe_chars(self):
        # path separator / 와 공백은 '_' 로 치환되어야 함.
        slug = host_slug("a b/c")
        self.assertNotIn("/", slug)
        self.assertNotIn(" ", slug)

    def test_host_slug_empty_fallback(self):
        # 빈 host 도 path 충돌 없는 fallback 을 돌려준다.
        self.assertTrue(host_slug(""))
        self.assertTrue(host_slug(None))  # type: ignore[arg-type]


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported on this platform")
class TestTargetEventsDir(unittest.TestCase):
    def test_no_host_returns_base(self):
        base = "/tmp/x/events"
        # host 없으면 base 그대로 — backward compat.
        self.assertEqual(target_events_dir(base, None), base)

    def test_with_host_returns_by_target_subdir(self):
        base = "/tmp/x/events"
        self.assertEqual(
            target_events_dir(base, "192.168.0.5"),
            os.path.join(base, BY_TARGET_DIR, "192-168-0-5"),
        )


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported on this platform")
class TestStartRunFileWithHost(unittest.TestCase):
    def _events_dir(self) -> str:
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        return os.path.join(base, "events")

    def test_host_routes_to_by_target_subdir(self):
        events_dir = self._events_dir()
        run_path = start_run_file(
            "smoke", "192.168.0.5", events_dir=events_dir, ts="T1",
            host="192.168.0.5",
        )
        # 파일이 events/by-target/192-168-0-5/ 에 위치한다.
        expected_dir = os.path.join(events_dir, BY_TARGET_DIR, "192-168-0-5")
        self.assertEqual(os.path.dirname(run_path), expected_dir)
        self.assertTrue(os.path.isfile(run_path))

    def test_host_creates_per_target_current_symlink(self):
        events_dir = self._events_dir()
        run_path = start_run_file(
            "smoke", "host-a", events_dir=events_dir, ts="T1", host="host-a",
        )
        per_target_current = os.path.join(
            events_dir, BY_TARGET_DIR, "host-a", CURRENT_SYMLINK_NAME,
        )
        self.assertTrue(os.path.islink(per_target_current))
        self.assertEqual(os.readlink(per_target_current), os.path.basename(run_path))

    def test_host_also_updates_legacy_root_symlink_for_tui_compat(self):
        # TUI viewer (pim_viewer.py) 는 events/current.jsonl 만 본다.
        # multi-target 도입으로 TUI 가 깨지지 않도록, host 모드에서도 legacy 심링크가
        # last-started 타겟의 런 파일을 가리키게 동시 갱신한다.
        events_dir = self._events_dir()
        run_a = start_run_file(
            "smoke", "host-a", events_dir=events_dir, ts="T1", host="host-a",
        )
        legacy = os.path.join(events_dir, CURRENT_SYMLINK_NAME)
        self.assertTrue(os.path.islink(legacy))
        # legacy 는 절대/상대 경로 어느 쪽이든 host-a 의 런 파일에 resolve 돼야 한다.
        self.assertEqual(os.path.realpath(legacy), os.path.realpath(run_a))

        run_b = start_run_file(
            "smoke", "host-b", events_dir=events_dir, ts="T2", host="host-b",
        )
        # 더 늦게 시작한 host-b 가 legacy 의 새 타겟 — last-started wins.
        self.assertEqual(os.path.realpath(legacy), os.path.realpath(run_b))

    def test_host_registers_in_active_hosts_json(self):
        events_dir = self._events_dir()
        start_run_file(
            "smoke", "host-a", events_dir=events_dir, ts="T1", host="host-a",
        )
        start_run_file(
            "comprehensive", "host-b", events_dir=events_dir, ts="T2", host="host-b",
        )
        active = read_active_hosts(events_dir)
        # 두 host 모두 등록.
        hosts = {h["host"]: h for h in active.get("hosts", [])}
        self.assertIn("host-a", hosts)
        self.assertIn("host-b", hosts)
        # slug, current path 가 포함되어 viewer 가 바로 사용 가능.
        self.assertEqual(hosts["host-a"]["slug"], "host-a")
        # current 경로는 events_dir 기준 상대 — 자유 이동 가능.
        self.assertEqual(
            hosts["host-a"]["current"],
            os.path.join(BY_TARGET_DIR, "host-a", CURRENT_SYMLINK_NAME),
        )
        # 같은 host 의 두 번째 런은 새 항목을 만들지 않고 plan/started_at 만 갱신.
        run_a2 = start_run_file(
            "smoke", "host-a", events_dir=events_dir, ts="T3", host="host-a",
        )
        active2 = read_active_hosts(events_dir)
        hosts2 = [h for h in active2.get("hosts", []) if h["host"] == "host-a"]
        self.assertEqual(len(hosts2), 1)
        # 같은 host 의 current 심링크는 새 런으로 갱신.
        per_target_current = os.path.join(
            events_dir, BY_TARGET_DIR, "host-a", CURRENT_SYMLINK_NAME,
        )
        self.assertEqual(
            os.path.realpath(per_target_current), os.path.realpath(run_a2),
        )

    def test_no_host_unchanged_layout(self):
        # host 미지정 시 기존 동작 그대로 (backward compat).
        events_dir = self._events_dir()
        run_path = start_run_file("smoke", "board-A", events_dir=events_dir, ts="T1")
        # by-target/ 디렉터리는 만들어지지 않는다.
        self.assertFalse(os.path.exists(os.path.join(events_dir, BY_TARGET_DIR)))
        # active.json 도 만들어지지 않는다.
        self.assertFalse(os.path.exists(os.path.join(events_dir, ACTIVE_HOSTS_NAME)))
        # 파일은 events/ 직속.
        self.assertEqual(os.path.dirname(run_path), events_dir)

    def test_active_hosts_json_is_valid_json(self):
        events_dir = self._events_dir()
        start_run_file("smoke", "h", events_dir=events_dir, ts="T1", host="h")
        with open(os.path.join(events_dir, ACTIVE_HOSTS_NAME)) as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)
        self.assertIn("hosts", data)


@unittest.skipUnless(hasattr(os, "fork"), "fork unsupported on this platform")
class TestActiveHostsCrossProcessRace(unittest.TestCase):
    """active.json 갱신이 cross-process 동시 호출에서도 lost-update 가 없어야 한다.

    PR #38 bot 리뷰(Gemini code-assist) 지적: threading.Lock 만으로는 별도 프로세스
    (예: 동시 pim_check.py 인스턴스, 또는 web.py spawn 자식)가 read-modify-write
    중간에 끼어들면 마지막-쓰기-승자가 다른 프로세스 항목을 덮어쓴다. 회귀 가드.
    """

    def test_concurrent_subprocess_registers_dont_lose_updates(self):
        import subprocess
        import sys
        import textwrap

        base = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, base, ignore_errors=True)
        events_dir = os.path.join(base, "events")
        os.makedirs(events_dir, exist_ok=True)
        repo = os.path.join(os.path.dirname(__file__), "..")

        # 워커 N 개를 동시 spawn — 각자 다른 host 등록.
        n = 8
        script = textwrap.dedent(f"""
            import sys, os
            sys.path.insert(0, {repo!r})
            from run_stream import register_active_host
            host = f"host-{{sys.argv[1]}}"
            register_active_host({events_dir!r}, host, "smoke", host, "run.jsonl")
        """)
        script_path = os.path.join(base, "worker.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        # 모두 동시에 시작해서 race 창을 최대화.
        procs = [
            subprocess.Popen([sys.executable, script_path, str(i)])
            for i in range(n)
        ]
        for p in procs:
            self.assertEqual(p.wait(timeout=30), 0)

        data = read_active_hosts(events_dir)
        hosts = {h["host"] for h in data.get("hosts", [])}
        expected = {f"host-{i}" for i in range(n)}
        # lost-update 가 발생했다면 일부 host 가 누락된다.
        self.assertEqual(hosts, expected, f"missing={expected - hosts}, got={hosts}")


if __name__ == "__main__":
    unittest.main()
