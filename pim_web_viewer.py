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
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import plan as plan_mod
import run_control
import run_stream
from viewer_state import ViewerState

PRODUCER_LOST_AFTER = 10.0
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(REPO_DIR, "profiles")
# start_run 의 check-then-spawn 을 직렬화한다. ThreadingHTTPServer 는 요청마다 스레드라
# 동시 /start 2건이 단일-런 가드를 모두 통과해 프로듀서가 2개 뜨는 TOCTOU 를 막는다.
_START_LOCK = threading.Lock()


def _producer_events_dir() -> str:
    # 제어(control state/guard)는 spawn 될 producer 가 실제로 쓰는 디렉터리에 고정한다.
    # producer(pim_check)는 뷰어의 read 경로와 무관하게 default_events_dir 에 기록·
    # current.jsonl 을 repoint 하므로, 여기에 맞춰야 단일-런 가드/상태가 일관된다.
    return run_stream.default_events_dir()


def _list_plans() -> list[str]:
    try:
        return plan_mod.list_plans(PROFILES_DIR)
    except Exception:
        return []


def control_status() -> dict:
    """관리 중인 런 상태 + 선택 가능한 plan 목록 (UI 제어판용)."""
    events_dir = _producer_events_dir()
    pid = run_control.active_pid(events_dir)
    info = run_control.read_control(events_dir) or {}
    return {
        "active": pid is not None, "pid": pid,
        "plan": info.get("plan"), "host": info.get("host"),
        "started_at": info.get("started_at"), "plans": _list_plans(),
    }


