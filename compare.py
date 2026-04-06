"""
compare.py — 테스트 실행 결과 비교

두 실행 간 체크별 PASS/FAIL 변화를 보여준다.
"""
from __future__ import annotations

from history import read_history


def compare_runs(
    history_dir: str = "reports",
    case_filter: str | None = None,
    run_a: int = -2,
    run_b: int = -1,
) -> dict:
    """히스토리에서 두 실행을 비교한다.

    Args:
        run_a: 비교 기준 (음수 인덱스, -2 = 이전 실행)
        run_b: 비교 대상 (음수 인덱스, -1 = 최근 실행)

    Returns:
        {"improved": [...], "regressed": [...], "unchanged": [...], "summary": str}
    """
    entries = read_history(history_dir, case_filter)
    if len(entries) < 2:
        return {
            "improved": [],
            "regressed": [],
            "unchanged": [],
            "summary": "Not enough runs to compare (need at least 2)",
        }

    try:
        a = entries[run_a]
        b = entries[run_b]
    except IndexError:
        return {
            "improved": [],
            "regressed": [],
            "unchanged": [],
            "summary": f"Invalid run indices: {run_a}, {run_b}",
        }

    checks_a = a.get("checks", {})
    checks_b = b.get("checks", {})
    all_checks = sorted(set(checks_a.keys()) | set(checks_b.keys()))

    improved = []
    regressed = []
    unchanged = []

    for check in all_checks:
        va = checks_a.get(check)
        vb = checks_b.get(check)

        if va is None:
            # 새로 추가된 체크
            if vb:
                improved.append({"check": check, "change": "NEW → PASS"})
            else:
                regressed.append({"check": check, "change": "NEW → FAIL"})
        elif vb is None:
            unchanged.append({"check": check, "change": "REMOVED"})
        elif va and not vb:
            regressed.append({"check": check, "change": "PASS → FAIL"})
        elif not va and vb:
            improved.append({"check": check, "change": "FAIL → PASS"})
        else:
            status = "PASS" if vb else "FAIL"
            unchanged.append({"check": check, "change": status})

    ts_a = a.get("timestamp", "?")[:19]
    ts_b = b.get("timestamp", "?")[:19]
    case = a.get("case") or b.get("case") or "healthcheck"
    summary = f"Comparing {case}: {ts_a} vs {ts_b} — "
    summary += f"{len(improved)} improved, {len(regressed)} regressed, {len(unchanged)} unchanged"

    return {
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "summary": summary,
    }


def format_comparison(result: dict) -> str:
    """비교 결과를 터미널 문자열로 포매팅."""
    from color import green, red, dim

    lines = [result["summary"], ""]

    if result["regressed"]:
        lines.append(red("REGRESSED:"))
        for r in result["regressed"]:
            lines.append(red(f"  {r['check']}: {r['change']}"))
        lines.append("")

    if result["improved"]:
        lines.append(green("IMPROVED:"))
        for r in result["improved"]:
            lines.append(green(f"  {r['check']}: {r['change']}"))
        lines.append("")

    if result["unchanged"]:
        lines.append(dim("UNCHANGED:"))
        for r in result["unchanged"]:
            lines.append(dim(f"  {r['check']}: {r['change']}"))

    return "\n".join(lines)
