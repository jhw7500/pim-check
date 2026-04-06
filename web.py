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
    """자동 실행 스레드. single 또는 rotate 모드."""
    while _auto_state["running"]:
        mode = _auto_state.get("mode", "single")
        host = _auto_state["host"]
        interval = _auto_state["interval"]

        if mode == "rotate":
            # 모든 케이스를 순회하면서 테스트
            tag = _auto_state.get("tag")
            cases = _list_cases()
            if tag:
                import yaml as _yaml
                filtered = []
                for c in cases:
                    for subdir in ["cases", "generated"]:
                        p = os.path.join(PROFILES_DIR, subdir, f"{c}.yaml")
                        if os.path.exists(p):
                            with open(p) as f:
                                data = _yaml.safe_load(f) or {}
                            if tag in data.get("tags", []):
                                filtered.append(c)
                            break
                cases = filtered

            for case in cases:
                if not _auto_state["running"]:
                    break
                try:
                    _run_test(case, host)
                except Exception:
                    pass
        else:
            # 단일 케이스 반복
            try:
                _run_test(_auto_state["case"], host)
            except Exception:
                pass

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
        case_rows += f'<tr><td><a href="/case/{name}" style="color:#3b82f6">{name}</a></td><td style="color:{color};font-weight:bold">{status}</td>'
        case_rows += f'<td>{e.get("passed",0)}/{e.get("total",0)}</td><td>{ts}</td>'
        case_rows += f'<td><button onclick="runTest(\'{name}\')" class="btn btn-sm">Run</button></td></tr>\n'

    # 최근 10건
    # 체크박스 목록 (카테고리별)
    case_groups = {"normal": [], "fault": [], "verify": [], "config": [], "generated": [], "other": []}
    for c in cases:
        if c.startswith("gen_"):
            case_groups["generated"].append(c)
        elif c.startswith("fault_"):
            case_groups["fault"].append(c)
        elif c.startswith("verify_"):
            case_groups["verify"].append(c)
        elif c.startswith("config_") or c.startswith("board_"):
            case_groups["config"].append(c)
        elif c in ("720p_2ch", "720p_4ch", "fhd_4ch", "rtsp_off"):
            case_groups["normal"].append(c)
        else:
            case_groups["other"].append(c)

    group_labels = {"normal": "Normal", "fault": "Fault", "verify": "Verify", "config": "Config", "generated": "Auto-Generated", "other": "Other"}
    checkbox_html = ""
    for grp, label in group_labels.items():
        items = case_groups[grp]
        if not items:
            continue
        checkbox_html += f'<div style="margin-bottom:8px"><div style="font-size:11px;color:#64748b;text-transform:uppercase;margin-bottom:4px;display:flex;align-items:center;gap:6px">{label} ({len(items)}) <label style="font-size:10px;cursor:pointer"><input type="checkbox" onchange="toggleGroup(this,\'{grp}\')" style="margin-right:2px">all</label></div>'
        for c in items:
            last = case_stats.get(c)
            if last:
                st = last["result"]
                dot = "#22c55e" if st == "PASS" else "#f59e0b" if st == "WARN" else "#ef4444"
            else:
                dot = "#475569"
            checkbox_html += f'<label style="display:inline-flex;align-items:center;gap:4px;margin:2px 8px 2px 0;font-size:12px;cursor:pointer"><input type="checkbox" class="case-cb grp-{grp}" value="{c}"><span style="width:6px;height:6px;border-radius:50%;background:{dot};display:inline-block"></span>{c}</label>'
        checkbox_html += '</div>'

    recent_rows = ""
    for e in reversed(history[-10:]):
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        case = e.get("case") or "healthcheck"
        status = e["result"]
        color = "#22c55e" if status == "PASS" else "#f59e0b" if status == "WARN" else "#ef4444"
        recent_rows += f'<tr><td>{ts}</td><td>{case}</td>'
        recent_rows += f'<td style="color:{color};font-weight:bold">{status}</td>'
        recent_rows += f'<td>{e.get("passed",0)}/{e.get("total",0)}</td></tr>\n'

    auto_mode = _auto_state.get("mode", "single")
    if _auto_state["running"]:
        auto_status = f"Running ({auto_mode})"
        auto_color = "#22c55e" if auto_mode == "single" else "#0891b2"
    else:
        auto_status = "Stopped"
        auto_color = "#6b7280"
    pass_color = "#22c55e" if pass_rate >= 80 else "#f59e0b" if pass_rate >= 50 else "#ef4444"

    # 추가 통계
    total_cases = len(cases)
    last_run_ts = history[-1].get("timestamp", "")[:19].replace("T", " ") if history else "Never"
    last_host = history[-1].get("host", "") if history else ""
    warn_count = sum(1 for e in history if e["result"] == "WARN")

    # 최근 실패 체크 상세
    failed_detail = ""
    if history:
        last = history[-1]
        for name, passed in last.get("checks", {}).items():
            if not passed:
                failed_detail += f'<span style="display:inline-block;background:#fef2f2;color:#dc2626;padding:2px 8px;border-radius:4px;font-size:12px;margin:2px">{name}</span>'

    # 추이 미니 차트 (최근 20건 SVG)
    mini_chart = ""
    if len(history) >= 2:
        n = min(len(history), 20)
        recent_h = history[-n:]
        chart_w, chart_h = 200, 40
        pts = []
        for i, e in enumerate(recent_h):
            x = int(i / (n - 1) * (chart_w - 4)) + 2 if n > 1 else chart_w // 2
            r = (e.get("passed", 0) / max(e.get("total", 1), 1)) * 100
            y = int(chart_h - 2 - (r / 100 * (chart_h - 4)))
            color = "#22c55e" if e["result"] == "PASS" else "#f59e0b" if e["result"] == "WARN" else "#ef4444"
            pts.append((x, y, color))
        polyline = " ".join(f"{x},{y}" for x, y, _ in pts)
        dots = "".join(f'<circle cx="{x}" cy="{y}" r="2.5" fill="{c}"/>' for x, y, c in pts)
        mini_chart = f'<svg width="{chart_w}" height="{chart_h}" style="vertical-align:middle"><polyline points="{polyline}" fill="none" stroke="#94a3b8" stroke-width="1.5"/>{dots}</svg>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>pim-check Dashboard</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,'Segoe UI',system-ui,sans-serif; background:#0f172a; color:#e2e8f0; }}
  .header {{ background:linear-gradient(135deg,#1e3a5f 0%,#0f172a 100%); padding:24px 32px; border-bottom:1px solid #1e293b; }}
  .header-top {{ display:flex; justify-content:space-between; align-items:center; max-width:1100px; margin:0 auto; }}
  .header h1 {{ font-size:20px; color:#f1f5f9; font-weight:600; display:flex; align-items:center; gap:10px; }}
  .header .meta {{ font-size:12px; color:#64748b; margin-top:6px; }}
  .badge {{ font-size:11px; padding:3px 10px; border-radius:12px; color:#fff; font-weight:500; }}
  .container {{ max-width:1100px; margin:0 auto; padding:20px 32px; }}
  .grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin-bottom:20px; }}
  .stat {{ background:#1e293b; border:1px solid #334155; border-radius:10px; padding:16px; text-align:center; }}
  .stat-value {{ font-size:26px; font-weight:700; }}
  .stat-label {{ font-size:11px; color:#64748b; margin-top:4px; text-transform:uppercase; letter-spacing:0.5px; }}
  .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
  .panel {{ background:#1e293b; border:1px solid #334155; border-radius:10px; padding:16px; margin-bottom:16px; }}
  .panel h2 {{ font-size:13px; color:#94a3b8; margin-bottom:12px; text-transform:uppercase; letter-spacing:0.5px; font-weight:600; }}
  .controls {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
  select, input {{ padding:7px 12px; border:1px solid #334155; border-radius:6px; font-size:13px; background:#0f172a; color:#e2e8f0; }}
  select:focus, input:focus {{ outline:none; border-color:#3b82f6; }}
  .btn {{ padding:7px 16px; border:none; border-radius:6px; cursor:pointer; font-size:13px; font-weight:500; transition:all .15s; }}
  .btn-primary {{ background:#3b82f6; color:#fff; }}
  .btn-primary:hover {{ background:#2563eb; transform:translateY(-1px); }}
  .btn-live {{ background:#8b5cf6; color:#fff; }}
  .btn-live:hover {{ background:#7c3aed; }}
  .btn-danger {{ background:#ef4444; color:#fff; }}
  .btn-danger:hover {{ background:#dc2626; }}
  .btn-success {{ background:#22c55e; color:#fff; }}
  .btn-success:hover {{ background:#16a34a; }}
  .btn-sm {{ padding:4px 10px; font-size:11px; background:#334155; border:1px solid #475569; color:#cbd5e1; border-radius:4px; }}
  .btn-sm:hover {{ background:#475569; }}
  table {{ width:100%; border-collapse:separate; border-spacing:0; }}
  th {{ text-align:left; padding:8px 12px; font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; border-bottom:1px solid #334155; }}
  td {{ padding:8px 12px; border-bottom:1px solid #1e293b; font-size:13px; }}
  tr:hover td {{ background:#0f172a; }}
  a {{ color:#60a5fa; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  #status {{ padding:10px 14px; border-radius:8px; margin-top:12px; display:none; font-size:13px; }}
  .spinner {{ display:inline-block; width:14px; height:14px; border:2px solid #475569; border-top-color:#3b82f6; border-radius:50%; animation:spin .6s linear infinite; }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
  .theme-toggle {{ position:fixed; top:16px; right:16px; cursor:pointer; font-size:18px;
    background:#1e293b; border:1px solid #334155; padding:6px 10px; border-radius:8px; color:#94a3b8; }}
  .theme-toggle:hover {{ background:#334155; color:#e2e8f0; }}
  .tag-label {{ display:inline-block; background:#334155; color:#94a3b8; padding:1px 6px; border-radius:3px; font-size:11px; margin-left:4px; }}
  .alert-bar {{ background:#7f1d1d; border:1px solid #991b1b; border-radius:8px; padding:10px 16px; margin-bottom:16px; font-size:13px; color:#fca5a5; display:flex; align-items:center; gap:8px; }}
  .alert-bar.ok {{ background:#052e16; border-color:#166534; color:#86efac; }}

  /* Light mode */
  body.light {{ background:#f8fafc; color:#1e293b; }}
  body.light .header {{ background:linear-gradient(135deg,#dbeafe 0%,#f8fafc 100%); border-color:#e2e8f0; }}
  body.light .header h1 {{ color:#1e293b; }}
  body.light .header .meta {{ color:#64748b; }}
  body.light .stat, body.light .panel {{ background:#fff; border-color:#e2e8f0; }}
  body.light th {{ color:#64748b; border-color:#e2e8f0; }}
  body.light td {{ border-color:#f1f5f9; }}
  body.light tr:hover td {{ background:#f8fafc; }}
  body.light select, body.light input {{ background:#fff; color:#1e293b; border-color:#d1d5db; }}
  body.light .btn-sm {{ background:#f1f5f9; border-color:#d1d5db; color:#475569; }}
  body.light .btn-sm:hover {{ background:#e2e8f0; }}
  body.light .theme-toggle {{ background:#fff; border-color:#d1d5db; color:#64748b; }}
  body.light .stat-label {{ color:#64748b; }}
  body.light a {{ color:#2563eb; }}
  body.light .alert-bar {{ background:#fef2f2; border-color:#fecaca; color:#991b1b; }}
  body.light .alert-bar.ok {{ background:#f0fdf4; border-color:#bbf7d0; color:#166534; }}
</style>
</head>
<body>
<button class="theme-toggle" onclick="toggleTheme()">&#9681;</button>

<div class="header">
  <div class="header-top">
    <div>
      <h1>pim-check
        <span class="badge" style="background:{auto_color}">{auto_status}</span>
        <span style="font-size:12px;color:#64748b;font-weight:400">v2.0.0</span>
      </h1>
      <div class="meta">iMX8MP QA Automation &middot; Last: {last_run_ts} &middot; Host: {last_host or 'N/A'} &middot; {total_cases} cases available</div>
    </div>
    <div style="text-align:right">
      {mini_chart}
      <div style="font-size:11px;color:#64748b;margin-top:4px">Pass rate trend (last 20)</div>
    </div>
  </div>
</div>

<div class="container">
  {"<div class='alert-bar'><strong>FAIL</strong> Last run failed &middot; " + failed_detail + "</div>" if history and history[-1]["result"] not in ("PASS","WARN") else "<div class='alert-bar ok'><strong>OK</strong> System healthy</div>" if history else ""}

  <div class="grid">
    <div class="stat"><div class="stat-value">{total_runs}</div><div class="stat-label">Total Runs</div></div>
    <div class="stat"><div class="stat-value" style="color:#22c55e">{total_pass}</div><div class="stat-label">Passed</div></div>
    <div class="stat"><div class="stat-value" style="color:#f59e0b">{warn_count}</div><div class="stat-label">Warnings</div></div>
    <div class="stat"><div class="stat-value" style="color:#ef4444">{total_runs - total_pass - warn_count}</div><div class="stat-label">Failed</div></div>
    <div class="stat"><div class="stat-value" style="color:{pass_color}">{pass_rate:.0f}%</div><div class="stat-label">Pass Rate</div></div>
  </div>

  <div class="panel">
    <h2>Run Test</h2>
    <div class="controls">
      <select id="case">{case_options}</select>
      <input id="host" type="text" value="{last_host or '192.168.0.5'}" placeholder="Target IP" style="width:140px">
      <button class="btn btn-primary" onclick="runSelected()">Run Now</button>
      <button class="btn btn-live" onclick="runLive()">Run Live</button>
      <span style="color:#475569">|</span>
      <input id="interval" type="number" value="300" min="30" style="width:70px"> sec
      <button class="btn btn-success" onclick="startAuto('single')">Auto Single</button>
      <button class="btn" style="background:#0891b2;color:#fff" onclick="startAuto('rotate')">Auto Rotate</button>
      <button class="btn btn-danger" onclick="stopAuto()">Stop</button>
    </div>
    <div class="controls" style="margin-top:8px">
      <span style="color:#64748b;font-size:12px">Tags:</span>
      <button class="btn btn-sm" onclick="runTag('smoke')">smoke</button>
      <button class="btn btn-sm" onclick="runTag('camera')">camera</button>
      <button class="btn btn-sm" onclick="runTag('stress')">stress</button>
      <span style="color:#475569">|</span>
      <button class="btn" style="background:#f97316;color:#fff;font-size:12px;padding:4px 12px" onclick="runSelectedCases()">Run Selected</button>
      <button class="btn btn-sm" onclick="selectAll(true)">Select All</button>
      <button class="btn btn-sm" onclick="selectAll(false)">Clear</button>
    </div>
    <div style="margin-top:10px;max-height:200px;overflow-y:auto;padding:8px;background:#0f172a;border-radius:6px;border:1px solid #334155">
      {checkbox_html}
    </div>
    <div id="status"></div>
  </div>

  <div class="two-col">
    <div class="panel">
      <h2>Case Summary ({len(case_stats)} cases)</h2>
      <table>
        <thead><tr><th>Case</th><th>Status</th><th>Checks</th><th>Last Run</th><th></th></tr></thead>
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

function startAuto(mode) {{
  const caseName = document.getElementById('case').value;
  const host = document.getElementById('host').value;
  const interval = document.getElementById('interval').value;
  const label = mode === 'rotate' ? 'Rotate (all cases)' : 'Single (' + caseName + ')';
  fetch('/api/auto/start?case=' + caseName + '&host=' + host + '&interval=' + interval + '&mode=' + mode)
    .then(r => r.json())
    .then(() => {{ showStatus('Auto ' + label + ' started (' + interval + 's)', '#f0fdf4'); setTimeout(() => location.reload(), 1000); }});
}}

function stopAuto() {{
  fetch('/api/auto/stop').then(r => r.json())
    .then(() => {{ showStatus('Auto mode stopped', '#fffbeb'); setTimeout(() => location.reload(), 1000); }});
}}

function runLive() {{
  const caseName = document.getElementById('case').value;
  const host = document.getElementById('host').value;
  let log = document.getElementById('livelog');
  if (!log) {{
    log = document.createElement('div');
    log.id = 'livelog';
    log.style.cssText = 'margin-top:12px;background:#111827;color:#e5e7eb;border-radius:8px;padding:12px;font-family:monospace;font-size:13px;max-height:300px;overflow-y:auto';
    document.getElementById('status').parentNode.appendChild(log);
  }}
  log.style.display = 'block';
  log.innerHTML = '';
  const es = new EventSource('/api/stream?case=' + caseName + '&host=' + host);
  es.addEventListener('start', e => {{ const d = JSON.parse(e.data); log.innerHTML += '<div style="color:#60a5fa">' + d.message + '</div>'; }});
  es.addEventListener('phase', e => {{ const d = JSON.parse(e.data); const c = d.ok ? '#22c55e' : '#fbbf24'; log.innerHTML += '<div style="color:' + c + '">[' + d.phase + '] ' + d.message + '</div>'; }});
  es.addEventListener('check_start', e => {{ const d = JSON.parse(e.data); log.innerHTML += '<div style="color:#9ca3af">  checking ' + d.check + '...</div>'; }});
  es.addEventListener('check_result', e => {{ const d = JSON.parse(e.data); const c = d.passed ? '#22c55e' : d.known_issue ? '#fbbf24' : '#ef4444'; const s = d.passed ? 'PASS' : d.known_issue ? 'WARN' : 'FAIL'; log.innerHTML += '<div style="color:' + c + '">  ' + s + ' ' + d.check + ' (' + d.duration_ms + 'ms)' + (d.reason && !d.passed ? ' - ' + d.reason : '') + '</div>'; }});
  es.addEventListener('warning', e => {{ const d = JSON.parse(e.data); log.innerHTML += '<div style="color:#fbbf24">WARNING: ' + d.message + '</div>'; }});
  es.addEventListener('error', e => {{ const d = JSON.parse(e.data); log.innerHTML += '<div style="color:#ef4444">ERROR: ' + d.message + '</div>'; }});
  es.addEventListener('done', e => {{ const d = JSON.parse(e.data); const c = d.status === 'PASS' ? '#22c55e' : d.status === 'WARN' ? '#fbbf24' : '#ef4444'; log.innerHTML += '<div style="color:' + c + ';font-weight:bold;margin-top:8px">' + d.message + '</div>'; es.close(); setTimeout(() => location.reload(), 3000); }});
  es.onerror = () => {{ log.innerHTML += '<div style="color:#ef4444">Connection lost</div>'; es.close(); }};
  setInterval(() => {{ log.scrollTop = log.scrollHeight; }}, 500);
}}

function getSelectedCases() {{
  return Array.from(document.querySelectorAll('.case-cb:checked')).map(cb => cb.value);
}}
function runSelectedCases() {{
  const cases = getSelectedCases();
  if (cases.length === 0) {{ showStatus('No cases selected', '#fffbeb'); return; }}
  const host = document.getElementById('host').value;
  showStatus('<span class="spinner"></span> Running ' + cases.length + ' cases...');
  fetch('/api/run-selected?host=' + host + '&cases=' + cases.join(','))
    .then(r => r.json())
    .then(data => {{
      const ok = data.results.filter(r => r.status === 'PASS' || r.status === 'WARN').length;
      const color = ok === data.count ? '#f0fdf4' : '#fef2f2';
      showStatus(ok + '/' + data.count + ' cases OK', color);
      setTimeout(() => location.reload(), 1500);
    }})
    .catch(e => showStatus('Error: ' + e, '#fef2f2'));
}}
function selectAll(checked) {{
  document.querySelectorAll('.case-cb').forEach(cb => cb.checked = checked);
}}
function toggleGroup(master, grp) {{
  document.querySelectorAll('.grp-' + grp).forEach(cb => cb.checked = master.checked);
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
  document.body.classList.toggle('light');
  localStorage.setItem('theme', document.body.classList.contains('light') ? 'light' : 'dark');
}}
if (localStorage.getItem('theme') === 'light') document.body.classList.add('light');

// 자동 새로고침 (30초)
setTimeout(() => location.reload(), 30000);
</script>
</body>
</html>"""


def _build_prometheus_metrics() -> str:
    """Prometheus exposition format 메트릭을 생성한다."""
    history = read_history(REPORTS_DIR)
    total_runs = len(history)
    total_pass = sum(1 for e in history if e["result"] == "PASS")
    total_fail = total_runs - total_pass

    # 케이스별 집계
    case_stats: dict[str, dict] = {}
    for e in history:
        case = e.get("case") or "healthcheck"
        if case not in case_stats:
            case_stats[case] = {"pass": 0, "fail": 0, "total": 0}
        case_stats[case]["total"] += 1
        if e["result"] == "PASS":
            case_stats[case]["pass"] += 1
        else:
            case_stats[case]["fail"] += 1

    lines = [
        "# HELP pimcheck_runs_total Total test runs",
        "# TYPE pimcheck_runs_total counter",
        f"pimcheck_runs_total {total_runs}",
        "",
        "# HELP pimcheck_runs_passed Total passed runs",
        "# TYPE pimcheck_runs_passed counter",
        f"pimcheck_runs_passed {total_pass}",
        "",
        "# HELP pimcheck_runs_failed Total failed runs",
        "# TYPE pimcheck_runs_failed counter",
        f"pimcheck_runs_failed {total_fail}",
        "",
        "# HELP pimcheck_pass_rate Overall pass rate",
        "# TYPE pimcheck_pass_rate gauge",
        f"pimcheck_pass_rate {(total_pass / total_runs) if total_runs else 0:.4f}",
        "",
        "# HELP pimcheck_case_runs_total Runs per case",
        "# TYPE pimcheck_case_runs_total counter",
    ]
    for case, stats in sorted(case_stats.items()):
        lines.append(f'pimcheck_case_runs_total{{case="{case}"}} {stats["total"]}')
    lines.append("")
    lines.append("# HELP pimcheck_case_pass_rate Pass rate per case")
    lines.append("# TYPE pimcheck_case_pass_rate gauge")
    for case, stats in sorted(case_stats.items()):
        rate = stats["pass"] / stats["total"] if stats["total"] else 0
        lines.append(f'pimcheck_case_pass_rate{{case="{case}"}} {rate:.4f}')
    lines.append("")

    # 자동 실행 상태
    lines.append("# HELP pimcheck_auto_running Auto mode status")
    lines.append("# TYPE pimcheck_auto_running gauge")
    lines.append(f"pimcheck_auto_running {1 if _auto_state['running'] else 0}")
    lines.append("")

    return "\n".join(lines) + "\n"


def _build_case_detail_html(case_name: str) -> str:
    """케이스 상세 페이지 HTML."""
    history = read_history(REPORTS_DIR, case_filter=case_name)
    total_runs = len(history)
    pass_count = sum(1 for e in history if e["result"] == "PASS")
    pass_rate = (pass_count / total_runs * 100) if total_runs else 0
    pass_color = "#22c55e" if pass_rate >= 80 else "#f59e0b" if pass_rate >= 50 else "#ef4444"

    # 체크별 최근 결과
    check_rows = ""
    if history:
        last = history[-1]
        for name, passed in last.get("checks", {}).items():
            color = "#22c55e" if passed else "#ef4444"
            icon = "&#10004;" if passed else "&#10008;"
            check_rows += f'<tr><td style="color:{color};text-align:center">{icon}</td><td>{name}</td><td style="color:{color}">{"PASS" if passed else "FAIL"}</td></tr>\n'

    # 이력 테이블
    history_rows = ""
    for e in reversed(history[-20:]):
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        status = e["result"]
        color = "#22c55e" if status == "PASS" else "#f59e0b" if status == "WARN" else "#ef4444"
        history_rows += f'<tr><td>{ts}</td><td style="color:{color};font-weight:bold">{status}</td><td>{e.get("passed",0)}/{e.get("total",0)}</td><td>{e.get("host","")}</td></tr>\n'

    # 추이 차트 (인라인 SVG)
    chart_svg = ""
    if len(history) >= 2:
        w, h = 600, 120
        points = []
        n = min(len(history), 30)
        recent = history[-n:]
        for i, e in enumerate(recent):
            x = int(i / (n - 1) * (w - 40)) + 20 if n > 1 else w // 2
            rate = (e.get("passed", 0) / max(e.get("total", 1), 1)) * 100
            y = int(h - 20 - (rate / 100 * (h - 40)))
            points.append((x, y, rate, e.get("timestamp", "")[:10]))

        polyline = " ".join(f"{x},{y}" for x, y, _, _ in points)
        dots = ""
        for x, y, rate, ts in points:
            color = "#22c55e" if rate >= 80 else "#f59e0b" if rate >= 50 else "#ef4444"
            dots += f'<circle cx="{x}" cy="{y}" r="4" fill="{color}"><title>{ts}: {rate:.0f}%</title></circle>'

        chart_svg = f"""<svg width="{w}" height="{h}" style="background:#fff;border:1px solid #e5e7eb;border-radius:8px">
  <polyline points="{polyline}" fill="none" stroke="#3b82f6" stroke-width="2"/>
  <line x1="20" y1="{h-20}" x2="{w-20}" y2="{h-20}" stroke="#e5e7eb"/>
  <text x="2" y="15" font-size="10" fill="#9ca3af">100%</text>
  <text x="2" y="{h-10}" font-size="10" fill="#9ca3af">0%</text>
  {dots}
</svg>"""

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>pim-check: {case_name}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,system-ui,sans-serif; background:#f9fafb; padding:24px; }}
  .container {{ max-width:800px; margin:0 auto; }}
  h1 {{ font-size:20px; margin-bottom:16px; }}
  a {{ color:#3b82f6; text-decoration:none; }}
  .stats {{ display:flex; gap:12px; margin-bottom:16px; }}
  .stat {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:14px; flex:1; text-align:center; }}
  .stat-value {{ font-size:24px; font-weight:700; }}
  .stat-label {{ font-size:12px; color:#6b7280; margin-top:4px; }}
  .panel {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:16px; margin-bottom:16px; }}
  .panel h2 {{ font-size:15px; color:#374151; margin-bottom:10px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ background:#f3f4f6; text-align:left; padding:8px 12px; font-size:13px; color:#374151; border-bottom:1px solid #e5e7eb; }}
  td {{ padding:8px 12px; border-bottom:1px solid #f3f4f6; font-size:14px; }}
  .btn {{ padding:6px 14px; border:none; border-radius:6px; cursor:pointer; font-size:14px; background:#3b82f6; color:#fff; }}
</style></head><body>
<div class="container">
  <h1><a href="/">&larr; Dashboard</a> / {case_name}</h1>
  <div class="stats">
    <div class="stat"><div class="stat-value">{total_runs}</div><div class="stat-label">Runs</div></div>
    <div class="stat"><div class="stat-value" style="color:#22c55e">{pass_count}</div><div class="stat-label">Passed</div></div>
    <div class="stat"><div class="stat-value" style="color:#ef4444">{total_runs - pass_count}</div><div class="stat-label">Failed</div></div>
    <div class="stat"><div class="stat-value" style="color:{pass_color}">{pass_rate:.0f}%</div><div class="stat-label">Pass Rate</div></div>
  </div>
  {"<div class='panel'><h2>Pass Rate Trend</h2>" + chart_svg + "</div>" if chart_svg else ""}
  <div class="panel">
    <h2>Last Run — Checks</h2>
    <table><thead><tr><th style="width:30px"></th><th>Check</th><th>Result</th></tr></thead>
    <tbody>{check_rows}</tbody></table>
  </div>
  <div class="panel">
    <h2>Run History (last 20)</h2>
    <table><thead><tr><th>Time</th><th>Result</th><th>Checks</th><th>Host</th></tr></thead>
    <tbody>{history_rows}</tbody></table>
  </div>
  <input id="host" type="text" value="192.168.0.5" placeholder="Target IP" style="width:140px;padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px">
  <button class="btn" onclick="runLive('{case_name}')">Run Now (Live)</button>
  <div id="livelog" style="display:none;margin-top:12px;background:#111827;color:#e5e7eb;border-radius:8px;padding:12px;font-family:monospace;font-size:13px;max-height:300px;overflow-y:auto"></div>
  <script>
  function runLive(c) {{
    const log = document.getElementById('livelog');
    log.style.display = 'block';
    log.innerHTML = '';
    const host = document.getElementById('host').value || '192.168.0.5';
    const es = new EventSource('/api/stream?case=' + c + '&host=' + host);
    es.addEventListener('start', e => {{ const d = JSON.parse(e.data); log.innerHTML += '<div style="color:#60a5fa">' + d.message + '</div>'; }});
    es.addEventListener('phase', e => {{ const d = JSON.parse(e.data); const c = d.ok ? '#22c55e' : '#fbbf24'; log.innerHTML += '<div style="color:' + c + '">[' + d.phase + '] ' + d.message + '</div>'; }});
    es.addEventListener('check_start', e => {{ const d = JSON.parse(e.data); log.innerHTML += '<div style="color:#9ca3af">  checking ' + d.check + '...</div>'; }});
    es.addEventListener('check_result', e => {{ const d = JSON.parse(e.data); const c = d.passed ? '#22c55e' : d.known_issue ? '#fbbf24' : '#ef4444'; const s = d.passed ? 'PASS' : d.known_issue ? 'WARN' : 'FAIL'; log.innerHTML += '<div style="color:' + c + '">  ' + s + ' ' + d.check + ' (' + d.duration_ms + 'ms)' + (d.reason && !d.passed ? ' - ' + d.reason : '') + '</div>'; }});
    es.addEventListener('warning', e => {{ const d = JSON.parse(e.data); log.innerHTML += '<div style="color:#fbbf24">WARNING: ' + d.message + '</div>'; }});
    es.addEventListener('error', e => {{ const d = JSON.parse(e.data); log.innerHTML += '<div style="color:#ef4444">ERROR: ' + d.message + '</div>'; }});
    es.addEventListener('done', e => {{ const d = JSON.parse(e.data); const c = d.status === 'PASS' ? '#22c55e' : d.status === 'WARN' ? '#fbbf24' : '#ef4444'; log.innerHTML += '<div style="color:' + c + ';font-weight:bold;margin-top:8px">' + d.message + '</div>'; es.close(); setTimeout(() => location.reload(), 3000); }});
    es.onerror = () => {{ log.innerHTML += '<div style="color:#ef4444">Connection lost</div>'; es.close(); }};
    log.scrollTop = log.scrollHeight;
    setInterval(() => {{ log.scrollTop = log.scrollHeight; }}, 500);
  }}
  </script>
</div></body></html>"""


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

        elif path == "/api/run-selected":
            cases_str = params.get("cases", [""])[0]
            host = params.get("host", ["192.168.0.5"])[0]
            if not cases_str:
                self._respond(400, json.dumps({"error": "cases required"}), "application/json")
                return
            case_list = [c.strip() for c in cases_str.split(",") if c.strip()]
            results = []
            for c in case_list:
                r = _run_test(c, host)
                results.append(r)
            self._respond(200, json.dumps({
                "count": len(case_list),
                "results": results,
            }), "application/json")

        elif path == "/api/auto/start":
            case = params.get("case", [None])[0]
            host = params.get("host", ["192.168.0.5"])[0]
            interval = int(params.get("interval", ["300"])[0])
            mode = params.get("mode", ["single"])[0]  # single or rotate
            tag = params.get("tag", [None])[0]
            _auto_state["running"] = True
            _auto_state["case"] = case
            _auto_state["host"] = host
            _auto_state["interval"] = interval
            _auto_state["mode"] = mode
            _auto_state["tag"] = tag
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

        elif path.startswith("/case/"):
            case = path[6:]  # /case/720p_2ch → 720p_2ch
            html = _build_case_detail_html(case)
            self._respond(200, html, "text/html")

        elif path == "/api/stream":
            case = params.get("case", [None])[0]
            host = params.get("host", ["192.168.0.5"])[0]
            self._handle_stream(case, host)
            return

        elif path == "/metrics":
            metrics = _build_prometheus_metrics()
            self._respond(200, metrics, "text/plain")

        else:
            self._respond(404, "Not Found", "text/plain")

    def _handle_stream(self, case: str | None, host: str):
        """SSE 스트리밍으로 테스트 실행 로그를 실시간 전송."""
        from stream import StreamRunner, format_sse

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        runner = StreamRunner(
            case, host,
            profiles_dir=PROFILES_DIR,
            reports_dir=REPORTS_DIR,
        )
        runner.start()

        try:
            while True:
                try:
                    event = runner.events.get(timeout=30)
                    sse_text = format_sse(event["event"], event["data"])
                    self.wfile.write(sse_text.encode("utf-8"))
                    self.wfile.flush()
                    if event["event"] == "done":
                        break
                except Exception:
                    # 큐 타임아웃 또는 연결 끊김
                    self.wfile.write(format_sse("ping", {}).encode("utf-8"))
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

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
