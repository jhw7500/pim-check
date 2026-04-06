"""
history.py — 테스트 결과 히스토리 관리 (JSONL)

결과를 reports/history.jsonl에 한 줄씩 추가하여
시간 경과에 따른 추이를 추적한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime


def append_result(
    results: list,
    case_name: str | None,
    host: str = "",
    samples_collected: int = 1,
    samples_total: int = 1,
    history_dir: str = "reports",
) -> str:
    """결과를 history.jsonl에 추가한다. 파일 경로를 반환."""
    os.makedirs(history_dir, exist_ok=True)
    filepath = os.path.join(history_dir, "history.jsonl")

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    entry = {
        "timestamp": datetime.now().isoformat(),
        "host": host,
        "case": case_name,
        "result": "PASS" if passed == total else "FAIL",
        "passed": passed,
        "total": total,
        "samples_collected": samples_collected,
        "samples_total": samples_total,
        "checks": {r["name"]: r["passed"] for r in results},
    }

    with open(filepath, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return filepath


def generate_dashboard(history_dir: str = "reports") -> str:
    """히스토리 데이터로 HTML 대시보드를 생성한다."""
    entries = read_history(history_dir)

    if not entries:
        return "<html><body><p>No history data yet. Run tests with --history first.</p></body></html>"

    # 케이스별 최근 결과 집계
    case_stats: dict[str, list[dict]] = {}
    for e in entries:
        case = e.get("case") or "healthcheck"
        if case not in case_stats:
            case_stats[case] = []
        case_stats[case].append(e)

    # 전체 통계
    total_runs = len(entries)
    total_pass = sum(1 for e in entries if e["result"] == "PASS")
    total_fail = total_runs - total_pass
    pass_rate = (total_pass / total_runs * 100) if total_runs else 0

    # 케이스별 요약 행
    case_rows = ""
    for case_name in sorted(case_stats.keys()):
        runs = case_stats[case_name]
        c_pass = sum(1 for r in runs if r["result"] == "PASS")
        c_fail = len(runs) - c_pass
        last = runs[-1]
        last_status = last["result"]
        last_color = "#22c55e" if last_status == "PASS" else "#ef4444"
        last_ts = last.get("timestamp", "")[:19].replace("T", " ")
        rate = (c_pass / len(runs) * 100) if runs else 0
        case_rows += f"""<tr>
  <td>{case_name}</td>
  <td style="color:{last_color};font-weight:bold">{last_status}</td>
  <td>{c_pass}</td><td>{c_fail}</td><td>{len(runs)}</td>
  <td>{rate:.0f}%</td>
  <td style="color:#6b7280;font-size:13px">{last_ts}</td>
</tr>\n"""

    # 최근 실행 이력 (최대 30건)
    recent_rows = ""
    for e in reversed(entries[-30:]):
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        case = e.get("case") or "healthcheck"
        status = e["result"]
        color = "#22c55e" if status == "PASS" else "#ef4444"
        checks = f"{e.get('passed', 0)}/{e.get('total', 0)}"
        host = e.get("host", "")
        recent_rows += f"""<tr>
  <td style="color:#6b7280;font-size:13px">{ts}</td>
  <td>{case}</td>
  <td style="color:{color};font-weight:bold">{status}</td>
  <td>{checks}</td>
  <td style="color:#6b7280">{host}</td>
