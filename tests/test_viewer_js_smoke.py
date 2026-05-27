"""tests/test_viewer_js_smoke.py — pim_web_viewer.py 의 임베디드 JS 가 브라우저에서
실제 실행되는지 verify 하는 smoke 테스트.

PR #42 의 silent JS SyntaxError (split 의 Python escape 함정) 같은 회귀가
pytest 단위 테스트 + ruff lint 를 모두 통과한 사례 → 브라우저에서 실제 페이지를
로드해 핵심 함수가 정의됐는지 확인하는 가드가 필요.

playwright 가 설치돼 있어야 한다 (dev extra). CI 의 별도 job 으로 분리:
- ``pip install playwright && playwright install chromium``
- 일반 ``pip install pim-check[dev]`` 사용자 환경에는 영향 없음.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

# playwright 가 설치 안 됐으면 전체 모듈 skip — 다른 환경에서 dev 흐름 막지 않음.
pytest.importorskip("playwright.sync_api")
# importorskip 이후 안전하게 module-top import — 일반적인 import 레이아웃 유지.
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    """OS 에 사용 가능 port 1 개 빌리기 — 8077 충돌 회피.

    TOCTOU: 이 socket close 와 viewer bind 사이에 다른 프로세스가 port 를
    가져갈 미세한 window 존재. CI 단일 호스트 환경에서 실용적으로 무시 가능.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _viewer(port: int):
    """pim_web_viewer 를 BG 로 spawn 하고 종료 시 정리.

    happy path 에서는 stdout/stderr 모두 DEVNULL — PIPE 였다면 viewer 가 64KB
    pipe buffer 채울 때 child write block + proc.wait deadlock. 기동 실패는
    매우 드물고, 실패 진단이 필요하면 사용자가 viewer 를 별도 실행해서 확인.
    """
    proc = subprocess.Popen(
        [sys.executable, "pim_web_viewer.py",
         "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # HTTP ready 까지 대기 (최대 5초). connect 가능해지면 즉시 진행.
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/control", timeout=0.5)
                break
            except urllib.error.HTTPError:
                # 서버가 응답했다는 것 자체가 ready 신호 — 4xx/5xx 라도 break.
                # HTTPError 는 OSError subclass 라 광범 catch 가 retry 로 가두는
                # 함정을 피해야 한다 (/control 404 같은 응답도 ready 로 인정).
                break
            except OSError:
                # urllib.error.URLError / ConnectionRefusedError / 기타 network
                # 일시 오류 (서버 not yet listening). retry.
                time.sleep(0.1)
        else:
            # 기동 실패 — 자식은 deadlock 회피 위해 stdout/stderr=DEVNULL 라 진단
            # 정보 캡처 불가. 사용자가 별도로 `python pim_web_viewer.py --port N`
            # 직접 실행해 stderr 확인하면 됨.
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise RuntimeError(f"viewer failed to start within 5s on port {port}")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()  # SIGKILL 즉시 — wait 로 reap 해 zombie 잔류 방지


def test_index_html_js_loads_without_pageerror():
    """페이지 로드 시 pageerror (uncaught exception) 가 없어야 한다.

    JS SyntaxError 가 발생하면 script 전체가 정의되지 않아 typeof === 'undefined'
    가 되는데, pageerror 도 동시에 fire. 두 가지 모두 verify.

    networkidle 은 쓰지 않는다 — viewer 가 tick/tickMulti/loadControl 로 1~3초
    간격 폴링을 계속 보내 networkidle 상태가 영원히 안 옴 → 10s timeout fail.
    'load' 가 fire 된 시점이면 <script> 블록은 parse + 초기 실행 완료 (typeof
    체크에 충분). DOMContentLoaded 보다 더 보수적이지만 안전한 선택.
    """
    port = _free_port()
    with _viewer(port):
        # headless=True 명시 — Playwright 기본도 True 지만, 미래 default 변경에
        # 대비한 CI 의도 self-documenting.
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                errors: list[str] = []
                console_errors: list[str] = []
                # pageerror = uncaught exception in page (SyntaxError, ReferenceError 포함)
                page.on("pageerror", lambda e: errors.append(str(e)))

                # console error 도 추적 — uncaught 가 아닌 JS 실패 (failed fetch
                # 외 라이브러리 에러 등) 도 catch. 함수로 분리 (lambda multiline 회피).
                def _on_console(msg):
                    if msg.type == "error":
                        console_errors.append(msg.text)
                page.on("console", _on_console)
                # goto 의 기본 wait = 'load' — 우리 검증에 충분.
                page.goto(f"http://127.0.0.1:{port}/")

                # 핵심 함수가 정의됐는가 — script 가 완전히 parse + 실행됐다는 signal
                must = ["tick", "tickMulti", "tickOnce", "startMulti", "stopHost",
                        "stopAll", "mtCol", "fetchActive", "fetchHostState",
                        "mtPostJSON"]
                missing = [f for f in must
                           if page.evaluate(f"typeof {f}") != "function"]
                assert not missing, f"functions undefined: {missing}"

                # 전역 상태 변수도 접근 가능해야 (TDZ 회귀 가드).
                # null 은 JS 에서 typeof "object" 라 두 값 다 허용.
                assert page.evaluate("typeof MT_SELECTED_HOST") in ("object", "string"), \
                    "MT_SELECTED_HOST should be accessible (typeof null === 'object')"
                assert page.evaluate("typeof MT_MAX") == "number"

                # 페이지 자체 에러 0건 (uncaught + console.error 모두).
                # console_errors 는 viewer 가 빈 상태일 때 발생하는 일부 fetch
                # 4xx 까지 포함될 수 있어 정보 표시만 하고 fail 시키지 않음 —
                # pageerror 가 진짜 JS 실패 signal.
                assert errors == [], f"pageerror: {errors}\nconsole errors: {console_errors}"
            finally:
                browser.close()