def start_run(params: dict) -> tuple[int, dict]:
    """plan 런 spawn. (http_status, body) 반환. 단일 런만 허용(current.jsonl 공유).

    check-then-spawn 전체를 _START_LOCK 으로 직렬화해 동시 /start 의 TOCTOU(프로듀서
    중복 spawn)를 막는다. 비밀번호는 argv 가 아니라 PIM_PASSWORD env 로 전달해
    ps/proc 노출을 피한다.
    """
    events_dir = _producer_events_dir()
    with _START_LOCK:
        if run_control.active_pid(events_dir) is not None:
            return 409, {"ok": False, "error": "이미 실행 중인 런이 있습니다"}
        # 외부(CLI/nohup)로 시작된 런도 감지 — producer 가 쓰는 default current.jsonl 이
        # 살아있으면 거부(공유 충돌 방지). 뷰어 read 경로가 아니라 producer write 경로 기준.
        st = build_state(_events_path())
        if st.get("exists") and not st.get("run_ended") and not st.get("producer_lost"):
            return 409, {"ok": False, "error": "이미 진행 중인 런이 있습니다 (외부 시작 포함)"}
        ok, err, clean = run_control.validate_start_request(params, _list_plans())
        if not ok:
            return 400, {"ok": False, "error": err}
        try:
            os.makedirs(events_dir, exist_ok=True)
            # 비밀번호는 env(PIM_PASSWORD)로만 전달 — argv 에 두면 ps/proc 에 노출된다.
            # with 블록: Popen 이 생성자에서 fd 를 자식으로 dup 하므로 spawn 직후 부모
            # 핸들을 닫아도 자식은 계속 기록한다(반복 /start 시 부모 FD 누수 방지).
            child_env = {**os.environ, "PIM_PASSWORD": clean["password"]}
            with open(os.path.join(events_dir, "viewer_run.log"), "ab") as logf:
                proc = subprocess.Popen(
                    [sys.executable, os.path.join(REPO_DIR, "pim_check.py"),
                     "--plan", clean["plan"], "--host", clean["host"],
                     "--user", clean["user"],
                     "--json", "--html", "--log"],
                    cwd=REPO_DIR, env=child_env, stdout=logf,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception as e:  # noqa: BLE001 — spawn 실패는 클라이언트에 그대로 보고
            return 500, {"ok": False, "error": f"spawn 실패: {e}"}
        run_control.write_control(events_dir, {
            "pid": proc.pid, "plan": clean["plan"], "host": clean["host"],
            "started_at": time.time(),
        })
    return 200, {"ok": True, "pid": proc.pid, "plan": clean["plan"], "host": clean["host"]}


def stop_run() -> tuple[int, dict]:
    """관리 중인 런 종료(SIGTERM→SIGKILL). (http_status, body) 반환."""
    events_dir = _producer_events_dir()
    info = run_control.read_control(events_dir)
    pid = info.get("pid") if info else None
    if not isinstance(pid, int) or not run_control.pid_alive(pid):
        run_control.clear_control(events_dir)
        return 200, {"ok": True, "stopped": False, "note": "관리 중인 런 없음"}
    _signal_pid(pid, signal.SIGTERM)
    for _ in range(30):
        if not run_control.pid_alive(pid):
            break
        time.sleep(0.1)
    if run_control.pid_alive(pid):
        _signal_pid(pid, signal.SIGKILL)
    run_control.clear_control(events_dir)
    return 200, {"ok": True, "stopped": True, "pid": pid}


def _signal_pid(pid: int, sig: int) -> None:
    """프로세스 그룹에 시그널(start_new_session 으로 그룹 리더). 실패 시 단일 pid."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
        except OSError:
            pass


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
        "elapsed_s": round(st.elapsed_s, 1),
        "eta": round(st.eta_seconds, 1),
        "run_ended": st.run_ended,
        "producer_lost": (not st.run_ended) and idle > PRODUCER_LOST_AFTER,
        "idle_s": round(idle, 1),
        "cases": st.cases,
        "status": st.case_status,
        "fail_summaries": st.fail_summaries,
        "fail_classification": st.fail_classification,
        "case_detail": st.case_details,
        "pending": st.pending_summaries.get(st.current_case),
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
  .detail .fl .cnt { color:#fbbf24; font-weight:700; }
  .detail .fl.r .rs { color:#86efac; } .detail .fl.c .rs { color:#fca5a5; }
  .detail .none { color:#5b647a; font-size:12px; }
  .detail .pendbox { margin:6px 0; padding:9px 12px; border-radius:8px; background:#1f1810; border:1px solid #3d2f12; }
  .detail .pendbox .ph { color:#fbbf24; font-weight:700; font-size:13px; }
  .detail .pendbox .ps { color:#caa55a; font-size:11px; margin-top:3px; }
  .detail .desc { color:#9fb0c8; font-size:12px; margin:0 0 8px; }
  .detail .prog { color:#9ad; font-size:12px; font-weight:600; margin:8px 0 2px; }
  .detail .chk { font-size:12px; padding:3px 0; border-top:1px solid #1c2433; cursor:pointer; }
  .detail .chk:hover { background:#141925; }
  .detail .chk .ci { width:16px; display:inline-block; text-align:center; }
  .chk-pass .ci { color:#4ade80; } .chk-fail .ci { color:#f87171; } .chk-run .ci { color:#fbbf24; }
  .chk-pend .ci { color:#5b647a; }
  .detail .method { margin:2px 0 6px 22px; font-size:11px; display:none; }
  .detail .method.open { display:block; }
  .detail .method .mc { color:#9ecbff; word-break:break-all; }
  .detail .method .me { color:#86efac; margin-top:2px; }
  .detail .method .ma { color:#fde68a; margin-top:2px; }
  .detail .meas { color:#cbd5e1; font-size:11px; margin-left:6px; }
  .detail .chk-fail .meas { color:#fca5a5; }
  .detail .x { margin-left:auto; cursor:pointer; color:#8a93a6; border:1px solid #283246; border-radius:5px; padding:1px 8px; }
  .hint { color:#5b647a; font-size:11px; margin-top:4px; }
  .foot { color:#5b647a; font-size:11px; margin-top:14px; }
  .st-pass { color:#4ade80; } .st-fail { color:#f87171; } .st-running { color:#fbbf24; } .st-pending { color:#5b647a; }
  /* live progress */
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.25} }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; vertical-align:middle; }
  .dot.run { background:#4ade80; animation:pulse 1.2s ease-in-out infinite; }
  .dot.done { background:#7aa2f7; } .dot.lost { background:#f87171; animation:pulse .8s infinite; }
  .clocks { display:flex; gap:28px; margin:6px 0 2px; }
  .clk b { font-size:26px; font-variant-numeric:tabular-nums; color:#e6e6e6; }
  .clk span { display:block; color:#8a93a6; font-size:11px; letter-spacing:.05em; }
  .banner { margin:12px 0; padding:12px 16px; border-radius:10px; background:#15203a;
            border:1px solid #2a3a63; display:flex; align-items:center; gap:12px; }
  .banner.idle { background:#161a22; border-color:#26304a; }
  .banner .bcase { font-size:18px; font-weight:700; color:#cfe0ff; }
  .banner .bt { margin-left:auto; font-size:20px; font-variant-numeric:tabular-nums; color:#fbbf24; }
  .box .case-h { font-weight:700; margin:4px 0 1px; }
  .box .sub, .detail .sub { margin:0 0 4px 14px; font-size:12px; opacity:.92; }
  .confirmed .case-h { color:#f87171; } .active .case-h { color:#fbbf24; }
  .confirmed .sub { color:#fca5a5; } .active .sub { color:#fcd34d; }
  .ctrl { background:#0f1722; border:1px solid #1f2a3a; border-radius:8px; padding:10px; margin:8px 0; }
  .ctrl .crow { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
  .ctrl select, .ctrl input { background:#0b1220; color:#e5e7eb; border:1px solid #334155;
    border-radius:6px; padding:5px 7px; font-size:12px; }
  .ctrl input.host { width:130px; } .ctrl input.cred { width:80px; }
  .ctrl button { border:none; border-radius:6px; padding:6px 12px; font-size:12px; cursor:pointer; }
  .ctrl .start { background:#16a34a; color:#fff; } .ctrl .stop { background:#dc2626; color:#fff; }
  .ctrl button:disabled { opacity:.4; cursor:not-allowed; }
  .ctrl .cmsg { font-size:11px; margin-top:6px; min-height:14px; color:#9ca3af; }
  .ctrl .cmsg.err { color:#fca5a5; } .ctrl .cmsg.ok { color:#86efac; }
</style></head>
<body><div class="wrap">
  <h1 id="title">pim_viewer (web)</h1>
  <div class="ctrl" id="ctrl">
    <div class="crow">
      <select id="c_plan" title="플랜"></select>
      <input class="host" id="c_host" placeholder="타겟 IP (예: 192.168.214.4)">
      <input class="cred" id="c_user" value="root" title="SSH 유저">
      <input class="cred" id="c_pass" type="password" value="root" title="SSH 비밀번호">
      <button class="start" id="c_start">▶ 시작</button>
      <button class="stop" id="c_stop" disabled>■ 중지</button>
    </div>
    <div class="cmsg" id="c_msg"></div>
  </div>
  <div class="sub" id="meta">waiting for event stream…</div>
  <div id="badge" class="badge b-run"><span class="dot run"></span>…</div>
  <div class="clocks">
    <div class="clk"><b id="elapsed">0s</b><span>경과 (ELAPSED)</span></div>
    <div class="clk"><b id="eta">~0s</b><span>남은 예상 (ETA)</span></div>
  </div>
  <div class="bar"><i id="bar"></i></div>
  <div class="sub" id="prog">0 / 0 (0%)</div>
  <div class="banner idle" id="banner">
    <span class="dot run"></span><span class="bcase" id="bcase">대기 중</span>
    <span class="bt" id="btimer"></span>
  </div>
  <div class="row">
    <div class="stat pass"><b id="pass">0</b><span>PASS</span></div>
    <div class="stat fail"><b id="fail">0</b><span>FAIL</span></div>
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
let OPEN = new Set();  // 검증 방법(toggle)이 펼쳐진 체크리스트 항목 키 (재렌더 사이 유지)
let SRV = {elapsed:0, at:0, ended:false, lost:false, exists:false};  // 시계 보간 기준점
let CUR_START = null;  // 현재 케이스 시작 elapsed_s
let LAST_RUN = null;   // 직전 폴링의 run_id (런 경계 감지용)
function fmtEta(s){ s=Math.max(0,Math.round(s||0)); return s<60?("~"+s+"s"):("~"+Math.floor(s/60)+"m "+(s%60)+"s"); }
function fmtDur(s){ if(s==null) return '—'; s=Math.round(s); return s<60?(s+"s"):(Math.floor(s/60)+"m "+(s%60)+"s"); }
function fmtClock(s){ s=Math.max(0,Math.floor(s||0)); const m=Math.floor(s/60); return m?(m+"m "+(s%60)+"s"):(s+"s"); }
function splitReason(r){ return String(r||'').split(';').map(s=>s.trim()).filter(Boolean); }

// 서버 elapsed_s 를 기준점 삼아 매초 부드럽게 흐르는 경과 시간(폴링 사이도 진행).
function liveElapsed(){ if(!SRV.exists) return 0; if(SRV.ended||SRV.lost) return SRV.elapsed; return SRV.elapsed + (Date.now()-SRV.at)/1000; }
function renderClocks(){
  document.getElementById('elapsed').textContent = SRV.exists ? fmtClock(liveElapsed()) : '0s';
  const bt=document.getElementById('btimer');
  bt.textContent = (CUR_START!=null && SRV.exists && !SRV.ended && !SRV.lost) ? (fmtClock(liveElapsed()-CUR_START)+' 경과') : '';
}
function setBadge(boxcls, dotcls, text){
  const b=document.getElementById('badge'); b.className='badge '+boxcls; b.replaceChildren();
  const dot=document.createElement('span'); dot.className='dot '+dotcls; b.appendChild(dot);
  b.appendChild(document.createTextNode(text));
}
function renderBanner(d){
  const ban=document.getElementById('banner'), bc=document.getElementById('bcase'), dot=ban.querySelector('.dot');
  if(d.producer_lost){ ban.className='banner'; dot.className='dot lost'; bc.textContent='Producer lost — 신호 끊김'; CUR_START=null; }
  else if(d.run_ended){ ban.className='banner'; dot.className='dot done'; bc.textContent='완료 — '+d['pass']+'/'+d.total+' pass'; CUR_START=null; }
  else if(d.current){ ban.className='banner'; dot.className='dot run';
    bc.textContent='실행 중: '+d.current+(d.pending?'   ·   ⏳ 준비 중 (검증 대기)':'');
    const cd=(d.case_detail||{})[d.current]; CUR_START=(cd && cd.started_s!=null)?cd.started_s:null; }
  else { ban.className='banner idle'; dot.className='dot run'; bc.textContent='대기 중'; CUR_START=null; }
}

function fillLines(boxId, listId, items){
  const box=document.getElementById(boxId), list=document.getElementById(listId);
  if(!items.length){ box.style.display='none'; list.replaceChildren(); return; }
  box.style.display='block';
  list.replaceChildren(...items.map(t=>{ const d=document.createElement('div'); d.className='ln'; d.textContent=t; return d; }));
}

function renderFailGroup(boxId, listId, cases, sum, mark){
  const box=document.getElementById(boxId), list=document.getElementById(listId);
  if(!cases.length){ box.style.display='none'; list.replaceChildren(); return; }
  box.style.display='block';
  const nodes=[];
  cases.forEach(n=>{
    const h=document.createElement('div'); h.className='case-h'; h.textContent=mark+' '+n; nodes.push(h);
    const lines=splitReason(sum[n]);
    (lines.length?lines:['(원인 미상)']).forEach(line=>{
      const d=document.createElement('div'); d.className='sub'; d.textContent='• '+line; nodes.push(d);
    });
  });
  list.replaceChildren(...nodes);
}
function renderFails(d){
  const cls=d.fail_classification||{}, sum=d.fail_summaries||{};
  const conf=[], act=[], rec=[];
  Object.keys(cls).forEach(n=>{
    if(cls[n]==='confirmed') conf.push(n);
    else if(cls[n]==='active') act.push(n);
    else if(cls[n]==='resolved') rec.push('↻ '+n+' — 재시도 후 통과');
  });
  renderFailGroup('confirmedBox','confirmed',conf,sum,'✗');
  renderFailGroup('activeBox','active',act,sum,'⚠');
  fillLines('recoveredBox','recovered',rec);  // 회복은 한 줄 요약(상세는 클릭)
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
  if(cd.desc){ const ds=document.createElement('div'); ds.className='desc'; ds.textContent=cd.desc; box.appendChild(ds); }
  const meta=document.createElement('div'); meta.className='meta';
  const running=(cd.status==='running');
  // 실행 중이면 라이브 경과, 종료됐으면 최종 소요.
  const timeTxt = (running && cd.started_s!=null)
    ? ('경과 '+fmtClock(Math.max(0, liveElapsed()-cd.started_s)))
    : ('소요 '+fmtDur(cd.duration_s));
  let metaTxt = 'phase '+(cd.phase||'—')+'  ·  '+timeTxt;
  if(cd.checks_total){ metaTxt += '  ·  검증 '+cd.checks_passed+'/'+cd.checks_total; }
  if(cd.fail_count){ metaTxt += '  ·  fault 이벤트 '+cd.fail_count+'건'; }
  meta.textContent = metaTxt;
  box.appendChild(meta);
  // 검증 항목 체크리스트 (항목 클릭 시 command/expected 검증 방법 toggle)
  const cl=cd.checklist||[];
  if(cl.length){
    const ct=document.createElement('div'); ct.className='prog';
    ct.textContent='검증 항목 ('+cd.checks_passed+'/'+cd.checks_total+' 통과)'; box.appendChild(ct);
    cl.forEach(it=>{
      const ic={pass:'✓',fail:'✗',running:'⏳',pending:'○'};
      const kl={pass:'chk-pass',fail:'chk-fail',running:'chk-run',pending:'chk-pend'};
      const icon = ic[it.status]||'○';
      const klass = kl[it.status]||'chk-pend';
      const row=document.createElement('div'); row.className='chk '+klass;
      const ci=document.createElement('span'); ci.className='ci'; ci.textContent=icon; row.appendChild(ci);
      row.appendChild(document.createTextNode(' '+(it.name||'')));
      if(it.actual!=null && it.actual!==''){ const meas=document.createElement('span'); meas.className='meas'; meas.textContent='측정 '+it.actual; row.appendChild(meas); }
      const m=document.createElement('div'); m.className='method';
      const mc=document.createElement('div'); mc.className='mc'; mc.textContent='$ '+(it.command||''); m.appendChild(mc);
      if(it.actual!=null && it.actual!==''){ const ma=document.createElement('div'); ma.className='ma'; ma.textContent='측정값: '+it.actual; m.appendChild(ma); }
      if(it.expected!=null && it.expected!==''){ const me=document.createElement('div'); me.className='me'; me.textContent='기대값: '+it.expected; m.appendChild(me); }
      const key=SEL+'|'+(it.name||'');
      if(OPEN.has(key)) m.classList.add('open');
      row.onclick=()=>{ if(OPEN.has(key)) OPEN.delete(key); else OPEN.add(key); m.classList.toggle('open'); };
      box.appendChild(row); box.appendChild(m);
    });
  }
  if(cd.pending && running){
    const p=document.createElement('div'); p.className='pendbox';
    const h=document.createElement('div'); h.className='ph'; h.textContent='⏳ 준비 중 — 검증 대기';
    const s=document.createElement('div'); s.className='ps';
    s.textContent='아직 장애 아님. finalize 등 조건이 갖춰지면 자동으로 통과 처리됩니다.';
    p.appendChild(h); p.appendChild(s); box.appendChild(p);
  }
  const fails=cd.fails||[];
  if(!fails.length){
    // 준비 중 박스가 이미 있으면 'fault 없음' 중복 줄은 생략.
    if(!(cd.pending && running)){
      const n=document.createElement('div'); n.className='none';
      n.textContent=(cd.status==='pass'?'✓ fault 없이 통과':'fault 이벤트 없음');
      box.appendChild(n);
    }
    return;
  }
  const resolved=(cd.classification==='resolved');
  fails.forEach(f=>{
    // reason 줄을 row(.fl r/c) 안에 넣어야 .detail .fl.r/.fl.c .rs 색상 셀렉터가 매칭된다.
    const row=document.createElement('div'); row.className='fl '+(resolved?'r':'c');
    const t=document.createElement('span'); t.className='t'; t.textContent=(f.elapsed_s!=null?('+'+Math.round(f.elapsed_s-(cd.started_s||0))+'s '):''); row.appendChild(t);
    const ck=document.createElement('span'); ck.className='ck'; ck.textContent=(f.check||'check'); row.appendChild(ck);
    if(f.count>1){ const cnt=document.createElement('span'); cnt.className='cnt'; cnt.textContent=' ×'+f.count; row.appendChild(cnt); }
    const lines=splitReason(f.reason);
    (lines.length?lines:['(원인 미상)']).forEach(line=>{
      const d=document.createElement('div'); d.className='rs sub'; d.textContent=(resolved?'↻ ':'✗ ')+line; row.appendChild(d);
    });
    box.appendChild(row);
  });
}

async function tick(){
  try{
    const r = await fetch('/state?_='+Date.now(), {cache:'no-store'});
    const d = await r.json();
    LAST = d;
    const foot = document.getElementById('foot');
    if(!d.exists){ SRV.exists=false; document.getElementById('meta').textContent='이벤트 스트림 없음 (pim_check.py --plan 실행 대기)'; foot.textContent='polling…'; return; }
    document.getElementById('meta').textContent = 'plan='+(d.plan||'?')+'  board='+(d.board||'?')+'  run='+(d.run_id||'?');
    // 런 경계 감지: run_id 가 바뀌면 새 런의 elapsed_s 로 baseline 을 리셋한다.
    // (안 그러면 아래 max 클램프가 이전 런의 높은 elapsed 를 그대로 끌고 와 새 런 시계가 오염됨)
    const runChanged = (d.run_id != null && d.run_id !== LAST_RUN);
    LAST_RUN = d.run_id;
    if(runChanged){ CUR_START=null; SEL=null; OPEN.clear(); }
    // 시계 보간 기준점 갱신 — 같은 런 안에서는 baseline 을 단조롭게 유지(서버가 같은
    // elapsed 를 연속 보고해도 보간 시계가 뒤로 튀지 않도록 max 로 클램프).
    SRV = {elapsed: runChanged ? (d.elapsed_s||0) : Math.max(liveElapsed(), d.elapsed_s||0), at:Date.now(),
           ended:!!d.run_ended, lost:!!d.producer_lost, exists:true};
    if(d.producer_lost){ setBadge('b-lost','lost','Producer lost ('+d.idle_s+'s)'); }
    else if(d.run_ended){ setBadge('b-done','done','DONE'); }
    else { setBadge('b-run','run','RUNNING'); }
    const pct = d.total ? Math.round(100*d.completed/d.total) : 0;
    document.getElementById('bar').style.width = pct+'%';
    document.getElementById('prog').textContent = d.completed+' / '+d.total+' ('+pct+'%)';
    document.getElementById('pass').textContent = d['pass'];
    document.getElementById('fail').textContent = d.fail;
    document.getElementById('eta').textContent = fmtEta(d.eta);
    renderBanner(d);
    renderClocks();
    renderFails(d);
    renderCases(d);
    renderDetail(d);
    foot.textContent = 'heartbeat#'+d.heartbeat_seq+'  ·  updated '+new Date().toLocaleTimeString();
  }catch(e){ document.getElementById('foot').textContent='연결 오류: '+e; }
}
// --- 제어판(plan 선택 → 시작/중지) ---------------------------------------
let PLANS_KEY="";
function setCtrlActive(active){
  document.getElementById('c_start').disabled=active;
  document.getElementById('c_stop').disabled=!active;
}
function cmsg(text, cls){ const m=document.getElementById('c_msg'); m.className='cmsg'+(cls?(' '+cls):''); m.textContent=text; }
async function loadControl(){
  try{
    const r=await fetch('/control?_='+Date.now(),{cache:'no-store'});
    const d=await r.json();
    if(Array.isArray(d.plans)){
      // 목록이 바뀐 경우에만 select 재구성(매 폴링마다 사용자 선택 초기화 방지).
      const key=d.plans.join('|');
      if(key!==PLANS_KEY){
        const sel=document.getElementById('c_plan'); const cur=sel.value;
        sel.replaceChildren(...d.plans.map(p=>{const o=document.createElement('option');o.value=p;o.textContent=p;return o;}));
        if(d.plans.includes(cur)) sel.value=cur;
        PLANS_KEY=key;
      }
    }
    setCtrlActive(!!d.active);
  }catch(e){ cmsg('제어판 상태 갱신 실패: '+e,'err'); }
}
async function startRun(){
  const plan=document.getElementById('c_plan').value;
  const host=document.getElementById('c_host').value.trim();
  const user=document.getElementById('c_user').value.trim();
  const password=document.getElementById('c_pass').value;
  if(!plan){ cmsg('플랜을 선택하세요','err'); return; }
  if(!host){ cmsg('타겟 IP를 입력하세요','err'); return; }
  cmsg('시작 중…'); setCtrlActive(true);
  try{
    const r=await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({plan:plan,host:host,user:user,password:password})});
    const d=await r.json();
    if(d.ok){ cmsg('시작됨: '+d.plan+' @ '+d.host+(d.pid?(' (pid '+d.pid+')'):''),'ok'); }
    else { cmsg('실패: '+(d.error||r.status),'err'); setCtrlActive(false); }
  }catch(e){ cmsg('요청 오류: '+e,'err'); setCtrlActive(false); }
}
async function stopRun(){
  cmsg('중지 중…');
  try{
    const r=await fetch('/stop',{method:'POST'});
    const d=await r.json();
    if(d.ok){ cmsg(d.stopped?('중지됨 (pid '+d.pid+')'):'관리 중인 런 없음','ok'); setCtrlActive(false); }
    else cmsg('실패: '+(d.error||r.status),'err');
  }catch(e){ cmsg('요청 오류: '+e,'err'); }
}
document.getElementById('c_start').onclick=startRun;
document.getElementById('c_stop').onclick=stopRun;
loadControl(); setInterval(loadControl, 3000);
tick(); setInterval(tick, 1000); setInterval(renderClocks, 1000);
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

    def _send_json(self, code: int, body: dict | list):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n > 0 else b""
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, TypeError):
            return None

    def do_GET(self):
        if self.path.startswith("/state"):
            self._send(json.dumps(build_state(self.events_path)).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/control"):
            self._send_json(200, control_status())
        elif self.path.startswith("/plans"):
            self._send_json(200, _list_plans())
        elif self.path == "/" or self.path.startswith("/?"):
            self._send(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        route = self.path.rstrip("/")
        if route == "/start":
            params = self._read_json()
            if params is None:
                self._send_json(400, {"ok": False, "error": "잘못된 요청 본문"})
                return
            code, body = start_run(params)
            self._send_json(code, body)
        elif route == "/stop":
            code, body = stop_run()
            self._send_json(code, body)
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
    # spawn 한 plan 런 자식 프로세스가 종료/중지 시 좀비로 남지 않도록 자동 reap.
    # (start_run 은 fire-and-forget Popen 이라 wait() 하지 않음)
    try:
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    except (ValueError, AttributeError, OSError):
        pass  # 비-POSIX 또는 비-메인스레드: 좀비 누적 감수
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
