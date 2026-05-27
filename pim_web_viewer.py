#!/usr/bin/env python3
"""pim_web_viewer — events/current.jsonl 을 브라우저로 실시간 표시하는 경량 웹 뷰어.

stdlib http.server 만 사용(의존 없음). TUI(pim_viewer)와 동일한
viewer_state.ViewerState 를 재사용해 같은 JSONL 을 웹으로 보여준다
(시드 비전: 단일 이벤트 스트림을 TUI/웹/알림이 공유).

/state 는 매 요청마다 JSONL 을 처음부터 다시 접어(monotonic replay) 현재 상태와
producer-lost(파일 mtime 기반 — heartbeat 가 5초마다 파일을 갱신하므로 10초 무갱신
이면 producer 사망 추정)를 JSON 으로 반환한다. 브라우저는 1초마다 /state 를 폴링해
렌더하므로, 페이지를 새로 열어도 처음부터 상태가 복원된다(재접속 = state snapshot).

보안 모델 (중요):
  이 뷰어는 **localhost / 사내 신뢰 네트워크 전용**이다. /start 요청 body 는
  SSH 비밀번호 (`password` / `targets[].password`) 를 평문 JSON 으로 전송하므로
  HTTPS 가 아닌 환경에서 외부망에 노출하면 패스워드 누설된다. 자식 process 에는
  argv 가 아닌 PIM_PASSWORD env 로 전달해 ps/proc 노출은 피한다 (이 부분만
  방어). 외부 노출 필요 시 reverse proxy + TLS + auth 를 caller 가 책임진다.
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
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import plan as plan_mod
import run_control
import run_stream
from viewer_state import ViewerState

PRODUCER_LOST_AFTER = 10.0
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(REPO_DIR, "profiles")
# multi-target 동시 실행 상한 — DUT 측 자원(특히 사내망 SSH/카메라 처리) 보호용 가드.
# Step 1~2 의 small-scale 합의 (yaml/UI ad-hoc 2~4 타겟). 초과 시 spawn 전 400 으로 차단.
MAX_CONCURRENT_TARGETS = 4
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


def active_hosts() -> dict:
    """multi-target viewer 가 enumerate 할 host 목록을 events/active.json 에서 반환.

    Step 1 의 ``run_stream.register_active_host`` 가 기록한 그대로를 노출한다.
    파일 없음/손상은 ``{"hosts": []}`` 로 graceful fallback — viewer 가 빈 화면을
    렌더하면 그만이지, 깨지면 안 된다.
    """
    return run_stream.read_active_hosts(_producer_events_dir())


def host_events_state(host) -> dict:
    """주어진 ``host`` 의 per-target current.jsonl 을 접어 평면 state 를 반환.

    multi-target viewer 가 host 별 컬럼을 그릴 때 폴링한다. 비유효 host(빈 값,
    path 주입 문자 등)는 즉시 ``{"exists": False}`` 로 reject — 디렉터리
    traversal 방어 (``run_control.is_valid_host`` 게이트).
    """
    if not run_control.is_valid_host(host):
        return {"exists": False}
    per_target_dir = run_stream.target_events_dir(_producer_events_dir(), host)
    current = os.path.join(per_target_dir, run_stream.CURRENT_SYMLINK_NAME)
    return build_state(current)


def start_run(params: dict) -> tuple[int, dict]:
    """plan 런 spawn. (http_status, body) 반환.

    두 형태의 요청 body 를 받는다:
      1) **single-host (legacy)**: ``{plan, host, user, password, until_pass?}`` —
         현재 events/current.jsonl 을 쓰는 기존 동작 그대로. external 동시 실행 가드.
      2) **multi-target**: ``{plan, targets:[{host,user,password},...], until_pass?}`` —
         각 host 마다 ``events/by-target/<slug>/`` scope 로 별도 pim_check.py spawn.
         per-host control 로 같은 host 의 동시 실행만 차단(다른 host 끼리는 동시 OK).

    check-then-spawn 전체를 _START_LOCK 으로 직렬화해 동시 /start 의 TOCTOU(프로듀서
    중복 spawn)를 막는다. 비밀번호는 argv 가 아니라 PIM_PASSWORD env 로 전달해
    ps/proc 노출을 피한다.
    """
    # multi-target 분기 — targets 가 list 면 dedicated path. 빈 list 도 명시 의도 거부.
    if isinstance(params, dict) and "targets" in params:
        return _start_multi_target(params)

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
            # spawn 자체는 single source of truth (_spawn_one_target) 에 위임.
            # 비밀번호 env / argv 조립 / start_new_session 처리가 single·multi 양쪽
            # 동일하게 유지된다 — 이후 인자 추가 시 한 곳만 수정하면 된다.
            pid = _spawn_one_target(events_dir, clean, bool(params.get("until_pass")))
        except Exception as e:  # noqa: BLE001 — spawn 실패는 클라이언트에 그대로 보고
            return 500, {"ok": False, "error": f"spawn 실패: {e}"}
        run_control.write_control(events_dir, {
            "pid": pid, "plan": clean["plan"], "host": clean["host"],
            "started_at": time.time(),
        })
    return 200, {"ok": True, "pid": pid, "plan": clean["plan"], "host": clean["host"]}


def _spawn_one_target(events_dir: str, clean: dict, until_pass: bool) -> int:
    """단일 target 의 pim_check.py 를 spawn 하고 PID 를 반환. 비밀번호는 env 로만 전달.

    caller (single/multi 양쪽) 가 공유하는 작은 헬퍼 — argv 조립과 비밀번호 env 처리
    의 단일 진실 지점을 두어 두 경로의 차이가 우연히 벌어지지 않게 한다.
    """
    os.makedirs(events_dir, exist_ok=True)
    child_env = {**os.environ, "PIM_PASSWORD": clean["password"]}
    with open(os.path.join(events_dir, "viewer_run.log"), "ab") as logf:
        argv = [sys.executable, os.path.join(REPO_DIR, "pim_check.py"),
                "--plan", clean["plan"], "--host", clean["host"],
                "--user", clean["user"],
                "--json", "--html", "--log"]
        if until_pass:
            argv.append("--until-pass")
        proc = subprocess.Popen(
            argv,
            cwd=REPO_DIR, env=child_env, stdout=logf,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return proc.pid


def _start_multi_target(params: dict) -> tuple[int, dict]:
    """``targets:[...]`` 배열 형태의 multi-target spawn 처리.

    all-or-nothing 정책 — 한 target 이라도 검증 실패하거나 per-host conflict 면
    spawn 자체를 안 한다(partial-start 가 되면 stop/cleanup 책임이 복잡해진다).
    """
    base_events_dir = _producer_events_dir()
    plan_name = str(params.get("plan", "")).strip()
    until_pass = bool(params.get("until_pass"))
    targets = params.get("targets")

    if not isinstance(targets, list) or not targets:
        return 400, {"ok": False, "error": "targets 는 비어있지 않은 list 여야 합니다"}
    if len(targets) > MAX_CONCURRENT_TARGETS:
        return 400, {
            "ok": False,
            "error": f"동시 타겟 수 상한 초과 ({len(targets)} > {MAX_CONCURRENT_TARGETS})",
        }

    # 1단계: 모든 target 을 validate (one shared plan + per-target host/user/password).
    plans = _list_plans()
    # plan 은 batch 전체 공유 — 한 번만 검증해 plan 오류가 target[0] 의 host 오류로
    # 보이는 잘못된 attribution 을 막는다.
    if not plan_name:
        return 400, {"ok": False, "error": "plan 이 비어있습니다"}
    if plan_name not in plans:
        return 400, {"ok": False, "error": f"unknown plan: {plan_name}"}
    cleaned: list[dict] = []
    for i, t in enumerate(targets):
        if not isinstance(t, dict):
            return 400, {"ok": False, "error": f"target[{i}] 형식 오류"}
        # `.get(k) or ""` 패턴 — JSON `{"host": null}` 같은 명시 null 도 빈 문자열로
        # 정규화. `t.get("host", "")` 만 쓰면 null 이 그대로 통과해 ``str(None)='None'``
        # 이 호스트 정규식을 통과하는 잘못된 검증을 유발한다.
        req = {
            "plan": plan_name,
            "host": t.get("host") or "",
            "user": t.get("user") or "root",
            "password": t.get("password") or "",
        }
        ok, err, clean = run_control.validate_start_request(req, plans)
        if not ok:
            return 400, {"ok": False, "error": f"target[{i}] {t.get('host','?')}: {err}"}
        cleaned.append(clean)

    # 같은 요청 안에서 host 중복은 명백한 오류 — slug 기준으로 비교한다.
    # 이유: per-target 디렉터리는 host_slug(host) 로 만들어지므로, raw 호스트가
    # 달라도 같은 slug 로 collapse 되면 (예: "a.b" 와 "a-b", "Host-A" 와 "host-a")
    # 같은 events/by-target/<slug>/ 를 두 자식이 동시에 쓰게 된다 — control 파일과
    # current.jsonl 경합.
    seen_slugs = [run_stream.host_slug(c["host"]) for c in cleaned]
    if len(set(seen_slugs)) != len(seen_slugs):
        raw = [c["host"] for c in cleaned]
        return 400, {
            "ok": False,
            "error": f"targets 의 host slug 충돌 (대소문자/'.'-'-' 정규화 결과 동일): {raw}",
        }

    # 2단계: 락 안에서 per-host conflict 점검 → 모두 통과해야 spawn (all-or-nothing).
    with _START_LOCK:
        # active_pid 는 read 만 — makedirs 는 spawn 시점(_spawn_one_target)이 책임진다.
        # 여기서 미리 만들어도 무해하지만, conflict 점검 path 에는 sentinel control
        # 파일도 없으므로 굳이 만들 필요 없음(빈 디렉터리만 생긴다).
        for clean in cleaned:
            per_dir = run_stream.target_events_dir(base_events_dir, clean["host"])
            if run_control.active_pid(per_dir) is not None:
                return 409, {
                    "ok": False,
                    "error": f"host '{clean['host']}' 에 이미 실행 중인 런이 있습니다",
                }

        # 3단계: 모두 spawn — 실패 시 이미 spawn 한 자식은 stop 으로 정리해야 하므로
        # try 안에서 누적 추적한다. spawn 실패 자체는 매우 드물지만 안전하게.
        started: list[dict] = []
        spawn_failure: Exception | None = None
        try:
            for clean in cleaned:
                per_dir = run_stream.target_events_dir(base_events_dir, clean["host"])
                pid = _spawn_one_target(per_dir, clean, until_pass)
                # *반드시* write_control 보다 먼저 started 에 추가한다.
                # write_control 이 디스크 풀/권한 등으로 raise 하면 그 사이 자식은
                # 살아있는데 started 에 없어 cleanup 이 종료시키지 못한다 — 추적 안
                # 되는 좀비 producer 가 된다.
                started.append({
                    "host": clean["host"], "plan": clean["plan"], "pid": pid,
                })
                run_control.write_control(per_dir, {
                    "pid": pid, "plan": clean["plan"], "host": clean["host"],
                    "started_at": time.time(),
                })
        except Exception as e:  # noqa: BLE001 — spawn 실패는 클라이언트에 그대로 보고
            spawn_failure = e
        # 의도적으로 with 블록 종료 — cleanup 의 SIGTERM 대기(최대 3s)를 락 밖에서
        # 진행해 다른 /start 요청이 우리 cleanup 동안 막히지 않도록 한다.
        # cleanup 대상 host 들의 control 파일은 곧 삭제되므로, 그 사이 같은 host
        # 가 다시 /start 되면 active_pid 가 살아있는 동안만 409 → 정리 후 정상화.

    # 에러 cleanup 은 lock 밖에서 병렬 실행 — 라거드 자식 1개가 다른 자식의
    # 종료를 막지 않고, lock 도 점유하지 않는다. waitpid 는 쓰지 않는다 — 요청
    # 핸들러 스레드의 blocking wait 가 서버 응답을 지연시킨다. pid_alive 폴링으로.
    if spawn_failure is not None:
        if started:
            with ThreadPoolExecutor(max_workers=len(started)) as cleanup_ex:
                list(cleanup_ex.map(_terminate_pid, [s["pid"] for s in started]))
        for s in started:
            per_dir = run_stream.target_events_dir(base_events_dir, s["host"])
            run_control.clear_control(per_dir)
        return 500, {"ok": False, "error": f"spawn 실패: {spawn_failure}",
                      "partial_started": started,
                      "cleanup_attempted": True}

    return 200, {"ok": True, "started": started}


def _terminate_pid(pid: int) -> None:
    """SIGTERM → 최대 3s pid_alive 폴링 → 살아있으면 SIGKILL. control 정리는 caller.

    bulk stop / spawn 실패 cleanup 양쪽이 자식 종료의 단일 규칙을 공유하도록
    분리. 폴링 간격 100ms × 30회 ≈ 3초 (POSIX signal 전달 + graceful exit
    예산). waitpid 대신 pid_alive 폴링: 요청 핸들러 스레드에서 blocking wait
    가 응답 지연을 만든다.
    """
    _signal_pid(pid, signal.SIGTERM)
    for _ in range(30):
        if not run_control.pid_alive(pid):
            return
        time.sleep(0.1)
    if run_control.pid_alive(pid):
        _signal_pid(pid, signal.SIGKILL)


def _stop_in_events_dir(events_dir: str) -> dict:
    """주어진 events_dir scope 의 관리 런을 SIGTERM→3s→SIGKILL 패턴으로 종료.

    반환:
      - 종료 성공: ``{"stopped": True, "pid": <int>}``
      - 관리 런 없음 / 이미 죽음: ``{"stopped": False, "pid": None, "note": "..."}``

    legacy 단일 런(events_dir=root)과 per-host 런(events_dir=by-target/<slug>/)
    양쪽이 같은 종료 로직을 공유 — control 파일 위치만 다르고 흐름은 동일하므로
    한 helper. ``note`` 는 실패 케이스에서만 포함된다.
    """
    info = run_control.read_control(events_dir)
    pid = info.get("pid") if info else None
    if not isinstance(pid, int) or not run_control.pid_alive(pid):
        run_control.clear_control(events_dir)
        return {"stopped": False, "pid": None, "note": "관리 중인 런 없음"}
    _terminate_pid(pid)
    run_control.clear_control(events_dir)
    return {"stopped": True, "pid": pid}


def stop_run(params: dict | None = None) -> tuple[int, dict]:
    """관리 중인 런 종료. (http_status, body) 반환.

    body 형태 세 가지를 받는다 (multi-target 지원):
      1) **body 없음 / 빈 dict**: legacy 단일 런 (events/ 직속 control). 기존 동작.
      2) **{host: "..."}**: 해당 host 의 per-target control 만 종료.
         host 가 유효하지 않으면 400.
      3) **{targets: ["...", "..."]}**: 다수 host 일괄 종료. 각 host 별로 시도하고
         host 별 결과를 stopped 배열에 모아 반환 (한 host 가 없어도 다른 host 는 진행).
    """
    p = params or {}
    base = _producer_events_dir()

    # (3) 다수 host bulk stop
    if isinstance(p.get("targets"), list):
        hosts = p["targets"]
        if len(hosts) > MAX_CONCURRENT_TARGETS:
            return 400, {"ok": False,
                          "error": f"한 번에 종료 가능한 host 수 상한 초과 ({len(hosts)} > {MAX_CONCURRENT_TARGETS})"}
        for h in hosts:
            if not run_control.is_valid_host(h):
                return 400, {"ok": False, "error": f"invalid host: {h!r}"}
        # 병렬 stop — 순차 실행 시 host 마다 3s 까지 SIGTERM 대기가 누적돼 4 host
        # 비응답 시 응답이 최대 12s 까지 지연된다. 각 stop 은 독립이라 thread pool
        # 로 안전하게 병렬화 — max 응답 시간 ≈ 3s.
        # 빈 targets:[] 는 idempotent stop 으로 200 반환 (start 쪽 빈 list 거부와의
        # 의도된 비대칭): /stop 은 멱등성을 우선 — "혹시 실행 중이면 멈춰라" 가
        # 자연스러운 시맨틱.
        results: list[dict] = []
        if hosts:
            with ThreadPoolExecutor(max_workers=len(hosts)) as ex:
                # host → Future 직접 매핑 — 입력 host 순서를 그대로 보존해 결과
                # 수집 시 동일 순서로 .result() 호출.
                future_by_host = {
                    h: ex.submit(_stop_in_events_dir,
                                 run_stream.target_events_dir(base, h))
                    for h in hosts
                }
                for h in hosts:
                    results.append({"host": h, **future_by_host[h].result()})
        return 200, {"ok": True, "stopped": results}

    # (2) per-host stop
    if p.get("host") is not None:
        host = p["host"]
        if not run_control.is_valid_host(host):
            return 400, {"ok": False, "error": "invalid host"}
        per_dir = run_stream.target_events_dir(base, host)
        r = _stop_in_events_dir(per_dir)
        return 200, {"ok": True, **r, "host": host}

    # (1) legacy 단일 런
    r = _stop_in_events_dir(base)
    return 200, {"ok": True, **r}


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
  .ahint { color:#a89668; font-size:11px; margin:2px 0 6px; }
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
  /* multi-target view: 페이지 자체 너비 확장 + grid 컬럼 */
  .wrap.mt { max-width: 1600px; }
  .mt-bar { display:flex; align-items:center; gap:10px; margin:8px 0 12px;
            padding:8px 12px; background:#0f1722; border:1px solid #1f2a3a; border-radius:8px; }
  .mt-bar .title { color:#9ad; font-size:13px; font-weight:700; }
  .mt-bar .count { color:#7a8; font-size:11px; }
  .mt-bar button { margin-left:auto; border:none; border-radius:6px; padding:5px 10px;
                   font-size:12px; cursor:pointer; }
  .mt-bar .stopall { background:#7c1d1d; color:#fde68a; }
  .mt-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
             gap:12px; margin-bottom:14px; }
  .mt-col { background:#11151d; border:1px solid #283246; border-radius:8px; padding:10px 12px;
             cursor:pointer; /* 컬럼 자체가 click 으로 host 선택 — 시각 affordance */ }
  .mt-col.cur { border-color:#3b82f6; box-shadow:0 0 0 1px #3b82f6 inset; }
  .mt-col .hd { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
  .mt-col .hd .host { font-weight:700; color:#cdd6f4; font-size:13px; }
  .mt-col .hd .plan { color:#9ad; font-size:11px; }
  .mt-col .hd .stop { margin-left:auto; border:none; border-radius:5px; padding:2px 8px;
                       background:#52111a; color:#fca5a5; font-size:11px; cursor:pointer; }
  .mt-col .hd .stop:hover { background:#7c1d1d; }
  .mt-col .mt-meta { color:#7a8; font-size:11px; margin-bottom:4px; }
  .mt-col .mt-bar2 { height:8px; background:#1b1f2a; border-radius:4px; overflow:hidden; margin:4px 0; }
  .mt-col .mt-bar2 > i { display:block; height:100%; background:linear-gradient(90deg,#3b82f6,#22d3ee); width:0; transition:width .4s; }
  .mt-col .mt-stats { display:flex; gap:14px; font-size:11px; margin-top:4px; }
  .mt-col .mt-stats span b { font-size:14px; font-variant-numeric:tabular-nums; }
  .mt-col .mt-cur { color:#fbbf24; font-size:12px; font-weight:600; margin-top:4px;
                    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .mt-col .mt-empty { color:#5b647a; font-size:12px; padding:6px 0; }
  /* multi-target start form (확장 패널) */
  .mtform { background:#0f1722; border:1px solid #1f2a3a; border-radius:8px;
            padding:10px 12px; margin:8px 0; display:none; }
  .mtform.open { display:block; }
  .mtform textarea { width:100%; min-height:60px; background:#0b1220; color:#e5e7eb;
                     border:1px solid #334155; border-radius:6px; padding:6px 8px;
                     font-family:inherit; font-size:12px; box-sizing:border-box; }
  .mtform .hint { color:#7a8; font-size:11px; margin:4px 0 6px; }
  .mtform .row2 { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
  .mtform button { border:none; border-radius:6px; padding:6px 12px; font-size:12px;
                    cursor:pointer; background:#16a34a; color:#fff; }
  /* + Multi-target 토글 버튼 — .ctrl 패널 안의 액션 버튼. 인라인 스타일을 회피해
     CSS 단일 진실 지점 유지. */
  .ctrl .btn-mt-add { margin-left:auto; background:#1e40af; color:#dbeafe;
                       border:none; border-radius:6px; padding:6px 12px;
                       font-size:12px; cursor:pointer; }
</style></head>
<body><div class="wrap" id="wrap">
  <h1 id="title">pim_viewer (web)</h1>
  <div class="ctrl" id="ctrl">
    <div class="crow">
      <select id="c_plan" title="플랜"></select>
      <input class="host" id="c_host" placeholder="타겟 IP (예: 192.168.214.4)">
      <input class="cred" id="c_user" value="root" title="SSH 유저">
      <input class="cred" id="c_pass" type="password" value="root" title="SSH 비밀번호">
      <button class="start" id="c_start">▶ 시작</button>
      <button class="stop" id="c_stop" disabled>■ 중지</button>
      <button class="btn-mt-add" id="c_mt_toggle">+ Multi-target</button>
    </div>
    <div class="cmsg" id="c_msg"></div>
    <!-- multi-target start form (collapsible) -->
    <div class="mtform" id="mtform">
      <div class="hint">여러 타겟을 한 번에 시작 — 호스트를 한 줄에 하나씩 입력 (최대 4개). 위의 plan/user/password 가 모든 타겟에 공유됩니다.</div>
      <textarea id="mt_hosts" rows="5" placeholder="192.168.214.4&#10;192.168.214.5"></textarea>
      <div class="row2" style="margin-top:6px;">
        <button id="mt_start">▶ Start All</button>
        <span class="cmsg" id="mt_msg"></span>
      </div>
    </div>
  </div>
  <!-- multi-target dashboard: hosts.length > 0 일 때만 표시 -->
  <div class="mt-bar" id="mtBar" style="display:none">
    <span class="title">Multi-target 실행 중</span>
    <span class="count" id="mtCount">0 hosts</span>
    <button class="stopall" id="mt_stop_all">■ Stop All</button>
  </div>
  <div class="mt-grid" id="mtGrid" style="display:none"></div>
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
  <div class="box active" id="activeBox" style="display:none"><div class="hd">⚠ 관찰 중 — 진행 중 일시 fail (회복 가능)</div><div class="ahint">아직 확정 아님 — 조건이 갖춰지면(예: 녹화 finalize) 자동 회복됩니다. 확정 결과는 케이스 종료 시점 기준.</div><div id="active"></div></div>
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
    else if(cls[name]==='active'){ const c=document.createElement('span'); c.className='chip chip-active'; c.textContent='⚠ 관찰 중'; el.appendChild(c); }
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
  else if(cd.classification==='active'){ const c=document.createElement('span'); c.className='chip chip-active'; c.textContent='⚠ 관찰 중 (회복 가능)'; hd.appendChild(c); }
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

// tick() 자체 in-flight 가드 — explicit tick() 호출이 3 곳(onclick + 2 auto-
// deselect)으로 늘어 stale response overwriting 가능성 증가. mtTicking2 + pending
// flag 패턴: 이미 fetch 중이면 pending=true 만 set 하고 return, 직전 fetch 끝나면
// do-while 로 coalesce 재실행해서 가장 최근 selection 으로 한 번 더 폴링.
// (단순 return 만 하면 클릭 → silently drop → 1초까지 stale UI 가 됨; Claude r3 권고)
let mtTicking2 = false;
let mtTickPending = false;
async function tick(){
  if(mtTicking2){ mtTickPending = true; return; }
  do {
    mtTicking2 = true;
    mtTickPending = false;
    await tickOnce();
    mtTicking2 = false;
    // pending 이 set 됐다는 건 우리 fetch 도중 selection 이 바뀌었다는 신호 —
    // 한 번 더 돌아 새 selection 으로 fresh fetch.
  } while(mtTickPending);
}
async function tickOnce(){
  try{
    // MT_SELECTED_HOST 가 set 이면 그 host 의 per-target state, 아니면 legacy
    // /state (events/current.jsonl). 두 endpoint 모두 build_state 결과 형식
    // 동일이라 아래 렌더링 코드는 한 path 만 유지.
    // *반드시* tick() 진입 시점에 한 번 snapshot — await 도중 사용자가 다른 컬럼
    // 클릭하거나 tickMulti 가 자동 해제하면 fetch 결과 (d) 와 host indicator 가
    // 어긋난 데이터를 표시한다.
    const selectedHost = MT_SELECTED_HOST;
    const url = selectedHost
      ? '/api/events?host='+encodeURIComponent(selectedHost)+'&_='+Date.now()
      : '/state?_='+Date.now();
    const r = await fetch(url, {cache:'no-store'});
    const d = await r.json();
    LAST = d;
    const foot = document.getElementById('foot');
    if(!d.exists){ SRV.exists=false; document.getElementById('meta').textContent='이벤트 스트림 없음 (pim_check.py --plan 실행 대기)'; foot.textContent='polling…'; return; }
    const hostTag = selectedHost ? '  [◉ '+selectedHost+']' : '';
    document.getElementById('meta').textContent = 'plan='+(d.plan||'?')+'  board='+(d.board||'?')+'  run='+(d.run_id||'?')+hostTag;
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
  // mtTicking2 / mtTickPending 은 caller(tick) 가 do-while 패턴으로 관리.
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
// --- Multi-target view --------------------------------------------------
// /api/active 로 host 목록을 가져온 뒤 각 host 의 /api/events?host=<>
// 를 병렬 폴링해 컬럼 그리드를 그린다. hosts.length === 0 이면 기존
// 단일-host 뷰가 그대로 보이고 (backward compat), 1개 이상이면 multi-grid 활성.
// 서버 MAX_CONCURRENT_TARGETS 와 매칭 — UI 에서도 즉시 차단.
// 초기 fallback — /api/active 응답의 max_concurrent 로 매 tick 마다 업데이트되므로
// 서버 MAX_CONCURRENT_TARGETS 가 바뀌어도 UI 가 silently diverge 하지 않는다.
let MT_MAX = 4;
// 다음 tick() 호출이 어느 host 의 state 를 가져올지 — null 이면 legacy /state
// (events/current.jsonl, last-started host). 컬럼 클릭으로 set, 다시 클릭하면 해제.
let MT_SELECTED_HOST = null;
// 이전 tick 이 아직 in-flight 인데 다음 setTimeout 이 fire 되면 fetch 가 중첩되고
// 결과 순서가 뒤바뀔 수 있다 (race condition). 단순 flag 로 직렬화.
let mtTicking = false;
// 진행 중인 per-host stop 추적 — DOM 의 button 은 매 tick replaceChildren 으로
// 교체돼 click 과 두번째 click 사이에 사라질 수 있다. host name 키 Set 이
// DOM lifecycle 와 무관하게 in-flight 가드로 안정적.
const mtInFlightStops = new Set();
// network error 또는 HTTP error 시 null 로 구분 (빈 hosts {} 와 다름) — caller 가
// UI 를 깜빡이지 않게 이전 상태 유지. r.ok 검사로 5xx HTML 페이지가 r.json() 에서
// SyntaxError 던지는 것도 막는다.
async function fetchActive(){
  try {
    const r = await fetch('/api/active?_='+Date.now(), {cache:'no-store'});
    if(!r.ok) return null;
    return await r.json();
  } catch(e){ return null; }
}
async function fetchHostState(host){
  try {
    const r = await fetch('/api/events?host='+encodeURIComponent(host)+'&_='+Date.now(), {cache:'no-store'});
    if(!r.ok) return null;
    return await r.json();
  } catch(e){ return null; }
}
function mtFmtClock(s){ s=Math.max(0,Math.floor(s||0)); const m=Math.floor(s/60); return m?(m+"m "+(s%60)+"s"):(s+"s"); }
function mtBadge(st){
  // st 가 null (network error) 이거나 stream 없으면 대기 표시 — guard 로 TypeError 방지.
  if(!st || !st.exists) return ['b-lost','대기'];
  if(st.producer_lost) return ['b-lost','Producer lost'];
  if(st.run_ended) return ['b-done','DONE'];
  return ['b-run','RUNNING'];
}
function mtCol(host, plan, st){
  const col = document.createElement('div');
  col.className = 'mt-col' + (host === MT_SELECTED_HOST ? ' cur' : '');
  // data-host 로 즉시 lookup 가능 — text 비교보다 robust (markup 변경에 강건).
  col.dataset.host = host;
  // 컬럼 클릭 → legacy single-view 가 이 host 로 전환. 같은 컬럼 다시 클릭하면 해제
  // (null = events/current.jsonl, last-started host).
  col.onclick = () => {
    MT_SELECTED_HOST = (MT_SELECTED_HOST === host) ? null : host;
    // 즉시 tick 호출 — 1초 폴링 다음 cycle 까지 기다리지 않고 전환 즉시 반영.
    // SEL / OPEN 도 초기화 — 이전 host 의 드릴다운 상태가 새 host case 이름과
    // 다를 수 있으므로 깨끗한 상태로 시작.
    SEL = null; OPEN.clear();
    tick();
    // 컬럼들 시각 갱신 — data-host 비교 (host 변경 / markup refactor 에 robust).
    document.querySelectorAll('.mt-col').forEach(c => {
      c.classList.toggle('cur', c.dataset.host === MT_SELECTED_HOST);
    });
  };
  const hd = document.createElement('div'); hd.className='hd';
  const h = document.createElement('span'); h.className='host'; h.textContent=host; hd.appendChild(h);
  const p = document.createElement('span'); p.className='plan'; p.textContent='plan='+(plan||'?'); hd.appendChild(p);
  const [bcls, btxt] = mtBadge(st);
  const b = document.createElement('span'); b.className='badge '+bcls; b.style.fontSize='10px'; b.style.padding='2px 8px'; b.textContent=btxt; hd.appendChild(b);
  const stop = document.createElement('button'); stop.className='stop'; stop.textContent='■ Stop';
  // type="button" 명시 — 기본 type="submit" 인데 향후 columns 가 form 안으로 들어가도
  // 의도치 않은 폼 제출이 발생하지 않도록 방어.
  stop.type = 'button';
  // data-host 로 stopHost 가 element 를 다시 찾아 disabled 토글 — closure 캡처보다
  // 다음 tick 재구성 후에도 안전 (host 이름 기반 lookup).
  stop.setAttribute('data-host', host);
  stop.onclick = (ev) => { ev.stopPropagation(); stopHost(host); };
  hd.appendChild(stop);
  col.appendChild(hd);
  if(!st || !st.exists){
    const e = document.createElement('div'); e.className='mt-empty'; e.textContent='이벤트 스트림 없음 (시작 대기)'; col.appendChild(e);
    return col;
  }
  const meta = document.createElement('div'); meta.className='mt-meta';
  meta.textContent = 'run='+(st.run_id||'?')+'  ·  경과 '+mtFmtClock(st.elapsed_s);
  col.appendChild(meta);
  const bar = document.createElement('div'); bar.className='mt-bar2';
  const i = document.createElement('i'); const pct = st.total ? Math.round(100*st.completed/st.total) : 0;
  i.style.width = pct+'%'; bar.appendChild(i); col.appendChild(bar);
  const sub = document.createElement('div'); sub.className='mt-meta';
  sub.textContent = st.completed+' / '+st.total+' ('+pct+'%)';
  col.appendChild(sub);
  // PASS/FAIL: createElement 만 사용 (innerHTML 회피 — 파일 전체 정책 일관성).
  const stats = document.createElement('div'); stats.className='mt-stats';
  const sp = document.createElement('span'); sp.appendChild(document.createTextNode('PASS '));
  const spb = document.createElement('b'); spb.style.color='#4ade80'; spb.textContent=String(st['pass']);
  sp.appendChild(spb);
  const sf = document.createElement('span'); sf.appendChild(document.createTextNode('FAIL '));
  const sfb = document.createElement('b'); sfb.style.color='#f87171'; sfb.textContent=String(st.fail);
  sf.appendChild(sfb);
  stats.appendChild(sp); stats.appendChild(sf); col.appendChild(stats);
  if(st.current){
    const cur = document.createElement('div'); cur.className='mt-cur'; cur.textContent='▶ '+st.current+(st.pending?' (⏳ 준비 중)':'');
    col.appendChild(cur);
  } else if(st.run_ended){
    const done = document.createElement('div'); done.className='mt-cur'; done.style.color='#7aa2f7';
    done.textContent = '완료 — '+st['pass']+'/'+st.total+' pass'; col.appendChild(done);
  }
  return col;
}
// 활성 호스트만 추려 stop 대상으로 사용 — active.json 에 누적된 stale 항목까지
// 보내면 MT_MAX 초과로 stop 자체가 400 에 막힌다.
function mtRunningHosts(hosts, states){
  return hosts.filter((h, i) => states[i] && states[i].exists
                       && !states[i].run_ended && !states[i].producer_lost);
}
async function tickMulti(){
  if(mtTicking) return;  // 직전 tick in-flight → 중복 실행 방지 (race window 차단)
  // 백그라운드 탭에서는 사용자가 보지 않는데 5 fetches/1.5s 로 무의미한 트래픽 누적.
  // 5초 간격으로 늦춰 reload 시 즉시 fresh 상태로 회복은 유지.
  // mtTicking 을 굳이 set 하지 않는다 — tick chain 은 단일 setTimeout 이라 동시
  // 실행이 발생할 수 없고, set 하면 throttle window 안 일찍 다른 caller (예:
  // visibilitychange 핸들러 추가 시) 가 fast-path 진입을 못 한다.
  if(document.hidden){ setTimeout(tickMulti, 5000); return; }
  mtTicking = true;
  try {
    const data = await fetchActive();
    if(data === null) return;  // network error → 이전 UI 유지, 다음 tick 재시도
    // server cap 을 매 tick 마다 동기화 — MAX_CONCURRENT_TARGETS 가 서버에서 바뀌면
    // 다음 tick 부터 UI 도 새 한도로 검증.
    if(typeof data.max_concurrent === 'number' && data.max_concurrent > 0){
      MT_MAX = data.max_concurrent;
    }
    const hosts = data.hosts || [];
    const bar = document.getElementById('mtBar');
    const grid = document.getElementById('mtGrid');
    const wrap = document.getElementById('wrap');
    if(hosts.length === 0){
      bar.style.display='none'; grid.style.display='none'; wrap.classList.remove('mt');
      // 활성 host 모두 사라지면 selection 도 자동 해제. 이전에 selection 이
      // 있었다면 즉시 tick() 호출 — 1초 폴링 cycle 까지 stale detail 이 남는 lag
      // 회피 (Gemini/Claude 공통 권고).
      if(MT_SELECTED_HOST !== null){
        MT_SELECTED_HOST = null;
        tick();
      }
      return;
    }
    // 선택된 host 가 active.json 에서 사라졌으면 자동 해제 (run 종료/stop 후).
    // 즉시 tick() 으로 legacy /state 갱신 — 1초 lag 회피.
    if(MT_SELECTED_HOST && !hosts.some(h => h.host === MT_SELECTED_HOST)){
      MT_SELECTED_HOST = null;
      tick();
    }
    // hosts 가 있으면 multi-grid 활성, 페이지 너비 확장.
    wrap.classList.add('mt');
    bar.style.display='flex'; grid.style.display='grid';
    document.getElementById('mtCount').textContent = hosts.length+' host'+(hosts.length>1?'s':'');
    // 모든 host 의 state 를 병렬 fetch (실패한 host 는 null → mtCol 이 대기 표시).
    const states = await Promise.all(hosts.map(h => fetchHostState(h.host)));
    // 매 tick 마다 grid 전체 재구성 — N<=4 라 성능 영향 미미. 부분 갱신/diff 는 향후
    // 필요 시 mtCol 에 update path 추가하는 식으로 확장 가능 (현재는 단순성 우선).
    // h.slug 는 디버그/식별 메타데이터로 응답에 포함되지만 UI 는 raw host name 만
    // 표시 (사용자 친숙성 우선). slug 는 server 측 by-target/ 경로 매핑에만 쓰이고,
    // 클라이언트는 항상 host 를 그대로 보내면 server 가 slug 변환.
    grid.replaceChildren(...hosts.map((h, i) => mtCol(h.host, h.plan, states[i])));
  } finally {
    mtTicking = false;
    // setInterval 대신 setTimeout 재예약 — async 가 1.5s 안에 끝나지 못해도 중복 fire
    // 안 되고, 다음 tick 은 항상 완료 후 1.5s 시작 (실제 폴링 간격이 일정해진다).
    setTimeout(tickMulti, 1500);
  }
}
// r.ok / r.json() 처리를 한 헬퍼로 모은다 — 500 HTML 페이지를 r.json() 이 받아
// SyntaxError 를 던지는 패턴을 막고, 모든 호출이 동일한 (ok, body, status) 모양을
// 보게 한다. fetch 자체 실패(네트워크) 도 ok:false 로 매핑.
async function mtPostJSON(url, payload){
  try {
    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                                body: JSON.stringify(payload)});
    let body = {};
    try { body = await r.json(); } catch(_e){ body = {error: 'invalid JSON response'}; }
    return {ok: r.ok && body.ok !== false, body: body, status: r.status};
  } catch(e){ return {ok: false, body: {error: String(e)}, status: 0}; }
}
async function stopHost(host){
  // Set 기반 in-flight 추적 — DOM 버튼은 매 tick replaceChildren 으로 교체돼
  // 두 번째 클릭 시점에 querySelector(null) 로 guard 가 silent 우회되는 race
  // 가 있었다. host name 키는 DOM lifecycle 무관.
  if(mtInFlightStops.has(host)) return;
  mtInFlightStops.add(host);
  // 버튼 시각 비활성화도 함께 (사용자 피드백) — 다음 tick 까지만 효과지만
  // 그 사이 두 번째 클릭 동기 guard 로 mtInFlightStops 가 빠르게 거른다.
  const btn = document.querySelector('.mt-col .stop[data-host="'+CSS.escape(host)+'"]');
  if(btn) btn.disabled = true;
  try {
    if(!confirm('Stop host '+host+'?')) return;
    const res = await mtPostJSON('/stop', {host: host});
    if(!res.ok) alert('Stop 실패: '+(res.body.error || ('HTTP '+res.status)));
    // 다음 tick 이 곧 fire 되므로 명시 호출 불필요.
  } finally {
    mtInFlightStops.delete(host);
    // btn 은 이미 replaceChildren 으로 사라졌을 수 있어 null check 후 re-enable.
    if(btn && btn.isConnected) btn.disabled = false;
  }
}
async function stopAll(){
  const btn = document.getElementById('mt_stop_all');
  // disabled 를 *모든 await 이전에* 설정 — fetchActive/Promise.all 등 await 도중
  // 두 번째 click 이 들어와도 두 동시 호출 모두 guard 를 통과해서 confirm 가
  // 중복 뜨고 stop 도 중복 전송되는 race 를 막는다.
  if(btn.disabled) return;
  btn.disabled = true;
  try {
    const data = await fetchActive();
    if(data === null){ alert('활성 호스트 목록을 가져오지 못했습니다.'); return; }
    const hosts = (data.hosts||[]).map(h => h.host);
    if(hosts.length === 0) return;
    // 활성 host 만 필터 — server 가 spawn 시점에 MAX_CONCURRENT_TARGETS 를 강제하므로
    // running.length 가 MT_MAX 를 넘는 일은 정상 흐름에 없음. 만약 초과한다면 server
    // cap 변경 / stale active.json edge case 신호 — 잘라내 묵음 처리하기보다 console
    // 경고로 노출 (silently skip 했다가 사용자가 "왜 일부만 멈춰?" 가 더 혼란스럽다).
    const states = await Promise.all(hosts.map(h => fetchHostState(h)));
    const running = mtRunningHosts(hosts, states);
    if(running.length > MT_MAX){
      console.warn('mt: running.length('+running.length+') > MT_MAX('+MT_MAX+') — server limit drift 의심');
    }
    if(running.length === 0){ alert('실행 중인 host 가 없습니다 (이미 완료/중지됨).'); return; }
    if(!confirm('Stop '+running.length+' running host'+(running.length>1?'s':'')+'?')) return;
    const res = await mtPostJSON('/stop', {targets: running});
    if(!res.ok) alert('Stop All 실패: '+(res.body.error || ('HTTP '+res.status)));
  } finally { btn.disabled = false; }
}
async function startMulti(){
  const btn = document.getElementById('mt_start');
  if(btn.disabled) return;  // double-submit 가드 — 빠른 연속 클릭으로 spawn 중복 방지.
  // 주의: backslash-n 을 JS 문자열로 쓰려면 Python 삼중따옴표 안에서 backslash 를
  // 두 번 써야 한다 (한 번이면 Python 이 newline 으로 치환해 JS unterminated string
  // literal 이 되어 전체 script parse 실패).
  const hosts = document.getElementById('mt_hosts').value.split('\\n')
    .map(s => s.trim()).filter(Boolean);
  const plan = document.getElementById('c_plan').value;
  const user = document.getElementById('c_user').value.trim();
  const password = document.getElementById('c_pass').value;
  const msg = document.getElementById('mt_msg');
  if(!plan){ msg.textContent='plan 을 선택하세요'; msg.className='cmsg err'; return; }
  if(hosts.length === 0){ msg.textContent='host 를 한 줄 이상 입력하세요'; msg.className='cmsg err'; return; }
  if(hosts.length > MT_MAX){
    msg.textContent='최대 '+MT_MAX+'개까지 가능 ('+hosts.length+'개 입력됨)'; msg.className='cmsg err'; return;
  }
  const targets = hosts.map(h => ({host: h, user: user, password: password}));
  msg.textContent='시작 중…'; msg.className='cmsg';
  btn.disabled = true;
  try {
    const res = await mtPostJSON('/start', {plan: plan, targets: targets});
    if(res.ok){
      msg.textContent = '시작됨 — '+(res.body.started||[]).length+' host';
      msg.className='cmsg ok';
      document.getElementById('mtform').classList.remove('open');
    } else {
      msg.textContent = '실패: '+(res.body.error || ('HTTP '+res.status));
      msg.className='cmsg err';
    }
  } finally { btn.disabled = false; }
}
document.getElementById('mt_stop_all').onclick = stopAll;
document.getElementById('mt_start').onclick = startMulti;
document.getElementById('c_mt_toggle').onclick = () => {
  document.getElementById('mtform').classList.toggle('open');
};
// 최초 1회 fire — 이후는 tickMulti 자체가 setTimeout 으로 재예약 (setInterval 회피).
tickMulti();
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
        elif self.path.startswith("/api/active"):
            # multi-target viewer 의 host enumerate 용 — 별도 라우트로 두어 기존
            # /state (단일 stream) 와 명확히 분리한다. max_concurrent 도 함께 노출해
            # 클라이언트가 JS 상수와 server cap 의 single source of truth 를 유지.
            # spread 로 새 dict 생성 — active_hosts() 가 dict 를 그대로 반환할 때
            # in-place 변경되는 것을 막아 향후 caching 도입 시에도 안전.
            self._send_json(200, {**active_hosts(),
                                  "max_concurrent": MAX_CONCURRENT_TARGETS})
        elif self.path.startswith("/api/events"):
            # per-host state — viewer 가 host 별 컬럼마다 폴링한다. 다른 JSON
            # endpoint 들과 일관성 있게 _send_json 사용 (Content-Type / Cache-Control
            # / Content-Length 처리를 단일 헬퍼에 위임).
            qs = parse_qs(urlsplit(self.path).query)
            hosts = qs.get("host") or []
            if not hosts:
                # 다른 early-exit 분기와 동일하게 명시적 return — 향후 분기 추가 시
                # 실수로 두 번 응답하는 것 방지 (defensive control flow).
                self._send_json(400, {"ok": False, "error": "host query 가 필요합니다"})
                return
            self._send_json(200, host_events_state(hosts[0]))
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
            if params is None or not isinstance(params, dict):
                self._send_json(400, {"ok": False, "error": "잘못된 요청 본문 (dict 필요)"})
                return
            code, body = start_run(params)
            self._send_json(code, body)
        elif route == "/stop":
            # body 가 있으면 multi-target stop (host=... 또는 targets=[...]).
            # body 없거나 빈 dict 면 legacy 단일 런 종료 (호환 유지).
            # /start 와 일관성 — list/원시값 등 dict 가 아닌 body 는 400.
            params = self._read_json()
            if params is None:
                self._send_json(400, {"ok": False, "error": "잘못된 요청 본문"})
                return
            if params and not isinstance(params, dict):
                self._send_json(400, {"ok": False, "error": "요청 본문은 dict 여야 합니다"})
                return
            code, body = stop_run(params if params else None)
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
    # 자동 시작 옵션 — 브라우저에서 수동 시작 없이 CLI 한 줄로 뷰어+플랜 실행.
    ap.add_argument("--plan", dest="auto_plan", default=None,
                    help="자동 시작할 플랜명 (지정 시 서버 기동 후 즉시 실행)")
    ap.add_argument("--target-host", default=None,
                    help="DUT SSH 호스트 (--plan 과 함께)")
    ap.add_argument("--user", default="root",
                    help="DUT SSH 사용자 (기본 root)")
    ap.add_argument("--password", default=None,
                    help="DUT SSH 비밀번호 (미지정 시 PIM_PASSWORD 환경변수)")
    ap.add_argument("--until-pass", action="store_true", dest="until_pass",
                    help="모든 체크 통과 시 자동 종료 (--plan 과 함께)")
    args = ap.parse_args(argv)
    # --plan 사용 시 --target-host 필수 — 자식 spawn 시점에 발견하지 말고
    # CLI 진입점에서 명확한 에러로 차단(빈 host 가 subprocess 깊은 곳까지 흘러가는 silent fail 방지).
    if args.auto_plan and not args.target_host:
        print("pim_web_viewer: --plan 사용 시 --target-host 필요", file=sys.stderr)
        return 2
    _Handler.events_path = _events_path(args.path)
    # spawn 한 plan 런 자식 프로세스가 종료/중지 시 좀비로 남지 않도록 자동 reap.
    # (start_run 은 fire-and-forget Popen 이라 wait() 하지 않음)
    try:
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    except (ValueError, AttributeError, OSError):
        pass  # 비-POSIX 또는 비-메인스레드: 좀비 누적 감수
    srv = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"pim_web_viewer: http://localhost:{args.port}  (events: {_Handler.events_path})", flush=True)
    if args.auto_plan:
        def _auto_start() -> None:
            # 서버 bind 는 ThreadingHTTPServer() 가 동기 완료해 thread 시작 시점엔 이미 listen 중.
            # start_run() 도 HTTP 가 아니라 Python 함수 호출이라 server-ready 대기 불필요.
            password = args.password or os.environ.get("PIM_PASSWORD", "")
            params = {
                "plan": args.auto_plan,
                "host": args.target_host,
                "user": args.user,
                "password": password,
                "until_pass": args.until_pass,
            }
            code, body = start_run(params)
            if code == 200:
                print(f"pim_web_viewer: auto-start OK — plan={args.auto_plan} pid={body.get('pid')}", flush=True)
            else:
                print(f"pim_web_viewer: auto-start FAIL — {body.get('error')}", file=sys.stderr, flush=True)

        threading.Thread(target=_auto_start, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
