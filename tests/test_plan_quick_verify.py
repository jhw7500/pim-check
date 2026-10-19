"""
tests/test_plan_quick_verify.py — quick_verify plan 계약.

- finalize 의존 custom_commands 분류기(checks.custom.needs_finalized_recording)를
  comprehensive 의 실제 케이스 파일 전체에 대해 범주별로 고정한다.
- plan 옵션(execution.skip_finalize_checks, case_overrides) lint 와 적용.
- quick_verify 는 같은 케이스에서 finalize 항목만 빼고 frame_flow 를 켠다.
- quick_verify 는 recording readiness 단계(readiness.recording: false)와 녹화 파일 ≥1
  항목(checks.custom.needs_recording_file)을 뺀다.
- quick_verify 는 reset_mode reboot — 모든 케이스와 캠페인 끝 복원이 reboot 이고
  require_hard_reset strict 게이트는 걸리지 않는다.
- comprehensive 는 아무것도 바뀌지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import pim_check
import plan as plan_mod
import run_stream
from checks.custom import needs_finalized_recording, needs_recording_file
from config import load_profile
from plan import (
    DEFAULT_EXECUTION,
    execute_plan,
    lint_plan,
    load_plan,
    plan_runtime_profile,
    resolve_cases,
)
from setup import (
    RECORDING_DIRS,
    RECOVERY_AUDIT_PATH,
    RECOVERY_HISTORY_DIR,
    RECOVERY_OWNER_PATH,
    SetupManager,
    readiness_kwargs,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = str(PROJECT_ROOT / "profiles")
COMPREHENSIVE = str(PROJECT_ROOT / "profiles" / "plans" / "comprehensive.yaml")
QUICK = str(PROJECT_ROOT / "profiles" / "plans" / "quick_verify.yaml")
NON_CAMERA = "process_restart_smoke"

# 범주: (항목 이름 패턴, comprehensive 20 카메라 케이스 기준 항목 수)
FINALIZE_CATEGORIES = {
    "bitrate": (r"bitrate|BITRATE", 45),
    "duration": (r"duration", 40),
    "file_integrity": (r"파일 무결성", 36),
    "fps_from_file": (r"(mp4|ts) fps ==", 11),
    "srt_sync": (r"srt sync", 3),
    "mux_format": (r"format = mpegts", 1),
}
KEEP_CATEGORIES = {
    "isp_i2c_readback": (r"ROTATION|AE_CTRL|AE_GAIN|AWB_CTRL|EXP_TIME", 195),
    "cam_state_error": (r"cam_state error count", 36),
    "jq_config_readback": (r"QP auto precondition|led_flash", 13),
    "dmesg_fsync_diag": (r"dmesg max9296_fsync", 20),
    "file_absence": (r"disabled \(|\.srt 파일 0건", 6),
    "file_presence": (r"파일 생성|\.srt 파일 ≥1", 4),
}
# finalize 와 무관하게 남지만 녹화 파일이 **하나 이상** 있어야 통과하는 범주 —
# readiness.recording: false (quick_verify) 면 첫 파일 전에 수집이 시작되므로 뺀다.
RECORDING_FILE_CATEGORIES = {"file_presence"}


def _cases(plan_path):
    return [cn for _s, cn in resolve_cases(load_plan(plan_path), PROFILES_DIR)]


def _items(case_name):
    prof = load_profile(PROFILES_DIR, case=case_name)
    return prof["checks"].get("custom_commands") or []


def _category(name):
    hits = [cat for cat, (pat, _n) in {**FINALIZE_CATEGORIES, **KEEP_CATEGORIES}.items()
            if re.search(pat, name)]
    assert len(hits) == 1, f"{name!r} matched {hits}"
    return hits[0]


# ── 분류기 (실제 케이스 파일) ─────────────────────────────────────

def test_classifier_matches_inventory_per_category():
    counts: dict[str, int] = {}
    for case_name in _cases(COMPREHENSIVE):
        if case_name == NON_CAMERA:
            continue
        for item in _items(case_name):
            cat = _category(item["name"])
            counts[cat] = counts.get(cat, 0) + 1
            assert needs_finalized_recording(item) is (cat in FINALIZE_CATEGORIES), (
                case_name, item["name"])
            if cat in KEEP_CATEGORIES:
                assert needs_recording_file(item) is (cat in RECORDING_FILE_CATEGORIES), (
                    case_name, item["name"])
    expected = {cat: n for cat, (_p, n) in {**FINALIZE_CATEGORIES, **KEEP_CATEGORIES}.items()}
    assert counts == expected
    assert sum(counts[c] for c in FINALIZE_CATEGORIES) == 136
    assert sum(counts[c] for c in KEEP_CATEGORIES) == 274
    assert sum(counts[c] for c in RECORDING_FILE_CATEGORIES) == 4


def test_recording_file_items_are_exactly_the_four_presence_checks():
    hits = [(cn, i["name"]) for cn in _cases(COMPREHENSIVE) for i in _items(cn)
            if not needs_finalized_recording(i) and needs_recording_file(i)]
    assert hits == [
        ("fhd_2ch_03", "ts 파일 생성 (직전 2분)"),
        ("fhd_2ch_03", "최근 2분 내 .srt 파일 ≥1 (srt_enable=true 실측)"),
        ("fhd_3ch_012", "최근 2분 내 .srt 파일 ≥1 (srt_enable=true 실측)"),
        ("fhd_4ch", "최근 2분 내 .srt 파일 ≥1 (srt_enable=true 실측)"),
    ]


@pytest.mark.parametrize("case_name,item_name,needs_file", [
    ("fhd_2ch_03", "ts 파일 생성 (직전 2분)", True),
    ("fhd_4ch", "최근 2분 내 .srt 파일 ≥1 (srt_enable=true 실측)", True),
    # 부재 확인(기대 0건)은 첫 파일 전에도 참이다 — 유지
    ("720p_2ch", "최근 2분 내 .srt 파일 0건 (srt_enable=false 실측)", False),
    ("720p_2ch", "ch2 disabled (i2c1.ch2.enable=false → mp4 0건)", False),
    # expected_min 1 이지만 녹화 경로가 아니다
    (NON_CAMERA, "재시작 후 ord 포트 리슨 (10007)", False),
])
def test_recording_file_classifier_representative_items(case_name, item_name, needs_file):
    item = next(i for i in _items(case_name) if i["name"] == item_name)
    assert needs_recording_file(item) is needs_file


def test_classifier_keeps_every_process_restart_item():
    items = _items(NON_CAMERA)
    assert len(items) == 5
    assert not any(needs_finalized_recording(i) for i in items)
    assert not any(needs_recording_file(i) for i in items)


# ── readiness.recording knob ─────────────────────────────────────

@pytest.mark.parametrize("profile,enabled", [
    ({}, True),
    ({"readiness": {}}, True),
    ({"readiness": {"recording": True}}, True),
    ({"readiness": {"recording": False}}, False),
    # 명시적 bool false 만 끈다 — 오타·문자열은 기본(대기)으로 안전하게
    ({"readiness": {"recording": "false"}}, True),
    ({"readiness": {"recording": None}}, True),
    ({"readiness": "off"}, True),
])
def test_readiness_recording_knob(profile, enabled):
    paths = readiness_kwargs(profile)["ready_recording_paths"]
    assert paths == (RECORDING_DIRS if enabled else [])


@pytest.mark.parametrize("case_name,item_name,needs_finalize", [
    ("multi_4ch_fhd", "ch0 BITRATE (~1024kbps ±20%, file-delta 5s)", True),
    ("multi_4ch_fhd", "ch0 duration (~60s ±5s, 첫 짧은 finalize skip)", True),
    ("multi_4ch_fhd", "ch0 파일 무결성 (codec=hevc, 1920x1080, fps≈15, .mp4)", True),
    ("720p_2ch", "ch0 mp4 fps == edgeconf fps (±0.5) — latest finalized session", True),
    ("720p_2ch", "ch0 bitrate ~ edgeconf bps[1] (±20%) — latest finalized session", True),
    ("fhd_3ch_012", "ch0 mp4와 srt sync (NR==2 session에서 둘 다 finalize)", True),
    ("fhd_2ch_03", "ts 파일 format = mpegts", True),
    ("fhd_2ch_03", "ch0 ts fps == 30 (±0.5)", True),
    ("multi_4ch_fhd", "ch0 ROTATION (vflip=false, hflip=true)", False),
    ("multi_4ch_fhd", "ch0 AE_GAIN (gain=512, manual mode)", False),
    ("multi_4ch_fhd", "bus1 EXP_TIME (20000us)", False),
    ("720p_2ch", "ch1 AWB_CTRL (d65)", False),
    ("multi_4ch_fhd", "ch0 cam_state error count", False),
    ("720p_2ch", "ch0 BPS QP auto precondition", False),
    ("fhd_2ch_03", "ch0 led_flash (T/20/delay=0)", False),
    ("720p_2ch", "dmesg max9296_fsync fps (진단: 기대 30, 게이팅 없음)", False),
    ("720p_2ch", "ch2 disabled (i2c1.ch2.enable=false → mp4 0건)", False),
    ("fhd_2ch_03", "ts 파일 생성 (직전 2분)", False),
    ("fhd_2ch_03", "최근 2분 내 .srt 파일 ≥1 (srt_enable=true 실측)", False),
])
def test_classifier_representative_items(case_name, item_name, needs_finalize):
    item = next(i for i in _items(case_name) if i["name"] == item_name)
    assert needs_finalized_recording(item) is needs_finalize


# ── plan 옵션 lint ────────────────────────────────────────────────

VALID = {
    "name": "x", "description": "x", "version": 1,
    "cases": {"regression": ["720p_2ch"]},
    "gate": {"threshold_pass_rate": 1.0},
}


def test_skip_finalize_checks_defaults_off():
    assert DEFAULT_EXECUTION["skip_finalize_checks"] is False


@pytest.mark.parametrize("value,ok", [(True, True), (False, True), ("true", False), (1, False)])
def test_skip_finalize_checks_must_be_bool(value, ok):
    errors = lint_plan({**VALID, "execution": {"skip_finalize_checks": value}})
    assert (errors == []) is ok, errors
    if not ok:
        assert any("skip_finalize_checks" in e for e in errors)


@pytest.mark.parametrize("value,ok", [({"checks": {}}, True), ([], False), ("x", False)])
def test_case_overrides_must_be_mapping(value, ok):
    errors = lint_plan({**VALID, "case_overrides": value})
    assert (errors == []) is ok, errors
    if not ok:
        assert any("case_overrides" in e for e in errors)


def test_plan_without_case_overrides_loads_empty():
    assert load_plan(COMPREHENSIVE).case_overrides == {}


# ── quick_verify plan ─────────────────────────────────────────────

def test_quick_verify_runs_the_same_cases_as_comprehensive():
    assert _cases(QUICK) == _cases(COMPREHENSIVE)
    assert len(_cases(QUICK)) == 21


def test_quick_verify_execution_gate_reports():
    plan = load_plan(QUICK)
    ex = plan.execution
    assert ex["stop_on_fail"] is True
    assert ex["case_retry"] == 2
    assert ex["retry_wait_sec"] == 90
    assert ex["reboot_wait_sec"] == 300
    assert ex["monitor_until_pass"] is True
    assert ex["skip_finalize_checks"] is True
    assert ex["reset_mode"] == "reboot"
    assert plan.gate["threshold_pass_rate"] == 1.0
    assert {(r["format"], r["path"]) for r in plan.reports} == {
        ("html", "reports/quick_verify/{timestamp}.html"),
        ("junit", "reports/quick_verify/{timestamp}.xml"),
        ("json", "reports/quick_verify/{timestamp}.json"),
        ("markdown_summary", "reports/quick_verify/{timestamp}.md"),
    }


def test_quick_verify_runtime_profiles_drop_finalize_keep_rest_enable_frame_flow():
    plan = load_plan(QUICK)
    kept_total = 0
    for case_name in _cases(QUICK):
        case_profile = load_profile(PROFILES_DIR, case=case_name)
        runtime = plan_runtime_profile(plan, case_profile)
        kept = runtime["checks"].get("custom_commands") or []
        expected = [i for i in _items(case_name)
                    if not needs_finalized_recording(i) and not needs_recording_file(i)]
        assert kept == expected, case_name
        if case_name != NON_CAMERA:
            kept_total += len(kept)
        # recording readiness 단계만 빠지고 나머지 readiness 는 케이스 그대로
        kw, base_kw = readiness_kwargs(runtime), readiness_kwargs(case_profile)
        assert kw["ready_recording_paths"] == [], case_name
        assert base_kw["ready_recording_paths"] == RECORDING_DIRS, case_name
        kw.pop("ready_recording_paths")
        base_kw.pop("ready_recording_paths")
        assert kw == base_kw, case_name
        # knob 은 setup 밖에 둔다 — setup 이 없는 케이스에 빈 setup 이 생기지 않는다
        assert ("setup" in runtime) == ("setup" in case_profile), case_name
        assert not any("ffprobe" in i["command"] or "NEED_2_FINALIZES" in i["command"]
                       for i in kept), case_name
        ff = runtime["checks"]["frame_flow"]
        assert ff["enabled"] is True, case_name
        if case_name == NON_CAMERA:
            # gstApp 을 pkill 하는 주입 케이스 — cam_health 와 같이 path: null 로 opt-out.
            assert ff["path"] is None
        else:
            assert ff["path"] == "/run/pim-camera/gstApp.json", case_name
            assert any("i2ctransfer" in i["command"] for i in kept), case_name
        # 입력 profile 은 변경되지 않는다
        assert case_profile["checks"].get("custom_commands") == _items(case_name)
    # comprehensive 410 중 finalize 136 + 녹화 파일 ≥1 4 를 뺀 270
    assert kept_total == 270


def test_comprehensive_runtime_profiles_are_unchanged():
    plan = load_plan(COMPREHENSIVE)
    assert plan.execution["reset_mode"] == "reboot"
    for case_name in _cases(COMPREHENSIVE):
        case_profile = load_profile(PROFILES_DIR, case=case_name)
        runtime = plan_runtime_profile(plan, case_profile)
        assert runtime == case_profile, case_name
        assert runtime["checks"]["frame_flow"]["enabled"] is False
        assert readiness_kwargs(runtime)["ready_recording_paths"] == RECORDING_DIRS


# ── reset_mode: quick_verify·comprehensive 모두 reboot ────────────

REQ_ID = "01234567-89ab-cdef-0123-456789abcdef"
OWNER = {"boot_id": "b", "invocation_id": "inv", "token": "t", "pid": 101,
         "proc_start_time": "2075", "lifecycle": "ACTIVE"}
HISTORY = {
    "request": {"id": REQ_ID, "type": "apply_config", "source": "pim-check",
                "status": "SUCCEEDED", "rc": 0, "owner": OWNER},
    "actions": [{"action": "camera_hard_reset", "request_id": REQ_ID, "status": "SUCCEEDED",
                 "rc": 0, "started_at": 1728990001, "finished_at": 1728990005}],
}


def _board_reply(command, **_kw):
    """camera13 이 apply_config 를 검증 가능하게 성공시키는 보드 (strict 증거 포함)."""
    if "cam-recoveryctl" in command:
        return (f"CAM_RECOVERY_RESULT id={REQ_ID} type=apply_config status=SUCCEEDED rc=0\n"
                "PIM_RECOVERY_CLI_RC=0")
    if command == f"cat {RECOVERY_OWNER_PATH}":
        return json.dumps(OWNER)
    if command == f"cat {RECOVERY_HISTORY_DIR}/{REQ_ID}.json":
        return json.dumps(HISTORY)
    if command.startswith("printf ") and RECOVERY_AUDIT_PATH in command:
        return shlex.split(command)[2]
    return "OK"


def _reset_outcomes(plan_path):
    """plan 을 실제 run_setup → reset_and_wait 로 돌려 리셋 결정을 모은다.

    보드 동작(설정 적용·스냅샷·앵커·reboot·안정화·검사)만 대체한다.
    Returns: [(케이스 또는 "<campaign restore>", reset_mode, ord_changed, 결과,
               require_hard_reset)]
    """
    outcomes: list = []
    current = {"case": None}
    real_reset = SetupManager.reset_and_wait
    real_teardown = SetupManager.run_teardown
    real_single = plan_mod._run_single_case

    def reset_spy(self, mode, stabilize_sec, **kw):
        result = real_reset(self, mode, stabilize_sec, **kw)
        outcomes.append((current["case"], mode, kw["ord_changed"], result,
                         kw.get("require_hard_reset", False)))
        return result

    def teardown_spy(self, setup_config, teardown_config=None):
        if setup_config:                    # 케이스별 recovery 는 빈 setup 으로 온다
            current["case"] = "<campaign restore>"
        return real_teardown(self, setup_config, teardown_config)

    def single_spy(ssh, profile, case_name, *a, **kw):
        current["case"] = case_name
        return real_single(ssh, profile, case_name, *a, **kw)

    def setup_factory(ssh):
        mgr = SetupManager(ssh)
        mgr.check_current = MagicMock(return_value=False)
        mgr.backup = MagicMock(return_value=True)
        mgr.apply_changes = MagicMock()
        mgr._snapshot_or_warn = MagicMock(
            side_effect=lambda path: mgr._config_snapshots.__setitem__(path, "snap"))
        mgr._restore_conf = MagicMock()
        mgr._prime_same_boot_anchor = MagicMock(return_value=True)
        mgr.reboot_and_wait = MagicMock()
        mgr._stabilize = MagicMock()
        mgr._local0_log = MagicMock()
        return mgr

    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    ssh.run.side_effect = _board_reply
    passed = ([{"name": "x", "passed": True, "reason": "OK"}], None, 1)
    with patch.object(SetupManager, "reset_and_wait", reset_spy), \
            patch.object(SetupManager, "run_teardown", teardown_spy), \
            patch("plan._run_single_case", side_effect=single_spy), \
            patch("verify_retry.run_verify_with_retry", return_value=passed):
        executions = execute_plan(load_plan(plan_path), PROFILES_DIR,
                                  ssh_factory=lambda h, u, p: ssh,
                                  setup_factory=setup_factory, engine_factory=MagicMock())
    assert all(e.passed for e in executions), [(e.case_name, e.error) for e in executions]
    return outcomes


ORD_CASES = {"720p_2ch", "fhd_2ch_03", "fhd_3ch_012", "fhd_4ch"}


STRICT_CASES = {"multi_4ch_720p", "multi_1ch_0_fhd"}


def test_quick_verify_every_case_reboots_and_never_engages_strict_gate():
    # strict 게이트를 가진 케이스가 실제로 있어야 "걸리지 않는다"가 공허하지 않다
    for case_name in STRICT_CASES:
        assert load_profile(PROFILES_DIR, case=case_name)["setup"]["require_hard_reset"]
    outcomes = _reset_outcomes(QUICK)
    per_case = {c: (mode, ord_changed, result, strict)
                for c, mode, ord_changed, result, strict in outcomes
                if c != "<campaign restore>"}
    multi = [c for c in _cases(QUICK) if c.startswith("multi_")]
    assert len(multi) == 16
    assert STRICT_CASES <= set(multi)
    assert set(per_case) == set(multi) | ORD_CASES      # process_restart_smoke: setup 없음
    for case_name in multi:
        assert per_case[case_name] == ("reboot", False, "reboot", False), case_name
    for case_name in ORD_CASES:
        assert per_case[case_name] == ("reboot", True, "reboot", False), case_name
    restore = [o for o in outcomes if o[0] == "<campaign restore>"]
    assert restore == [("<campaign restore>", "reboot", True, "reboot", False)]


def test_comprehensive_still_reboots_every_case():
    outcomes = _reset_outcomes(COMPREHENSIVE)
    assert outcomes and {(mode, result) for _c, mode, _o, result, _s in outcomes} == {
        ("reboot", "reboot")}


def _profiles_seen_by_execute_plan(plan_path):
    seen: dict = {}

    def fake_run(ssh, profile, case_name, *args, **kwargs):
        seen[case_name] = profile
        return [{"name": "x", "passed": True, "reason": "OK"}], True, None

    ssh = MagicMock()
    ssh.check_connectivity.return_value = True
    with patch("plan._run_single_case", side_effect=fake_run):
        execute_plan(load_plan(plan_path), PROFILES_DIR,
                     ssh_factory=lambda h, u, p: ssh, setup_factory=None,
                     engine_factory=MagicMock())
    return seen


def test_execute_plan_applies_quick_verify_profile():
    seen = _profiles_seen_by_execute_plan(QUICK)
    assert set(seen) == set(_cases(QUICK))
    prof = seen["multi_4ch_fhd"]
    cmds = [i["command"] for i in prof["checks"]["custom_commands"]]
    assert len(cmds) == 33 - 4 * 3      # BITRATE·duration·파일 무결성 × 4ch 제거
    assert not any("ffprobe" in c for c in cmds)
    assert prof["checks"]["frame_flow"]["enabled"] is True


def test_execute_plan_leaves_comprehensive_profile_alone():
    seen = _profiles_seen_by_execute_plan(COMPREHENSIVE)
    prof = seen["multi_4ch_fhd"]
    assert prof["checks"]["custom_commands"] == _items("multi_4ch_fhd")
    assert prof["checks"]["frame_flow"]["enabled"] is False


def test_viewer_checklist_matches_quick_verify_profile(tmp_path, monkeypatch):
    """run_start 의 케이스 체크리스트도 같은 profile 규칙을 따라야 한다 — 아니면
    뷰어에 빠진 finalize 항목이 영원히 '대기'로 남는다."""
    events_dir = str(tmp_path / "events")
    monkeypatch.setattr(run_stream, "default_events_dir", lambda: events_dir)
    monkeypatch.setattr(plan_mod, "load_baseline", lambda ref, root: (None, None))
    monkeypatch.setattr(plan_mod, "evaluate_gate", lambda *a, **k: MagicMock(
        verdict="PASS", pass_rate=1.0, regressions=[], fixed=[], new_cases=[], known_warns=[]))
    monkeypatch.setattr(plan_mod, "render_reports", lambda *a, **k: [])
    monkeypatch.setattr(plan_mod, "execute_plan", lambda *a, **k: [])
    args = argparse.Namespace(plan="quick_verify", host="192.168.0.5", user=None,
                              password=None, duration=None, quiet=True, until_pass=False)
    pim_check._run_plan(args)
    with open(os.path.join(events_dir, "current.jsonl"), encoding="utf-8") as f:
        run_start = next(r for r in map(json.loads, f) if r["event_type"] == "run_start")
    names = [c["name"] for c in run_start["case_plans"]["multi_4ch_fhd"]["checklist"]]
    expected = [i["name"] for i in _items("multi_4ch_fhd") if not needs_finalized_recording(i)]
    assert names == expected
