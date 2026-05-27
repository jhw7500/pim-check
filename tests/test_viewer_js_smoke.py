"""tests/test_viewer_js_smoke.py — pim_web_viewer.py 의 임베디드 JS 가 브라우저에서
실제 실행되는지 verify 하는 smoke 테스트.

PR #42 의 silent JS SyntaxError (split('\\n') 의 Python escape 함정) 같은 회귀가
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
import urllib.request
from contextlib import contextmanager

import pytest

# playwright 가 설치 안 됐으면 전체 모듈 skip — 다른 환경에서 dev 흐름 막지 않음.
playwright = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    """OS 에 사용 가능 port 1 개 빌리기 — 8077 충돌 회피."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _viewer(port: int):
    """pim_web_viewer 를 BG 로 spawn 하고 종료 시 정리."""
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
            except (urllib.error.URLError, ConnectionRefusedError):
                time.sleep(0.1)
        else:
            raise RuntimeError("viewer failed to start within 5s")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_index_html_js_loads_without_pageerror():
    """페이지 로드 시 pageerror (uncaught exception) 가 없어야 한다.

    JS SyntaxError 가 발생하면 script 전체가 정의되지 않아 typeof === 'undefined'
    가 되는데, pageerror 도 동시에 fire. 두 가지 모두 verify.
    """
    port = _free_port()
    with _viewer(port):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                errors: list[str] = []
                # pageerror = uncaught exception in page (SyntaxError, ReferenceError 포함)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/")
                page.wait_for_load_state("networkidle", timeout=10000)

                # 핵심 함수가 정의됐는가 — script 가 완전히 parse + 실행됐다는 signal
                must = ["tick", "tickMulti", "tickOnce", "startMulti", "stopHost",
                        "stopAll", "mtCol", "fetchActive", "fetchHostState",
                        "mtPostJSON"]
                missing = [f for f in must
                           if page.evaluate(f"typeof {f}") != "function"]
                assert not missing, f"functions undefined: {missing}"

                # 전역 상태 변수도 접근 가능해야 (TDZ 회귀 가드)
                assert page.evaluate("typeof MT_SELECTED_HOST") in ("object", "string"), \
                    "MT_SELECTED_HOST should be accessible (null or string)"
                assert page.evaluate("typeof MT_MAX") == "number"

                # 페이지 자체 에러 0건
                assert errors == [], f"page errors: {errors}"
            finally:
                browser.close()
