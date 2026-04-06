"""web.py API 테스트"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch, MagicMock
from io import BytesIO
from http.server import HTTPServer

from web import DashboardHandler, _build_dashboard_html, _list_cases


class TestBuildDashboardHtml(unittest.TestCase):
    def test_renders_html(self):
        html = _build_dashboard_html()
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("pim-check Dashboard", html)

    def test_contains_stats(self):
        html = _build_dashboard_html()
        self.assertIn("Total Runs", html)
        self.assertIn("Pass Rate", html)

    def test_contains_controls(self):
        html = _build_dashboard_html()
        self.assertIn("Run Now", html)
        self.assertIn("Auto Start", html)
        self.assertIn("Run Smoke", html)


class TestListCases(unittest.TestCase):
    def test_returns_list(self):
        cases = _list_cases()
        self.assertIsInstance(cases, list)
        self.assertTrue(len(cases) > 0)

    def test_includes_generated(self):
        cases = _list_cases(include_generated=True)
        gen = [c for c in cases if c.startswith("gen_")]
        self.assertTrue(len(gen) > 0)


class TestDashboardHandlerAPI(unittest.TestCase):
    """DashboardHandler의 API 엔드포인트를 직접 테스트."""

    def _make_request(self, path: str) -> tuple:
        """핸들러에 GET 요청을 보내고 (status_code, body) 를 반환."""
        handler = MagicMock(spec=DashboardHandler)
        handler.path = path
        handler.headers = {}
        wfile = BytesIO()
        handler.wfile = wfile

        # _respond를 실제 동작하도록 연결
        def fake_respond(code, body, content_type):
            handler._status_code = code
            wfile.write(body.encode("utf-8"))

        handler._respond = fake_respond

        DashboardHandler.do_GET(handler)

        body = wfile.getvalue().decode("utf-8")
        return (getattr(handler, "_status_code", 0), body)

    def test_root_returns_html(self):
        code, body = self._make_request("/")
        self.assertEqual(code, 200)
        self.assertIn("pim-check", body)

    def test_api_status(self):
        code, body = self._make_request("/api/status")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertIn("auto", data)
        self.assertIn("active", data)

    def test_api_history(self):
        code, body = self._make_request("/api/history")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertIsInstance(data, list)

    def test_api_cases(self):
        code, body = self._make_request("/api/cases")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) > 0)

    def test_404(self):
        code, body = self._make_request("/nonexistent")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
