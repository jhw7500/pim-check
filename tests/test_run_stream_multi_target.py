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
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import run_stream  # noqa: E402
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

    def test_host_slug_is_case_insensitive(self):
        # macOS/Windows 의 case-insensitive FS 에서 같은 호스트가 다른 슬러그를
        # 만들면 두 자식이 같은 디렉터리를 경합한다. lower-case 정규화로 차단.
        self.assertEqual(host_slug("Host-A"), host_slug("host-a"))
        self.assertEqual(host_slug("HOST-A"), "host-a")
        self.assertEqual(host_slug("imx8mp-DEV.LAN"), "imx8mp-dev-lan")


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


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported on this platform")
class TestActiveHostsMalformedSchema(unittest.TestCase):
    """active.json 가 손상/오염된 상태에서도 register 가 죽지 않아야 한다.

    PR #38 봇 리뷰(Gemini 3차) 지적: hosts 리스트에 non-dict 항목(예: 수동 편집,
    이전 버전 fragment, partial write 등)이 섞이면 ``h.get("host")`` 에서
    AttributeError 발생. viewer 인프라 전체가 멈출 리스크라 방어 코드 추가.
    """

    def test_read_active_hosts_handles_non_dict_root(self):
        # JSON 자체는 valid 지만 root 가 dict 가 아닌 경우 (e.g. 누군가 list 로 덮어씀).
        # read_active_hosts 는 빈 인덱스로 graceful fallback 해야 한다.
        base = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, base, ignore_errors=True)
        events_dir = os.path.join(base, "events")
        os.makedirs(events_dir, exist_ok=True)
        with open(os.path.join(events_dir, ACTIVE_HOSTS_NAME), "w") as f:
            json.dump(["not", "a", "dict"], f)
        # AttributeError/KeyError 없이 비어있는 hosts 로 fallback.
        data = read_active_hosts(events_dir)
        self.assertEqual(data, {"hosts": []})
        # 후속 register 도 정상 작동.
        from run_stream import register_active_host
        register_active_host(events_dir, "new-h", "smoke", "new-h", "r.jsonl")
        data2 = read_active_hosts(events_dir)
        self.assertEqual({h["host"] for h in data2["hosts"]}, {"new-h"})

    def test_register_skips_non_dict_entries(self):
        base = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, base, ignore_errors=True)
        events_dir = os.path.join(base, "events")
        os.makedirs(events_dir, exist_ok=True)
        # 비정상: 문자열이 hosts 에 끼어 있음.
        with open(os.path.join(events_dir, ACTIVE_HOSTS_NAME), "w") as f:
            json.dump(
                {"hosts": ["legacy-string-entry", {"host": "valid-h", "slug": "valid-h"}]},
                f,
            )
        # AttributeError 없이 새 host 가 정상 등록되고, 비정상 항목은 사라져야 한다.
        from run_stream import register_active_host
        register_active_host(events_dir, "new-h", "smoke", "new-h", "run.jsonl")
        active = read_active_hosts(events_dir)
        hosts = {h["host"] for h in active["hosts"]}
        self.assertIn("new-h", hosts)
        self.assertIn("valid-h", hosts)
        self.assertNotIn("legacy-string-entry", hosts)


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


class TestActiveHostsWindowsFallback(unittest.TestCase):
    """`_fcntl = None` 환경에서 msvcrt.locking 분기가 호출되는지 검증 (PR B).

    실제 Windows CI 가 없어 mock 으로 환경 시뮬레이션 — Linux CI 에서도 msvcrt
    분기가 정상 호출되는지 회귀 가드 (Windows 사용자가 fcntl 없이도 동시
    /start race 안전성을 가지도록).
    """

    def test_register_uses_msvcrt_when_fcntl_absent(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        events_dir = os.path.join(base, "events")
        os.makedirs(events_dir, exist_ok=True)

        msvcrt_mock = MagicMock()
        msvcrt_mock.LK_LOCK = 1
        msvcrt_mock.LK_UNLCK = 0

        with patch.object(run_stream, "_fcntl", None), \
             patch.object(run_stream, "_msvcrt", msvcrt_mock):
            run_stream.register_active_host(
                events_dir, "win-h", "smoke", "win-h", "r.jsonl",
            )

        # 결과: active.json 에 정상 등록.
        data = read_active_hosts(events_dir)
        self.assertIn("win-h", {h["host"] for h in data["hosts"]})

        # msvcrt.locking 이 LK_LOCK + LK_UNLCK 으로 각각 호출됐는지.
        calls = msvcrt_mock.locking.call_args_list
        self.assertGreaterEqual(
            len(calls), 2, f"expected at least lock+unlock, got: {calls}",
        )
        modes = [c.args[1] for c in calls]
        self.assertIn(msvcrt_mock.LK_LOCK, modes, "LK_LOCK 호출 없음")
        self.assertIn(msvcrt_mock.LK_UNLCK, modes, "LK_UNLCK 호출 없음")

    def test_register_swallows_msvcrt_oserror_and_still_releases(self):
        """msvcrt.locking acquire 가 OSError 를 던져도 deadlock 없이 진행 + release 시도 보장."""
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        events_dir = os.path.join(base, "events")
        os.makedirs(events_dir, exist_ok=True)

        msvcrt_mock = MagicMock()
        msvcrt_mock.LK_LOCK = 1
        msvcrt_mock.LK_UNLCK = 0
        # acquire 만 실패, release 는 정상 — graceful degradation 시 thread lock 으로 진행
        # 하더라도 finally 의 release 경로는 여전히 호출돼 idempotent 하게 처리되어야 함.
        msvcrt_mock.locking.side_effect = [OSError("retry exhausted"), None]

        with patch.object(run_stream, "_fcntl", None), \
             patch.object(run_stream, "_msvcrt", msvcrt_mock):
            run_stream.register_active_host(
                events_dir, "oserr-h", "smoke", "oserr-h", "r.jsonl",
            )

        # OSError swallow 후에도 register 완료 (lost-update 가능성 작음 — atomic rename 보장).
        data = read_active_hosts(events_dir)
        self.assertIn("oserr-h", {h["host"] for h in data["hosts"]})

        # acquire 실패 후에도 release 가 LK_UNLCK 로 시도됐는지 — finally 의 release 경로 회귀 가드.
        calls = msvcrt_mock.locking.call_args_list
        modes = [c.args[1] for c in calls]
        self.assertIn(msvcrt_mock.LK_LOCK, modes, "LK_LOCK 호출 없음")
        self.assertIn(
            msvcrt_mock.LK_UNLCK, modes,
            "acquire OSError 후에도 LK_UNLCK 가 호출되어야 — finally release 경로 누락",
        )

    def test_register_works_without_any_file_lock(self):
        """`_fcntl=_msvcrt=None` (비-POSIX 비-Windows) graceful — thread lock 만 적용."""
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        events_dir = os.path.join(base, "events")
        os.makedirs(events_dir, exist_ok=True)

        with patch.object(run_stream, "_fcntl", None), \
             patch.object(run_stream, "_msvcrt", None):
            run_stream.register_active_host(
                events_dir, "noflock-h", "smoke", "noflock-h", "r.jsonl",
            )

        data = read_active_hosts(events_dir)
        self.assertIn("noflock-h", {h["host"] for h in data["hosts"]})


if __name__ == "__main__":
    unittest.main()
