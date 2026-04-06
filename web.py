"""
web.py — pim-check 웹 대시보드 서버

내장 http.server 기반. 외부 의존성 없음.
대시보드에서 테스트 실행, 결과 확인, 자동 실행 설정 가능.

사용법:
    python3 web.py                    # localhost:8080
    python3 web.py --port 9090        # 커스텀 포트
    python3 web.py --host 0.0.0.0     # 외부 접근 허용
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from history import read_history, save_dashboard, append_result
from config import load_profile
from engine import Engine
from reporter import Reporter
from setup import SetupManager
from ssh import SshClient

PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# 자동 실행 상태
_auto_state = {
    "running": False,
    "interval": 0,
    "case": None,
    "host": "192.168.0.5",
    "thread": None,
}

# 실행 중인 테스트 상태
_run_state = {
    "active": False,
    "case": None,
    "started": None,
}


def _list_cases(include_generated: bool = True) -> list[str]:
    import glob
    pattern = os.path.join(PROFILES_DIR, "cases", "*.yaml")
    paths = glob.glob(pattern)
    if include_generated:
        gen_pattern = os.path.join(PROFILES_DIR, "generated", "*.yaml")
        paths.extend(glob.glob(gen_pattern))
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in paths)


def _run_test(case_name: str | None, host: str, user: str = "root",
              password: str = "root") -> dict:
    """테스트를 실행하고 결과 dict를 반환한다."""
    _run_state["active"] = True
    _run_state["case"] = case_name
    _run_state["started"] = datetime.now().isoformat()

    try:
        profile = load_profile(PROFILES_DIR, case=case_name)
        profile["target"]["host"] = host
        profile["target"]["user"] = user
        profile["target"]["password"] = password

        h = profile["target"].get("host", host)
        ssh = SshClient(h, user, password)

        if not ssh.check_connectivity():
            return {"status": "ERROR", "message": f"Cannot connect to {h}"}

        ssh.preflight_check()

        setup_config = profile.get("setup")
        setup_mgr = SetupManager(ssh)
        setup_changed = False
        if setup_config:
            try:
                setup_changed = setup_mgr.run_setup(setup_config)
            except TimeoutError as e:
                return {"status": "ERROR", "message": f"Setup failed: {e}"}

        try:
            engine = Engine(ssh, profile)
            effective_duration = profile["monitor"].get("duration_sec", 0)
            if effective_duration <= 0:
                results = engine.run_snapshot()
                collected, total = 1, 1
            else:
                results, collected, total = engine.run_monitor()

            # known_issues 적용
            known_issues = profile.get("known_issues")
            if known_issues:
                for r in results:
                    if not r["passed"]:
                        for ki in known_issues:
                            if r["name"] == ki["check"] and ki["reason_contains"] in r.get("reason", ""):
                                r["known_issue"] = ki["label"]

            # 히스토리에 저장
            append_result(results, case_name, h, collected, total, REPORTS_DIR)
            save_dashboard(REPORTS_DIR)

            passed = sum(1 for r in results if r["passed"])
            real_fails = [r for r in results if not r["passed"] and "known_issue" not in r]
            status = "PASS" if not real_fails and not any("known_issue" in r for r in results) else \
                     "WARN" if not real_fails else "FAIL"

            return {
                "status": status,
                "case": case_name,
                "host": h,
                "passed": passed,
                "total": len(results),
                "checks": [
                    {
                        "name": r["name"],
                        "passed": r["passed"],
                        "reason": r.get("reason", ""),
                        "known_issue": r.get("known_issue", ""),
                    }
                    for r in results
                ],
            }
        finally:
            if setup_config and setup_changed:
                try:
                    setup_mgr.run_teardown(setup_config)
                except TimeoutError:
                    pass
    finally:
        _run_state["active"] = False


def _auto_runner():
    """자동 실행 스레드."""
    while _auto_state["running"]:
        case = _auto_state["case"]
        host = _auto_state["host"]
        try:
            _run_test(case, host)
        except Exception:
            pass
        interval = _auto_state["interval"]
        for _ in range(interval):
            if not _auto_state["running"]:
                break
            time.sleep(1)


def _build_dashboard_html() -> str:
    """웹 대시보드 HTML을 생성한다."""
    cases = _list_cases()
    case_options = "\n".join(f'<option value="{c}">{c}</option>' for c in cases)

    history = read_history(REPORTS_DIR)
    total_runs = len(history)
    total_pass = sum(1 for e in history if e["result"] == "PASS")
    pass_rate = (total_pass / total_runs * 100) if total_runs else 0

    # 케이스별 최근 결과
    case_stats: dict[str, dict] = {}
    for e in history:
        case = e.get("case") or "healthcheck"
        case_stats[case] = e  # 마지막 결과만

    case_rows = ""
    for name in sorted(case_stats.keys()):
        e = case_stats[name]
        status = e["result"]
        color = "#22c55e" if status == "PASS" else "#f59e0b" if status == "WARN" else "#ef4444"
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        case_rows += f'<tr><td>{name}</td><td style="color:{color};font-weight:bold">{status}</td>'
        case_rows += f'<td>{e.get("passed",0)}/{e.get("total",0)}</td><td>{ts}</td>'
        case_rows += f'<td><button onclick="runTest(\'{name}\')" class="btn btn-sm">Run</button></td></tr>\n'

    # 최근 10건
    recent_rows = ""
    for e in reversed(history[-10:]):
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        case = e.get("case") or "healthcheck"
        status = e["result"]
        color = "#22c55e" if status == "PASS" else "#f59e0b" if status == "WARN" else "#ef4444"
        recent_rows += f'<tr><td>{ts}</td><td>{case}</td>'
        recent_rows += f'<td style="color:{color};font-weight:bold">{status}</td>'
        recent_rows += f'<td>{e.get("passed",0)}/{e.get("total",0)}</td></tr>\n'

    auto_status = "Running" if _auto_state["running"] else "Stopped"
    auto_color = "#22c55e" if _auto_state["running"] else "#6b7280"
    pass_color = "#22c55e" if pass_rate >= 80 else "#f59e0b" if pass_rate >= 50 else "#ef4444"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>pim-check Dashboard</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,system-ui,sans-serif; background:#f9fafb; padding:20px; }}
  .container {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:22px; color:#111; margin-bottom:16px; display:flex; align-items:center; gap:12px; }}
  .badge {{ font-size:12px; padding:3px 8px; border-radius:12px; color:#fff; }}
  .stats {{ display:flex; gap:12px; margin-bottom:20px; }}
  .stat {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:16px; flex:1; text-align:center; }}
  .stat-value {{ font-size:28px; font-weight:700; }}
  .stat-label {{ font-size:12px; color:#6b7280; margin-top:4px; }}
  .panel {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:16px; margin-bottom:16px; }}
  .panel h2 {{ font-size:15px; color:#374151; margin-bottom:12px; }}
  .controls {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
  select, input {{ padding:6px 10px; border:1px solid #d1d5db; border-radius:6px; font-size:14px; }}
  .btn {{ padding:6px 14px; border:none; border-radius:6px; cursor:pointer; font-size:14px; font-weight:500; }}
  .btn-primary {{ background:#3b82f6; color:#fff; }}
  .btn-primary:hover {{ background:#2563eb; }}
  .btn-danger {{ background:#ef4444; color:#fff; }}
  .btn-success {{ background:#22c55e; color:#fff; }}
  .btn-sm {{ padding:4px 10px; font-size:12px; background:#f3f4f6; border:1px solid #d1d5db; }}
  .btn-sm:hover {{ background:#e5e7eb; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ background:#f3f4f6; text-align:left; padding:8px 12px; font-size:13px; color:#374151; border-bottom:1px solid #e5e7eb; }}
  td {{ padding:8px 12px; border-bottom:1px solid #f3f4f6; font-size:14px; }}
  #status {{ padding:8px 12px; border-radius:6px; margin-top:12px; display:none; }}
  .spinner {{ display:inline-block; width:14px; height:14px; border:2px solid #ccc; border-top-color:#3b82f6; border-radius:50%; animation:spin .6s linear infinite; }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
  .theme-toggle {{ position:fixed; top:16px; right:16px; cursor:pointer; font-size:20px;
    background:none; border:none; padding:4px 8px; border-radius:6px; }}
  .theme-toggle:hover {{ background:#e5e7eb; }}
  body.dark {{ background:#111827; color:#e5e7eb; }}
  body.dark .panel, body.dark .stat {{ background:#1f2937; border-color:#374151; }}
  body.dark h1 {{ color:#f9fafb; }}
  body.dark h2 {{ color:#d1d5db; }}
  body.dark th {{ background:#374151; color:#d1d5db; border-color:#4b5563; }}
  body.dark td {{ border-color:#374151; }}
  body.dark select, body.dark input {{ background:#374151; color:#e5e7eb; border-color:#4b5563; }}
  body.dark .btn-sm {{ background:#374151; border-color:#4b5563; color:#e5e7eb; }}
  body.dark .btn-sm:hover {{ background:#4b5563; }}
  body.dark .theme-toggle:hover {{ background:#374151; }}
</style>
</head>
<body>
<button class="theme-toggle" onclick="toggleTheme()">🌓</button>
<div class="container">
  <h1>pim-check Dashboard
    <span class="badge" style="background:{auto_color}">{auto_status}</span>
  </h1>

  <div class="stats">
    <div class="stat"><div class="stat-value">{total_runs}</div><div class="stat-label">Total Runs</div></div>
    <div class="stat"><div class="stat-value" style="color:#22c55e">{total_pass}</div><div class="stat-label">Passed</div></div>
    <div class="stat"><div class="stat-value" style="color:#ef4444">{total_runs - total_pass}</div><div class="stat-label">Failed</div></div>
    <div class="stat"><div class="stat-value" style="color:{pass_color}">{pass_rate:.0f}%</div><div class="stat-label">Pass Rate</div></div>
  </div>

  <div class="panel">
    <h2>Run Test</h2>
    <div class="controls">
      <select id="case">{case_options}</select>
      <input id="host" type="text" value="192.168.0.5" placeholder="Target IP" style="width:140px">
      <button class="btn btn-primary" onclick="runSelected()">Run Now</button>
      <span style="color:#9ca3af">|</span>
      <input id="interval" type="number" value="300" min="30" style="width:70px"> sec
      <button class="btn btn-success" onclick="startAuto()">Auto Start</button>
      <button class="btn btn-danger" onclick="stopAuto()">Stop</button>
    </div>
    <div class="controls" style="margin-top:8px">
      <span style="color:#6b7280;font-size:13px">Tag:</span>
      <button class="btn btn-sm" onclick="runTag('smoke')">Run Smoke</button>
      <button class="btn btn-sm" onclick="runTag('camera')">Run Camera</button>
      <button class="btn btn-sm" onclick="runTag('stress')">Run Stress</button>
    </div>
    <div id="status"></div>
  </div>

  <div class="panel">
    <h2>Case Summary</h2>
    <table>
      <thead><tr><th>Case</th><th>Last</th><th>Checks</th><th>Time</th><th></th></tr></thead>
      <tbody>{case_rows}</tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Recent Runs</h2>
    <table>
      <thead><tr><th>Time</th><th>Case</th><th>Result</th><th>Checks</th></tr></thead>
      <tbody id="recent">{recent_rows}</tbody>
    </table>
  </div>
</div>

<script>
function showStatus(msg, color) {{
  const s = document.getElementById('status');
  s.style.display = 'block';
  s.style.background = color || '#eff6ff';
  s.style.color = '#1e40af';
  s.innerHTML = msg;
}}

function runTest(caseName) {{
  const host = document.getElementById('host').value;
  showStatus('<span class="spinner"></span> Running ' + caseName + '...');
  fetch('/api/run?case=' + caseName + '&host=' + host)
    .then(r => r.json())
    .then(data => {{
      const color = data.status === 'PASS' ? '#f0fdf4' : data.status === 'WARN' ? '#fffbeb' : '#fef2f2';
      showStatus(data.status + ': ' + (data.case||'healthcheck') + ' (' + data.passed + '/' + data.total + ')', color);
      setTimeout(() => location.reload(), 1500);
    }})
    .catch(e => showStatus('Error: ' + e, '#fef2f2'));
}}

function runSelected() {{
  const caseName = document.getElementById('case').value;
  runTest(caseName);
}}

function startAuto() {{
  const caseName = document.getElementById('case').value;
  const host = document.getElementById('host').value;
  const interval = document.getElementById('interval').value;
  fetch('/api/auto/start?case=' + caseName + '&host=' + host + '&interval=' + interval)
    .then(r => r.json())
    .then(() => {{ showStatus('Auto mode started (' + interval + 's interval)', '#f0fdf4'); setTimeout(() => location.reload(), 1000); }});
}}

function stopAuto() {{
  fetch('/api/auto/stop').then(r => r.json())
    .then(() => {{ showStatus('Auto mode stopped', '#fffbeb'); setTimeout(() => location.reload(), 1000); }});
}}

function runTag(tag) {{
  const host = document.getElementById('host').value;
  showStatus('<span class="spinner"></span> Running all [' + tag + '] cases...');
  fetch('/api/run-tag?tag=' + tag + '&host=' + host)
    .then(r => r.json())
    .then(data => {{
      const passed = data.results.filter(r => r.status === 'PASS' || r.status === 'WARN').length;
      const color = passed === data.count ? '#f0fdf4' : '#fef2f2';
      showStatus('[' + tag + '] ' + passed + '/' + data.count + ' cases OK', color);
      setTimeout(() => location.reload(), 1500);
    }})
    .catch(e => showStatus('Error: ' + e, '#fef2f2'));
}}

function toggleTheme() {{
  document.body.classList.toggle('dark');
  localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light');
}}
if (localStorage.getItem('theme') === 'dark') document.body.classList.add('dark');

// 자동 새로고침 (30초)
setTimeout(() => location.reload(), 30000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    AUTH = None  # "user:pass" or None

    def _check_auth(self) -> bool:
        if not self.AUTH:
            return True
        import base64
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            return decoded == self.AUTH
        except Exception:
            return False

    def do_GET(self):
        if not self._check_auth():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="pim-check"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/dashboard":
            html = _build_dashboard_html()
            self._respond(200, html, "text/html")

        elif path == "/api/run":
            case = params.get("case", [None])[0]
            host = params.get("host", ["192.168.0.5"])[0]
            result = _run_test(case, host)
            self._respond(200, json.dumps(result), "application/json")

        elif path == "/api/auto/start":
            case = params.get("case", [None])[0]
            host = params.get("host", ["192.168.0.5"])[0]
            interval = int(params.get("interval", ["300"])[0])
            _auto_state["running"] = True
            _auto_state["case"] = case
            _auto_state["host"] = host
            _auto_state["interval"] = interval
            if _auto_state["thread"] is None or not _auto_state["thread"].is_alive():
                t = threading.Thread(target=_auto_runner, daemon=True)
                _auto_state["thread"] = t
                t.start()
            self._respond(200, json.dumps({"status": "started"}), "application/json")

        elif path == "/api/auto/stop":
            _auto_state["running"] = False
            self._respond(200, json.dumps({"status": "stopped"}), "application/json")

        elif path == "/api/status":
            self._respond(200, json.dumps({
                "auto": _auto_state["running"],
                "active": _run_state["active"],
                "case": _run_state["case"],
            }), "application/json")

        elif path == "/api/history":
            history = read_history(REPORTS_DIR)
            case_filter = params.get("case", [None])[0]
            if case_filter:
                history = [e for e in history if e.get("case") == case_filter]
            self._respond(200, json.dumps(history[-50:]), "application/json")

        elif path == "/api/cases":
            cases = _list_cases()
            self._respond(200, json.dumps(cases), "application/json")

        elif path == "/api/run-tag":
            tag = params.get("tag", [None])[0]
            host = params.get("host", ["192.168.0.5"])[0]
            if not tag:
                self._respond(400, json.dumps({"error": "tag required"}), "application/json")
                return
            import yaml as _yaml
            cases = _list_cases()
            tagged = []
            for c in cases:
                for subdir in ["cases", "generated"]:
                    p = os.path.join(PROFILES_DIR, subdir, f"{c}.yaml")
                    if os.path.exists(p):
                        with open(p) as f:
                            data = _yaml.safe_load(f) or {}
                        if tag in data.get("tags", []):
                            tagged.append(c)
                        break
            results = []
            for c in tagged:
                r = _run_test(c, host)
                results.append(r)
            self._respond(200, json.dumps({
                "tag": tag,
                "count": len(tagged),
                "results": results,
            }), "application/json")

        elif path == "/api/case-detail":
            case = params.get("case", [None])[0]
            history = read_history(REPORTS_DIR, case_filter=case)
            last = history[-1] if history else None
            self._respond(200, json.dumps({
                "case": case,
                "runs": len(history),
                "last": last,
                "history": history[-10:],
            }), "application/json")

        else:
            self._respond(404, "Not Found", "text/plain")

    def _respond(self, code: int, body: str, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        # 간결한 로그
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="pim-check web dashboard")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--auth", type=str, default=None, metavar="USER:PASS",
                        help="Basic Auth 활성화 (예: --auth admin:secret)")
    args = parser.parse_args()

    if args.auth:
        DashboardHandler.AUTH = args.auth

    os.makedirs(REPORTS_DIR, exist_ok=True)
    server = HTTPServer((args.host, args.port), DashboardHandler)
    print(f"pim-check dashboard: http://{args.host}:{args.port}")
    print("Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        _auto_state["running"] = False
        server.server_close()


if __name__ == "__main__":
    main()
