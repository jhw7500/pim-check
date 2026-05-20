"""
tests/test_results_json_golden_snapshot.py — *_results.json 골든 스냅샷 회귀 가드.

목적 (Sub-AC 1)
===============
JSONL event stream emitter 가 **비활성/부재**일 때, ``*_results.json`` writer 가
고정된 test-plan 입력에 대해 **바이트 단위로 동일한(byte-identical)** 산출물을
내는지 검증하는 골든 스냅샷(golden master) 회귀 테스트.

기존 schema-freeze 테스트(``test_results_json_backcompat.py``)는 키 집합/값을
동결하지만, 직렬화 형식(indent, 키 순서, 후행 개행, 공백)까지 바이트 단위로
고정하지는 않는다. 본 테스트는 한 발 더 나아가 writer 가 디스크에 쓴 **원시
바이트**를 커밋된 골든 파일과 대조한다. emit hook 도입(형제 AC)이 결과 dict 에
키를 추가/제거하거나 직렬화 옵션을 바꾸면 바이트가 달라지므로 즉시 실패한다.

대상 writer
-----------
1. ``plan.render_reports`` 의 ``format: json`` 경로(``plan._render_json``)
   — plan 주도 ``*_results.json`` (``json.dump(payload, f, indent=2,
   ensure_ascii=False)``). 고정 plan/executions/gate/host/timestamp 가 주어지면
   완전히 결정론적이다.
2. ``run_*`` 스크립트가 루트에 쓰는 ``<name>_results.json`` 직렬화 형식
   (``json.dumps(results, indent=2, ensure_ascii=False)`` — 시나리오 dict 리스트).
   레포 루트의 실제 산출물(예: ``comprehensive_results.json``)과 동일한 형식.

emitter 부재 입증
-----------------
``_render_json`` 과 루트 리스트 직렬화 경로는 emitter 를 인자로 받지도,
호출하지도 않는다. 따라서 emitter 모듈이 import 가능한 상태(=설치되어 있으나
writer 경로에서 비활성)에서도 writer 산출물은 골든 바이트와 동일해야 한다.
``test_emitter_import_does_not_perturb_writer`` 가 이를 명시적으로 확인한다.

골든 파일 재생성
----------------
골든 파일은 현재(=baseline) writer 코드가 만든 바이트를 동결한 것이다. writer
형식을 의도적으로 바꿀 때만 아래로 재생성한다::

    python tests/test_results_json_golden_snapshot.py --regenerate
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

from plan import (
    CaseExecution,
    DEFAULT_EXECUTION,
    DEFAULT_GATE,
    GateResult,
    Plan,
    render_reports,
)

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")
GOLDEN_PLAN_JSON = os.path.join(GOLDEN_DIR, "comprehensive_results.golden.json")
GOLDEN_ROOT_JSON = os.path.join(GOLDEN_DIR, "root_scenario_results.golden.json")

# ── 고정 test-plan 입력 (완전 결정론적) ───────────────────────────────
FIXED_HOST = "192.168.0.5"
FIXED_TIMESTAMP = "20260520_120000"


def _fixed_plan() -> Plan:
    return Plan(
        name="comprehensive",
        description="full regression",
        version=1,
        cases={"regression": ["fault_gstapp_crash", "board_hw_check"]},
        execution=dict(DEFAULT_EXECUTION),
        gate=dict(DEFAULT_GATE),
        reports=[{"format": "json", "path": "out/{plan_name}/{timestamp}.json"}],
    )


def _fixed_executions() -> list:
    return [
        CaseExecution(
            section="regression",
            case_name="fault_gstapp_crash",
            results=[
                {
                    "name": "process",
                    "passed": False,
                    "reason": "gstApp is not running",
                    "data": {},
                    "duration_ms": 50,
                }
            ],
            passed=False,
            retries_used=1,
            error=None,
            duration_sec=64.0,
        ),
        CaseExecution(
            section="regression",
            case_name="board_hw_check",
            results=[
                {
                    "name": "thermal",
                    "passed": True,
                    "reason": "OK",
                    "data": {"max_temp": 72.0},
                    "duration_ms": 30,
                }
            ],
            passed=True,
            retries_used=0,
            error=None,
            duration_sec=12.5,
        ),
    ]


def _fixed_gate() -> GateResult:
    return GateResult(
        verdict="FAIL",
        pass_rate=0.5,
        regressions=["fault_gstapp_crash"],
        fixed=[],
        new_cases=[],
        known_warns=[],
    )


def _render_plan_json_bytes(base_path: str) -> tuple:
    """고정 입력으로 plan 주도 *_results.json 을 쓰고 (경로, 바이트)를 반환."""
    written = render_reports(
        _fixed_plan(),
        _fixed_executions(),
        _fixed_gate(),
        host=FIXED_HOST,
        base_path=base_path,
        timestamp=FIXED_TIMESTAMP,
    )
    assert len(written) == 1, written
    with open(written[0], "rb") as f:
        return written[0], f.read()


def _fixed_scenario_results() -> list:
    """run_* 스크립트가 루트 <name>_results.json 에 쓰는 시나리오 dict 리스트."""
    return [
        {
            "name": "p2_quad_720p_ch0_vflip",
            "result": "PASS",
            "expected_hex": "0x000x02",
            "actual": "0x000x02",
            "retries_used": 0,
            "elapsed": 64.0,
        },
        {
            "name": "p2_quad_720p_ch0_hflip",
            "result": "FAIL",
            "expected_hex": "0x000x01",
            "actual": "(no response)",
            "retries_used": 3,
            "elapsed": 270.5,
        },
    ]


def _render_root_json_bytes() -> bytes:
    """루트 <name>_results.json 직렬화 바이트 (json.dumps indent=2)."""
    return json.dumps(_fixed_scenario_results(), indent=2, ensure_ascii=False).encode("utf-8")


def _regenerate_golden() -> None:
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        _, plan_bytes = _render_plan_json_bytes(tmp)
    with open(GOLDEN_PLAN_JSON, "wb") as f:
        f.write(plan_bytes)
    with open(GOLDEN_ROOT_JSON, "wb") as f:
        f.write(_render_root_json_bytes())
    print(f"regenerated: {GOLDEN_PLAN_JSON}")
    print(f"regenerated: {GOLDEN_ROOT_JSON}")


# ── 골든 스냅샷 회귀 테스트 ───────────────────────────────────────────


class TestPlanResultsJsonGoldenSnapshot(unittest.TestCase):
    """plan 주도 *_results.json 산출물이 골든 바이트와 byte-identical 한지 검증."""

    def test_golden_fixture_exists(self):
        self.assertTrue(
            os.path.exists(GOLDEN_PLAN_JSON),
            "골든 파일 부재 — 'python tests/test_results_json_golden_snapshot.py "
            "--regenerate' 로 baseline 을 동결하라",
        )

    def test_plan_json_byte_identical_to_golden(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, produced = _render_plan_json_bytes(tmp)
        with open(GOLDEN_PLAN_JSON, "rb") as f:
            golden = f.read()
        self.assertEqual(
            produced,
            golden,
            "plan 주도 *_results.json 바이트 불일치 — emitter 도입이 baseline "
            "writer 출력을 변경했다 (하위호환 회귀)",
        )

    def test_plan_json_deterministic_across_runs(self):
        """동일 고정 입력 → 두 번의 렌더가 서로 byte-identical (비결정성 부재)."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, first = _render_plan_json_bytes(a)
            _, second = _render_plan_json_bytes(b)
        self.assertEqual(first, second)

    def test_emitter_import_does_not_perturb_writer(self):
        """emitter 모듈이 import 가능(=비활성 존재)해도 writer 바이트는 골든과 동일.

        emitter 가 'disabled/absent' 인 writer 경로의 핵심 계약: writer 는
        emitter 를 호출하지 않으므로, emitter 코드의 존재 자체가 산출물에 영향을
        주어선 안 된다.
        """
        import event_stream  # noqa: F401  — 부수효과(emit) 없이 import 만 한다

        with tempfile.TemporaryDirectory() as tmp:
            _, produced = _render_plan_json_bytes(tmp)
        with open(GOLDEN_PLAN_JSON, "rb") as f:
            golden = f.read()
        self.assertEqual(produced, golden)


class TestRootResultsJsonGoldenSnapshot(unittest.TestCase):
    """루트 <name>_results.json 직렬화 형식이 골든 바이트와 byte-identical 한지 검증."""

    def test_golden_fixture_exists(self):
        self.assertTrue(
            os.path.exists(GOLDEN_ROOT_JSON),
            "골든 파일 부재 — --regenerate 로 baseline 을 동결하라",
        )

    def test_root_list_json_byte_identical_to_golden(self):
        produced = _render_root_json_bytes()
        with open(GOLDEN_ROOT_JSON, "rb") as f:
            golden = f.read()
        self.assertEqual(
            produced,
            golden,
            "루트 <name>_results.json 바이트 불일치 — 직렬화 형식이 변경됨",
        )

    def test_root_json_indent2_format_preserved(self):
        """골든 파일이 indent=2 리스트 형식을 유지하고 round-trip 가능."""
        with open(GOLDEN_ROOT_JSON, "rb") as f:
            golden = f.read()
        parsed = json.loads(golden.decode("utf-8"))
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed, _fixed_scenario_results())
        self.assertIn(b"\n  {", golden)  # indent=2 들여쓰기 유지


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        _regenerate_golden()
    else:
        unittest.main()