</tr>\n"""

    pass_color = "#22c55e" if pass_rate >= 80 else "#f59e0b" if pass_rate >= 50 else "#ef4444"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>pim-check History Dashboard</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,system-ui,sans-serif; background:#f9fafb; padding:24px; }}
  .container {{ max-width:900px; margin:0 auto; }}
  h1 {{ font-size:22px; color:#111827; margin-bottom:16px; }}
  h2 {{ font-size:16px; color:#374151; margin:20px 0 10px; }}
  .stats {{ display:flex; gap:12px; margin-bottom:20px; }}
  .stat {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:16px; flex:1; text-align:center; }}
  .stat-value {{ font-size:28px; font-weight:700; }}
  .stat-label {{ font-size:12px; color:#6b7280; margin-top:4px; }}
  table {{ width:100%; background:#fff; border:1px solid #e5e7eb; border-radius:8px;
           border-collapse:collapse; overflow:hidden; margin-bottom:16px; }}
  th {{ background:#f3f4f6; text-align:left; padding:10px 12px; font-size:13px;
       color:#374151; border-bottom:1px solid #e5e7eb; }}
  td {{ padding:8px 12px; border-bottom:1px solid #f3f4f6; font-size:14px; }}
  tr:last-child td {{ border-bottom:none; }}
  .footer {{ text-align:center; color:#9ca3af; font-size:12px; margin-top:16px; }}
</style>
</head>
<body>
<div class="container">
  <h1>pim-check History Dashboard</h1>
  <div class="stats">
    <div class="stat">
      <div class="stat-value">{total_runs}</div>
      <div class="stat-label">Total Runs</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color:#22c55e">{total_pass}</div>
      <div class="stat-label">Passed</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color:#ef4444">{total_fail}</div>
      <div class="stat-label">Failed</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color:{pass_color}">{pass_rate:.0f}%</div>
      <div class="stat-label">Pass Rate</div>
    </div>
  </div>

  <h2>Case Summary</h2>
  <table>
    <thead><tr><th>Case</th><th>Last</th><th>Pass</th><th>Fail</th><th>Runs</th><th>Rate</th><th>Last Run</th></tr></thead>
    <tbody>{case_rows}</tbody>
  </table>

  <h2>Recent Runs (last 30)</h2>
  <table>
    <thead><tr><th>Time</th><th>Case</th><th>Result</th><th>Checks</th><th>Host</th></tr></thead>
    <tbody>{recent_rows}</tbody>
  </table>

  <div class="footer">Generated by pim-check</div>
</div>
</body>
</html>"""


def save_dashboard(history_dir: str = "reports") -> str:
    """대시보드를 HTML 파일로 저장. 파일 경로를 반환."""
    os.makedirs(history_dir, exist_ok=True)
    html = generate_dashboard(history_dir)
    filepath = os.path.join(history_dir, "dashboard.html")
    with open(filepath, "w") as f:
        f.write(html)
    return filepath


def export_csv(history_dir: str = "reports", case_filter: str | None = None) -> str:
    """히스토리를 CSV 파일로 내보낸다. 파일 경로를 반환."""
    import csv

    entries = read_history(history_dir, case_filter)
    os.makedirs(history_dir, exist_ok=True)
    filepath = os.path.join(history_dir, "history.csv")

    # 모든 체크 이름 수집
    all_checks: list[str] = []
    for e in entries:
        for name in e.get("checks", {}).keys():
            if name not in all_checks:
                all_checks.append(name)

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["timestamp", "host", "case", "result", "passed", "total",
                  "samples_collected", "samples_total"] + all_checks
        writer.writerow(header)
        for e in entries:
            checks = e.get("checks", {})
            row = [
                e.get("timestamp", ""),
                e.get("host", ""),
                e.get("case", ""),
                e.get("result", ""),
                e.get("passed", 0),
                e.get("total", 0),
                e.get("samples_collected", 0),
                e.get("samples_total", 0),
            ] + [checks.get(c, "") for c in all_checks]
            writer.writerow(row)

    return filepath


def read_history(history_dir: str = "reports", case_filter: str | None = None) -> list[dict]:
    """히스토리를 읽어 dict 리스트로 반환한다."""
    filepath = os.path.join(history_dir, "history.jsonl")
    if not os.path.exists(filepath):
        return []

    entries = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if case_filter and entry.get("case") != case_filter:
                continue
            entries.append(entry)
    return entries
