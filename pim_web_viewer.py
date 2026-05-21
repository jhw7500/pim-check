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
        "fail_classification": st.fail_classification,
        "case_detail": st.case_details,
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
  .box { border-radius:8px; padding:8px 12px; margin:10px 0; font-size:13px; }
  .box .hd { font-weight:700; font-size:12px; margin-bottom:4px; letter-spacing:.04em; }
  .box div.ln { padding:2px 0; }
  .confirmed { background:#1a1014; border:1px solid #3a2026; }
  .confirmed .hd { color:#f87171; } .confirmed .ln { color:#fca5a5; }
  .active { background:#1f1810; border:1px solid #3d2f12; }
  .active .hd { color:#fbbf24; } .active .ln { color:#fcd34d; }
  .recovered { background:#101a14; border:1px solid #1f3a2a; }
  .recovered .hd { color:#34d399; } .recovered .ln { color:#86efac; }
  .cases { display:grid; grid-template-columns: repeat(auto-fill, minmax(220px,1fr)); gap:4px 14px; margin-top:8px; }
  .case { font-size:13px; padding:3px 6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
          cursor:pointer; border-radius:5px; border:1px solid transparent; }
  .case:hover { background:#161a22; border-color:#26304a; }
  .case.sel { background:#172033; border-color:#3b82f6; }
  .m-pass { color:#4ade80; } .m-fail { color:#f87171; } .m-run { color:#fbbf24; } .m-pend { color:#5b647a; }
  .chip { font-size:10px; padding:1px 6px; border-radius:8px; margin-left:6px; }
  .chip-resolved { background:#0e2a1c; color:#34d399; }
  .chip-active { background:#2a2210; color:#fbbf24; }
  .cur { color:#fbbf24; font-weight:700; }
  .detail { margin-top:12px; background:#11151d; border:1px solid #283246; border-radius:8px; padding:12px 14px; }
  .detail .dh { display:flex; align-items:center; gap:10px; margin-bottom:8px; }
  .detail .dname { font-size:14px; font-weight:700; color:#cdd6f4; }
  .detail .meta { color:#8a93a6; font-size:12px; margin-bottom:8px; }
  .detail .fl { font-size:12px; padding:3px 0; border-top:1px solid #1c2433; }
  .detail .fl .t { color:#7aa2f7; } .detail .fl .ck { color:#cbd5e1; }
  .detail .fl.r .rs { color:#86efac; } .detail .fl.c .rs { color:#fca5a5; }
  .detail .none { color:#5b647a; font-size:12px; }
  .detail .x { margin-left:auto; cursor:pointer; color:#8a93a6; border:1px solid #283246; border-radius:5px; padding:1px 8px; }
  .hint { color:#5b647a; font-size:11px; margin-top:4px; }
  .foot { color:#5b647a; font-size:11px; margin-top:14px; }
  .st-pass { color:#4ade80; } .st-fail { color:#f87171; } .st-running { color:#fbbf24; } .st-pending { color:#5b647a; }
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
  <div class="box confirmed" id="confirmedBox" style="display:none"><div class="hd">✗ FAILED (최종)</div><div id="confirmed"></div></div>
  <div class="box active" id="activeBox" style="display:none"><div class="hd">⚠ FAULT (진행 중)</div><div id="active"></div></div>
  <div class="box recovered" id="recoveredBox" style="display:none"><div class="hd">↻ 재시도로 회복됨 (일시 fail)</div><div id="recovered"></div></div>
  <div class="hint">케이스를 클릭하면 상세 진행 상황을 볼 수 있습니다.</div>
  <div class="cases" id="cases"></div>
  <div class="detail" id="detail" style="display:none"></div>
  <div class="foot" id="foot"></div>
</div>
<script>
let SEL = null;     // 선택된 케이스 (드릴다운)
let LAST = null;    // 마지막 /state 응답
function fmtEta(s){ s=Math.max(0,Math.round(s||0)); return s<60?("~"+s+"s"):("~"+Math.floor(s/60)+"m "+(s%60)+"s"); }
function fmtDur(s){ if(s==null) return '—'; s=Math.round(s); return s<60?(s+"s"):(Math.floor(s/60)+"m "+(s%60)+"s"); }

function fillLines(boxId, listId, items){
  const box=document.getElementById(boxId), list=document.getElementById(listId);
  if(!items.length){ box.style.display='none'; list.replaceChildren(); return; }
  box.style.display='block';
  list.replaceChildren(...items.map(t=>{ const d=document.createElement('div'); d.className='ln'; d.textContent=t; return d; }));
}

function renderFails(d){
  const cls=d.fail_classification||{}, sum=d.fail_summaries||{};
  const conf=[], act=[], rec=[];
  Object.keys(cls).forEach(n=>{
    const reason=sum[n]||'';
    if(cls[n]==='confirmed') conf.push('✗ '+n+': '+reason);
    else if(cls[n]==='active') act.push('⚠ '+n+': '+reason);
    else if(cls[n]==='resolved') rec.push('↻ '+n+' — 재시도 후 통과');
  });
  fillLines('confirmedBox','confirmed',conf);
  fillLines('activeBox','active',act);
  fillLines('recoveredBox','recovered',rec);
}

function renderCases(d){
  const mark={pass:['✓','m-pass'], fail:['✗','m-fail'], running:['⏳','m-run'], pending:['⏳','m-pend']};
  const cls=d.fail_classification||{};
  const cc=document.getElementById('cases');
  cc.replaceChildren(...(d.cases||[]).map(name=>{
    const m=mark[d.status[name]||'pending']||mark.pending;
    const el=document.createElement('div');
    el.className='case'+(name===d.current?' cur':'')+(name===SEL?' sel':'');
    const sp=document.createElement('span'); sp.className=m[1]; sp.textContent=m[0];
    el.appendChild(sp);
    el.appendChild(document.createTextNode(' '+name+(name===d.current?'  ◀':'')));
    if(cls[name]==='resolved'){ const c=document.createElement('span'); c.className='chip chip-resolved'; c.textContent='↻ 회복'; el.appendChild(c); }
    else if(cls[name]==='active'){ const c=document.createElement('span'); c.className='chip chip-active'; c.textContent='⚠ fault'; el.appendChild(c); }
    el.onclick=()=>{ SEL=(SEL===name?null:name); renderCases(LAST); renderDetail(LAST); };
    return el;
  }));
}

function renderDetail(d){
  const box=document.getElementById('detail');
  if(!SEL || !d || !d.case_detail || !d.case_detail[SEL]){ box.style.display='none'; box.replaceChildren(); return; }
  const cd=d.case_detail[SEL]; box.style.display='block'; box.replaceChildren();
  const hd=document.createElement('div'); hd.className='dh';
  const nm=document.createElement('span'); nm.className='dname'; nm.textContent=SEL; hd.appendChild(nm);
  const stt=document.createElement('span'); stt.className='st-'+(cd.status||'pending'); stt.textContent=(cd.status||'pending').toUpperCase(); hd.appendChild(stt);
  if(cd.classification==='resolved'){ const c=document.createElement('span'); c.className='chip chip-resolved'; c.textContent='↻ 재시도로 회복'; hd.appendChild(c); }
  else if(cd.classification==='active'){ const c=document.createElement('span'); c.className='chip chip-active'; c.textContent='⚠ 진행 중 fault'; hd.appendChild(c); }
  const x=document.createElement('span'); x.className='x'; x.textContent='✕ 닫기'; x.onclick=()=>{ SEL=null; renderCases(LAST); renderDetail(LAST); }; hd.appendChild(x);
  box.appendChild(hd);
  const meta=document.createElement('div'); meta.className='meta';
  meta.textContent='phase='+(cd.phase||'—')+'   소요='+fmtDur(cd.duration_s)+'   fail 이벤트='+(cd.fail_count||0)+'건';
  box.appendChild(meta);
  const fails=cd.fails||[];
  if(!fails.length){ const n=document.createElement('div'); n.className='none'; n.textContent=(cd.status==='pass'?'fault 없이 통과':'fault 이벤트 없음'); box.appendChild(n); return; }
  const resolved=(cd.classification==='resolved');
  fails.forEach(f=>{
    const row=document.createElement('div'); row.className='fl '+(resolved?'r':'c');
    const t=document.createElement('span'); t.className='t'; t.textContent=(f.elapsed_s!=null?('+'+Math.round(f.elapsed_s-(cd.started_s||0))+'s '):''); row.appendChild(t);
    const ck=document.createElement('span'); ck.className='ck'; ck.textContent=(f.check||'check')+': '; row.appendChild(ck);
    const rs=document.createElement('span'); rs.className='rs'; rs.textContent=(resolved?'↻ ':'✗ ')+(f.reason||''); row.appendChild(rs);
    box.appendChild(row);
  });
}

async function tick(){
  try{
    const r = await fetch('/state?_='+Date.now(), {cache:'no-store'});
    const d = await r.json();
    LAST = d;
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
    renderFails(d);
    renderCases(d);
    renderDetail(d);
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
