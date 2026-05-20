#!/usr/bin/env python3
"""pim_web_viewer — events/current.jsonl 을 브라우저로 실시간 표시하는 경량 웹 뷰어.

stdlib http.server 만 사용(의존 없음). TUI(pim_viewer)와 동일한
viewer_state.ViewerState 를 재사용해 같은 JSONL 을 웹으로 보여준다
(시드 비전: 단일 이벤트 스트림을 TUI/웹/알림이 공유).

/state 는 매 요청마다 JSONL 을 처음부터 다시 접어(monotonic replay) 현재 상태와
producer-lost(파일 mtime 기반 — heartbeat 가 5초마다 파일을 갱신하므로 10초 무갱신
이면 producer 사망 추정)를 JSON 으로 반환한다. 브라우저는 1초마다 /state 를 폴링해
렌더하므로, 페이지를 새로 열어도 처음부터 상태가 복원된다(재접속 = state snapshot).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import run_stream
from viewer_state import ViewerState

PRODUCER_LOST_AFTER = 10.0


def _events_path(custom: str | None = None) -> str:
    return custom or os.path.join(run_stream.default_events_dir(),
                                  run_stream.CURRENT_SYMLINK_NAME)


def build_state(path: str) -> dict:
    """현재 JSONL 을 접어 웹 렌더용 평면 dict 를 만든다 (테스트 대상)."""
    if not os.path.exists(path):
        return {"exists": False}
    try:
        with open(path, encoding="utf-8") as f:
            st = ViewerState.from_lines(f)
        idle = time.time() - os.path.getmtime(path)
    except OSError:
        return {"exists": False}
    done, total = st.progress
    return {
        "exists": True,
        "plan": st.plan, "board": st.board, "run_id": st.run_id,
        "completed": done, "total": total,
        "pass": st.pass_count, "fail": st.fail_count,
        "current": st.current_case,
        "eta": round(st.eta_seconds, 1),
        "run_ended": st.run_ended,
        "producer_lost": (not st.run_ended) and idle > PRODUCER_LOST_AFTER,
        "idle_s": round(idle, 1),
        "cases": st.cases,
        "status": st.case_status,
        "fail_summaries": st.fail_summaries,
        "heartbeat_seq": st.last_heartbeat_seq,
    }


INDEX_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pim_viewer (web)</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0f1115; color:#e6e6e6;
         font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace; }
  .wrap { max-width: 880px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 16px; font-weight: 600; margin: 0 0 4px; color:#9ad; }
  .sub { color:#7a8; font-size: 12px; margin-bottom: 14px; }
  .badge { display:inline-block; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:700; }
  .b-run { background:#13351f; color:#4ade80; }
  .b-done { background:#1e2a44; color:#7aa2f7; }
  .b-lost { background:#3a1620; color:#f87171; }
  .bar { height: 18px; background:#1b1f2a; border-radius:9px; overflow:hidden; margin:10px 0 4px; }
  .bar > i { display:block; height:100%; background:linear-gradient(90deg,#3b82f6,#22d3ee); width:0; transition:width .4s; }
  .row { display:flex; gap:24px; flex-wrap:wrap; margin:10px 0; }
  .stat b { font-size: 22px; }
  .stat span { color:#8a93a6; font-size:12px; display:block; }
  .pass b { color:#4ade80; } .fail b { color:#f87171; }
  .fails { background:#1a1014; border:1px solid #3a2026; border-radius:8px; padding:8px 12px; margin:10px 0; }
  .fails div { color:#fca5a5; font-size:13px; padding:2px 0; }
  .cases { display:grid; grid-template-columns: repeat(auto-fill, minmax(220px,1fr)); gap:4px 14px; margin-top:8px; }
  .case { font-size:13px; padding:2px 0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .m-pass { color:#4ade80; } .m-fail { color:#f87171; } .m-run { color:#fbbf24; } .m-pend { color:#5b647a; }
  .cur { color:#fbbf24; font-weight:700; }
  .foot { color:#5b647a; font-size:11px; margin-top:14px; }
</style></head>
<body><div class="wrap">
  <h1 id="title">pim_viewer (web)</h1>
  <div class="sub" id="meta">waiting for event stream…</div>
  <div id="badge" class="badge b-run">…</div>
  <div class="bar"><i id="bar"></i></div>
  <div class="sub" id="prog">0 / 0 (0%)</div>
  <div class="row">
    <div class="stat pass"><b id="pass">0</b><span>PASS</span></div>
    <div class="stat fail"><b id="fail">0</b><span>FAIL</span></div>
    <div class="stat"><b id="cur">—</b><span>CURRENT</span></div>
    <div class="stat"><b id="eta">~0s</b><span>ETA</span></div>
  </div>
  <div class="fails" id="failsBox" style="display:none"><div id="fails"></div></div>
  <div class="cases" id="cases"></div>
  <div class="foot" id="foot"></div>
</div>
<script>
function fmtEta(s){ s=Math.max(0,Math.round(s||0)); return s<60?("~"+s+"s"):("~"+Math.floor(s/60)+"m "+(s%60)+"s"); }
async function tick(){
  try{
    const r = await fetch('/state?_='+Date.now(), {cache:'no-store'});
    const d = await r.json();
    const foot = document.getElementById('foot');
    if(!d.exists){ document.getElementById('meta').textContent='이벤트 스트림 없음 (pim_check.py --plan 실행 대기)'; foot.textContent='polling…'; return; }
    document.getElementById('meta').textContent = 'plan='+(d.plan||'?')+'  board='+(d.board||'?')+'  run='+(d.run_id||'?');
    const badge = document.getElementById('badge');
    if(d.producer_lost){ badge.className='badge b-lost'; badge.textContent='❌ Producer lost ('+d.idle_s+'s)'; }
    else if(d.run_ended){ badge.className='badge b-done'; badge.textContent='● DONE'; }
    else { badge.className='badge b-run'; badge.textContent='● RUNNING'; }
    const pct = d.total ? Math.round(100*d.completed/d.total) : 0;
    document.getElementById('bar').style.width = pct+'%';
    document.getElementById('prog').textContent = d.completed+' / '+d.total+' ('+pct+'%)';
    document.getElementById('pass').textContent = d['pass'];
    document.getElementById('fail').textContent = d.fail;
    document.getElementById('cur').textContent = d.current || '—';
    document.getElementById('eta').textContent = fmtEta(d.eta);
    const fb = document.getElementById('failsBox'), fl = document.getElementById('fails');
    const fk = Object.keys(d.fail_summaries||{});
    if(fk.length){ fb.style.display='block'; fl.innerHTML = fk.map(k=>'✗ '+k+': '+d.fail_summaries[k]).map(t=>'<div></div>').join('');
      [...fl.children].forEach((el,i)=>el.textContent='✗ '+fk[i]+': '+d.fail_summaries[fk[i]]); }
    else { fb.style.display='none'; }
    const mark = {pass:['✓','m-pass'], fail:['✗','m-fail'], running:['⏳','m-run'], pending:['⏳','m-pend']};
    const cc = document.getElementById('cases');
    cc.innerHTML = (d.cases||[]).map(()=>'<div class="case"></div>').join('');
    (d.cases||[]).forEach((name,i)=>{ const m=mark[d.status[name]||'pending']||mark.pending;
      const el=cc.children[i]; el.className='case'+(name===d.current?' cur':'');
      el.innerHTML='<span class="'+m[1]+'">'+m[0]+'</span> '+name+(name===d.current?'  ◀':''); });
    foot.textContent = 'heartbeat#'+d.heartbeat_seq+'  ·  updated '+new Date().toLocaleTimeString();
  }catch(e){ document.getElementById('foot').textContent='연결 오류: '+e; }
}
tick(); setInterval(tick, 1000);
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    events_path: str = ""

    def log_message(self, *args):  # 조용히
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/state"):
            self._send(json.dumps(build_state(self.events_path)).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path == "/" or self.path.startswith("/?"):
            self._send(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="pim_web_viewer",
        description="events/current.jsonl 실시간 웹 뷰어 (stdlib only).",
    )
    ap.add_argument("path", nargs="?", default=None,
                    help="JSONL 경로 (기본: events/current.jsonl)")
    ap.add_argument("--host", default="0.0.0.0", help="바인드 호스트 (기본 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8077, help="포트 (기본 8077)")
    args = ap.parse_args(argv)
    _Handler.events_path = _events_path(args.path)
    srv = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"pim_web_viewer: http://localhost:{args.port}  (events: {_Handler.events_path})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
