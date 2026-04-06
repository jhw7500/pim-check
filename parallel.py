"""
parallel.py — 다수 타겟 병렬 테스트 실행

ThreadPoolExecutor로 여러 타겟에 동시에 케이스를 실행하고
타겟별 결과를 집계한다.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from config import load_profile, deep_merge
from engine import Engine
from reporter import Reporter
from setup import SetupManager
from ssh import SshClient


PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


def load_targets(targets_path: str) -> list[dict]:
    """profiles/targets.yaml에서 타겟 목록을 로드한다."""
    if not os.path.exists(targets_path):
        return []
    with open(targets_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("targets", [])


def run_on_target(
    host: str,
    user: str,
    password: str,
    case_name: str | None,
    duration: int | None,
    overrides: dict | None = None,
) -> dict:
    """단일 타겟에서 케이스를 실행하고 결과를 반환한다."""
    profile = load_profile(PROFILES_DIR, case=case_name)

    # 타겟별 overrides 적용
    if overrides:
        checks = profile.get("checks", {})
        profile["checks"] = deep_merge(checks, overrides)

    if host:
        profile["target"]["host"] = host
    if user:
        profile["target"]["user"] = user
    if password:
        profile["target"]["password"] = password
    if duration is not None:
        profile["monitor"]["duration_sec"] = duration

    h = profile["target"].get("host", "192.168.0.5")
    u = profile["target"].get("user", "root")
    p = profile["target"].get("password", "root")
    effective_duration = profile["monitor"].get("duration_sec", 0)

    ssh = SshClient(h, u, p)

    if not ssh.check_connectivity():
        return {
            "host": h,
            "case": case_name,
            "status": "UNREACHABLE",
            "results": [],
            "collected": 0,
            "total": 0,
        }

    # Preflight
    ssh.preflight_check()

    # Setup
    setup_config = profile.get("setup")
    setup_mgr = SetupManager(ssh)
    setup_changed = False

    try:
        if setup_config:
            try:
                setup_changed = setup_mgr.run_setup(setup_config)
            except TimeoutError:
                return {
                    "host": h,
                    "case": case_name,
                    "status": "SETUP_FAILED",
                    "results": [],
                    "collected": 0,
                    "total": 0,
                }

        engine = Engine(ssh, profile)
        if effective_duration <= 0:
            results = engine.run_snapshot()
            collected, total_samples = 1, 1
        else:
            results, collected, total_samples = engine.run_monitor()

        known_issues = profile.get("known_issues")
        if known_issues:
            for r in results:
                if not r["passed"]:
                    for ki in known_issues:
                        if r["name"] == ki["check"] and ki["reason_contains"] in r.get("reason", ""):
                            r["known_issue"] = ki["label"]

        real_fails = [r for r in results if not r["passed"] and "known_issue" not in r]
        if not real_fails:
            status = "PASS" if not any("known_issue" in r for r in results) else "WARN"
        else:
            status = "FAIL"

        return {
            "host": h,
            "case": case_name,
            "status": status,
            "results": results,
            "collected": collected,
            "total": total_samples,
        }
    finally:
        if setup_config and setup_changed:
            try:
                setup_mgr.run_teardown(setup_config)
            except TimeoutError:
                pass


def run_parallel(
    hosts: list[str],
    case_name: str | None,
    user: str = "root",
    password: str = "root",
    duration: int | None = None,
    max_workers: int = 4,
    target_overrides: dict | None = None,
) -> list[dict]:
    """여러 타겟에서 동시에 케이스를 실행한다.

    Args:
        target_overrides: {host: {check_overrides}} 형태의 타겟별 override
    """
    results = []
    overrides_map = target_overrides or {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_on_target, host, user, password, case_name,
                            duration, overrides_map.get(host)): host
            for host in hosts
        }
        for future in as_completed(futures):
            host = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    "host": host,
                    "case": case_name,
                    "status": f"ERROR: {e}",
                    "results": [],
                    "collected": 0,
                    "total": 0,
                }
            results.append(result)

    return results


def format_parallel_results(results: list[dict]) -> str:
    """병렬 실행 결과를 요약 문자열로 반환한다."""
    lines = ["=== Parallel Test Results ===", ""]

    for r in results:
        host = r["host"]
        status = r["status"]
        case = r.get("case") or "healthcheck"

        if status == "UNREACHABLE":
            lines.append(f"[X] {host} ({case}): UNREACHABLE")
        elif status == "SETUP_FAILED":
            lines.append(f"[X] {host} ({case}): SETUP FAILED")
        elif status.startswith("ERROR"):
            lines.append(f"[X] {host} ({case}): {status}")
        else:
            checks = r.get("results", [])
            passed = sum(1 for c in checks if c["passed"])
            total = len(checks)
            marker = "[+]" if status in ("PASS", "WARN") else "[X]"
            lines.append(f"{marker} {host} ({case}): {status} ({passed}/{total})")

    lines.append("")
    total_hosts = len(results)
    ok = sum(1 for r in results if r["status"] in ("PASS", "WARN"))
    lines.append(f"Summary: {ok}/{total_hosts} targets OK")
    return "\n".join(lines)
