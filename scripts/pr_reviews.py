#!/usr/bin/env python3
"""
scripts/pr_reviews.py — PR 자동리뷰 3종(Claude·Gemini·Codex) 집계 + 머지 게이트.

왜 필요한가
-----------
세 리뷰어가 서로 다른 GitHub API 경로에 결과를 남긴다:

  - Claude : issues/{pr}/comments   (github-actions[bot] + automation 마커)
  - Gemini : issues/{pr}/comments   (github-actions[bot] + automation 마커)
  - Codex  : 지적이 있으면 pulls/{pr}/comments(인라인) + pulls/{pr}/reviews
             지적이 없으면 issues/{pr}/comments

한 경로만 조회하면 리뷰 하나를 통째로 놓친다. `statusCheckRollup` 은 체크의
pass/fail 만 보여주므로 지적 내용을 알려주지 않는다. 이 스크립트는 세 경로를
한 번에 모아 하나의 표로 제시하고, 객관적으로 판정 가능한 조건만 게이트로 건다.

무엇을 판정하는가 (객관 신호만)
-------------------------------
  MISSING   리뷰어가 아무 것도 남기지 않음 (워크플로 미실행/실패)
  FAILED    automation-state.attempt_status != success
  STALE     리뷰한 커밋 != PR HEAD → 지금 머지될 코드는 그 리뷰어가 못 봤다
  FINDINGS  지적이 있고, 그 이후 신뢰할 수 있는 구성원의 처분 코멘트가 없음
            (PR 메인 대화 또는 인라인 스레드 답글)

무엇을 판정하지 '않는가'
------------------------
  - GitHub 리뷰 스레드의 resolve 상태는 이 저장소에서 신호가 되지 못한다.
    (실측 2026-08-24: 머지된 PR #97~#103 의 codex 스레드가 전부
     isResolved=false. #103 은 지적이 반영됐는데도 isOutdated=false)
    따라서 "해결됨" 을 자동 추정하지 않는다.
  - Claude/Gemini 의 지적 심각도는 산문이라 파싱하지 않는다. 본문에 명시적
    무지적 문구("차단 이슈 없음" / "No blocking issues found")가 있으면
    '자기신고 무지적' 으로만 표시하고, 판단은 사람에게 넘긴다.

STALE 이 왜 중요한가 (실측)
---------------------------
2026-08-24 기준 최근 8개 PR(#97~#104)에서 Claude·Gemini 는 push 마다 재리뷰해
8/8 이 HEAD 기준이었으나, Codex 는 **7/8 이 옛 커밋 기준**이었다. Codex 는 PR
open 시점에 한 번 리뷰하고 이후 push 에는 재리뷰하지 않기 때문이다. 결과적으로
Codex 가 지적한 P1/P2 를 고친 `(#N 자동리뷰)` 커밋을 정작 Codex 는 한 번도 다시
보지 않은 채 머지됐다. 재리뷰는 PR 에 `@codex review` 를 코멘트하면 트리거된다.

사용법
------
  python3 scripts/pr_reviews.py 103            # 집계 표 출력
  python3 scripts/pr_reviews.py 103 --full     # 리뷰 본문 전체 포함
  python3 scripts/pr_reviews.py 103 --gate     # 게이트 판정 (exit code 로 차단)
  python3 scripts/pr_reviews.py 103 --json     # 기계 판독 출력

Exit code:
  0 = 통과 (--gate 없이 실행한 경우도 0)
  1 = 차단 (--gate 에서 MISSING/FAILED/STALE/미처분 FINDINGS 발견)
  3 = 입력·환경 에러 (gh 미설치·미인증, PR 없음 등)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional

# --- 리뷰어 식별 -----------------------------------------------------------

CLAUDE = "claude"
GEMINI = "gemini"
CODEX = "codex"
REVIEWERS = (CLAUDE, GEMINI, CODEX)

# automation 마커 → reviewer id. state 메타데이터는 마커와 일치할 때만 보조 정보로 쓴다.
MARKER_TO_REVIEWER = {
    "claude-code-review": CLAUDE,
    "gemini-auto-review": GEMINI,
    "gemini-review": GEMINI,
}

ACTIONS_BOT_LOGIN = "github-actions[bot]"
CODEX_BOT_LOGIN = "chatgpt-codex-connector[bot]"

# --- 본문 파싱 정규식 ------------------------------------------------------

MARKER_RE = re.compile(r"<!--\s*automation:([a-z0-9-]+):v\d+\s*-->")
STATE_RE = re.compile(r"<!--\s*automation-state:(\{.*?\})\s*-->", re.DOTALL)
CODEX_COMMIT_RE = re.compile(r"Reviewed commit:\*\*\s*`([0-9a-f]{7,40})`")
CODEX_BADGE_RE = re.compile(r"badge/(P\d)-")
CODEX_TITLE_RE = re.compile(r"</sub></sub>\s*(.+?)\*\*")
CODEX_NO_FINDINGS = "Didn't find any major issues"
TRUSTED_HUMAN_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

# Claude/Gemini 가 스스로 "지적 없음" 을 선언하는 문구. 판정 근거가 아니라
# 표시용 힌트다 — 산문이라 형태가 바뀔 수 있다.
CLEAR_PHRASES = (
    "차단 이슈 없음",
    "No blocking issues found",
    "블로킹 이슈 없음",
)


class GhError(RuntimeError):
    """gh CLI 호출 실패."""


# --- gh 호출 ---------------------------------------------------------------


def gh_json(args: List[str]) -> Any:
    """`gh` 를 호출하고 stdout 을 JSON 으로 파싱한다."""
    try:
        proc = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:  # gh 미설치
        raise GhError("gh CLI 를 찾을 수 없다. https://cli.github.com 설치 필요") from exc
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args)} 실패 (exit {proc.returncode}): {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise GhError(f"gh 출력이 JSON 이 아니다: {proc.stdout[:200]!r}") from exc


def detect_repo() -> str:
    """현재 디렉터리의 GitHub 저장소를 OWNER/NAME 으로 반환."""
    data = gh_json(["repo", "view", "--json", "nameWithOwner"])
    name = (data or {}).get("nameWithOwner")
    if not name:
        raise GhError("현재 디렉터리에서 GitHub 저장소를 찾지 못했다 (--repo 로 지정)")
    return str(name)


def fetch_payloads(repo: str, pr: int, gh: Callable[[List[str]], Any] = gh_json) -> Dict[str, Any]:
    """PR 메타 + 세 경로의 코멘트를 모두 가져온다."""
    # gh 2.96.0 실측: --paginate 는 여러 배열 페이지를 단일 배열로 병합한다.
    # --slurp 를 더하면 배열의 배열이 되어 아래 collect 계약과 맞지 않는다.
    return {
        "pr": gh(["api", f"repos/{repo}/pulls/{pr}"]),
        "issue_comments": gh(["api", f"repos/{repo}/issues/{pr}/comments", "--paginate"]),
        "review_comments": gh(["api", f"repos/{repo}/pulls/{pr}/comments", "--paginate"]),
        "reviews": gh(["api", f"repos/{repo}/pulls/{pr}/reviews", "--paginate"]),
    }


# --- 순수 파싱 함수 (네트워크 무관, 테스트 대상) ---------------------------


def parse_automation_state(body: str) -> Optional[Dict[str, Any]]:
    """`<!-- automation-state:{...} -->` 마커의 JSON 을 파싱한다."""
    m = STATE_RE.search(body or "")
    if not m:
        return None
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return state if isinstance(state, dict) else None


def identify_reviewer(body: str) -> Optional[str]:
    """automation 마커가 있는 코멘트의 리뷰어 id 를 판정한다."""
    m = MARKER_RE.search(body or "")
    if not m:
        return None
    reviewer = MARKER_TO_REVIEWER.get(m.group(1))
    if not reviewer:
        return None
    state = parse_automation_state(body)
    if state and state.get("reviewer") not in (None, reviewer):
        return None
    return reviewer


def is_codex(user: Dict[str, Any]) -> bool:
    """Codex REST 산출물의 정확한 봇 신원인가."""
    return (user or {}).get("login") == CODEX_BOT_LOGIN and (user or {}).get("type") == "Bot"


def parse_codex_commit(body: str) -> Optional[str]:
    """Codex 본문의 `**Reviewed commit:** \\`sha\\`` 를 추출한다 (축약 sha)."""
    m = CODEX_COMMIT_RE.search(body or "")
    return m.group(1) if m else None


def parse_codex_finding(comment: Dict[str, Any]) -> Dict[str, Any]:
    """Codex 인라인 코멘트 1건을 지적 레코드로 변환한다."""
    body = comment.get("body") or ""
    badge = CODEX_BADGE_RE.search(body)
    title = CODEX_TITLE_RE.search(body)
    if title:
        text = title.group(1).strip()
    else:  # 배지 없는 형태 — 첫 비어있지 않은 줄로 폴백
        text = next((ln.strip().strip("*") for ln in body.splitlines() if ln.strip()), "")
    return {
        "severity": badge.group(1) if badge else "?",
        "title": text,
        "path": comment.get("path"),
        "line": comment.get("line"),
        "url": comment.get("html_url"),
        "created_at": comment.get("created_at"),
    }


def commits_match(reviewed: Optional[str], head: Optional[str]) -> bool:
    """리뷰 대상 커밋과 HEAD 가 같은가. Codex 는 축약 sha 라 접두 비교한다."""
    if not reviewed or not head:
        return False
    short, long_ = sorted((reviewed, head), key=len)
    return long_.startswith(short)


def has_clear_phrase(body: str) -> bool:
    """Claude/Gemini 가 명시적으로 무지적을 선언했는가 (자기신고)."""
    return any(p in (body or "") for p in CLEAR_PHRASES)


def _is_bot(user: Dict[str, Any]) -> bool:
    return (user or {}).get("type") == "Bot" or (user or {}).get("login", "").endswith("[bot]")


def _is_actions_bot(user: Dict[str, Any]) -> bool:
    """Claude/Gemini 산출물을 남기는 GitHub Actions 봇의 REST 신원인가."""
    return (user or {}).get("login") == ACTIONS_BOT_LOGIN and (user or {}).get("type") == "Bot"


def _is_trusted_human_comment(comment: Dict[str, Any]) -> bool:
    """지적을 처분할 권한이 있는 사람의 코멘트인가."""
    return not _is_bot(comment.get("user") or {}) and comment.get("author_association") in TRUSTED_HUMAN_ASSOCIATIONS


# --- 집계 ------------------------------------------------------------------


def collect(payloads: Dict[str, Any]) -> Dict[str, Any]:
    """세 경로의 payload 를 리뷰어별 상태로 정리한다."""
    pr = payloads.get("pr") or {}
    head = ((pr.get("head") or {}).get("sha")) or ""
    issue_comments = payloads.get("issue_comments") or []
    review_comments = payloads.get("review_comments") or []
    reviews = payloads.get("reviews") or []

    entries: Dict[str, Dict[str, Any]] = {}

    # 1) Claude / Gemini — issue 코멘트의 automation 마커. 같은 리뷰어가 여러 번
    #    남기면 마지막(가장 최신)을 채택한다.
    for c in issue_comments:
        if not _is_actions_bot(c.get("user") or {}):
            continue
        body = c.get("body") or ""
        who = identify_reviewer(body)
        if not who:
            continue
        state = parse_automation_state(body) or {}
        candidate = {
            "reviewer": who,
            "source": "issues/comments",
            "reviewed_commit": state.get("attempt_head") or state.get("successful_head"),
            "run_status": state.get("attempt_status"),
            "run_id": state.get("run_id"),
            # Sticky comment 갱신은 새 리뷰 본문이므로 이전 처분보다 나중이면
            # 다시 사람의 확인을 요구한다. created_at 만 쓰면 새 본문을 놓친다.
            "created_at": c.get("updated_at") or c.get("created_at"),
            "url": c.get("html_url"),
            "body": body,
            "self_reported_clear": has_clear_phrase(body),
            "findings": [],
        }
        current = entries.get(who)
        if current is None or (candidate["created_at"] or "") >= (current["created_at"] or ""):
            entries[who] = candidate

    # 2) Codex — 지적이 없으면 issue 코멘트, 있으면 review + 인라인 코멘트.
    codex_candidates: List[Dict[str, Any]] = []
    for c in issue_comments:
        if is_codex(c.get("user") or {}):
            body = c.get("body") or ""
            codex_candidates.append(
                {
                    "reviewer": CODEX,
                    "source": "issues/comments",
                    "reviewed_commit": parse_codex_commit(body),
                    "run_status": None,
                    "run_id": None,
                    "created_at": c.get("created_at"),
                    "url": c.get("html_url"),
                    "body": body,
                    "self_reported_clear": CODEX_NO_FINDINGS in body,
                    "findings": [],
                    "_review_id": None,
                }
            )
    for r in reviews:
        if is_codex(r.get("user") or {}):
            body = r.get("body") or ""
            codex_candidates.append(
                {
                    "reviewer": CODEX,
                    "source": "pulls/reviews",
                    "reviewed_commit": parse_codex_commit(body) or r.get("commit_id"),
                    "run_status": None,
                    "run_id": None,
                    "created_at": r.get("submitted_at"),
                    "url": r.get("html_url"),
                    "body": body,
                    "self_reported_clear": CODEX_NO_FINDINGS in body,
                    "findings": [],
                    "_review_id": r.get("id"),
                }
            )
    if codex_candidates:
        # Codex 는 한 실행의 결과를 issue comment 또는 pull request review 중
        # 한 경로에 남긴다. 경로 우선순위가 아니라 생성 시각으로 최신 실행을 고른다.
        # 같은 시각이면 findings 를 담는 review 쪽을 더 구체적인 산출물로 본다.
        codex_entry = max(
            codex_candidates,
            key=lambda e: (
                e.get("created_at") or "",
                e.get("source") == "pulls/reviews",
            ),
        )
        review_id = codex_entry.pop("_review_id")
        if codex_entry["source"] == "pulls/reviews":
            codex_entry["findings"] = [
                parse_codex_finding(c)
                for c in review_comments
                if is_codex(c.get("user") or {})
                and not c.get("in_reply_to_id")
                and (review_id is None or c.get("pull_request_review_id") == review_id)
            ]
        entries[CODEX] = codex_entry

    # 3) 처분 코멘트 — PR 메인 대화 또는 인라인 스레드에 저장소의
    #    신뢰할 수 있는 사람이 남긴 최신 코멘트 시각. 다른 review comment는
    #    새로운 지적일 수 있으므로 답글(in_reply_to_id)만 처분으로 본다.
    disposition_comments = list(issue_comments)
    disposition_comments += [c for c in review_comments if c.get("in_reply_to_id")]
    human_times = [
        c.get("created_at") for c in disposition_comments if _is_trusted_human_comment(c) and c.get("created_at")
    ]
    latest_human = max(human_times) if human_times else None

    # 봇이 남긴 리뷰 산출물 중 가장 최신 시각 (처분이 그 이후여야 유효).
    # Claude/Gemini sticky comment의 updated_at도 포함해 갱신된 본문을 예전 처분으로
    # 통과시키지 않는다(fail closed).
    bot_times = [e["created_at"] for e in entries.values() if e.get("created_at")]
    bot_times += [f["created_at"] for e in entries.values() for f in e["findings"] if f.get("created_at")]
    latest_bot = max(bot_times) if bot_times else None

    for entry in entries.values():
        entry["fresh"] = commits_match(entry.get("reviewed_commit"), head)

    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "head": head,
        "state": pr.get("state"),
        "entries": entries,
        "latest_human_comment": latest_human,
        "latest_bot_artifact": latest_bot,
        "disposed": bool(latest_human and latest_bot and latest_human > latest_bot),
    }


# --- 게이트 ----------------------------------------------------------------


def _run_failed(who: str, entry: Dict[str, Any]) -> bool:
    """자동화 리뷰는 명시적 success 만 성공이다. Codex 는 상태 필드가 없다."""
    status = entry.get("run_status")
    if who in (CLAUDE, GEMINI):
        return status != "success"
    return status not in (None, "success")


def evaluate(summary: Dict[str, Any]) -> List[Dict[str, str]]:
    """게이트 위반 목록을 반환한다. 빈 리스트면 통과."""
    violations: List[Dict[str, str]] = []
    entries = summary.get("entries") or {}
    head = summary.get("head") or ""

    for who in REVIEWERS:
        entry = entries.get(who)
        if entry is None:
            violations.append(
                {
                    "reviewer": who,
                    "kind": "MISSING",
                    "detail": "리뷰 산출물이 없다 — 워크플로가 실행되지 않았거나 실패했다",
                    "remedy": _remedy(who),
                }
            )
            continue
        if _run_failed(who, entry):
            violations.append(
                {
                    "reviewer": who,
                    "kind": "FAILED",
                    "detail": f"리뷰 실행 상태가 {entry['run_status']} 다",
                    "remedy": _remedy(who),
                }
            )
        if not entry.get("fresh"):
            reviewed = entry.get("reviewed_commit") or "(불명)"
            violations.append(
                {
                    "reviewer": who,
                    "kind": "STALE",
                    "detail": f"리뷰 대상 {reviewed[:10]} != HEAD {head[:10]} — 지금 머지될 코드를 못 봤다",
                    "remedy": _remedy(who),
                }
            )

    findings = [(who, f) for who, e in entries.items() for f in e.get("findings") or []]
    if findings and not summary.get("disposed"):
        for who, f in findings:
            violations.append(
                {
                    "reviewer": who,
                    "kind": "FINDINGS",
                    "detail": f"{f['severity']} {f['title']} ({f.get('path')}:{f.get('line')})",
                    "remedy": "지적을 반영하거나, 신뢰할 수 있는 구성원이 PR 에 처분 근거를 기록한다",
                }
            )
    return violations


def _remedy(who: str) -> str:
    if who == CODEX:
        return "PR 에 `@codex review` 코멘트로 재리뷰 트리거"
    return "빈 커밋 push 또는 워크플로 재실행으로 재리뷰 트리거"


# --- 출력 ------------------------------------------------------------------


def render(summary: Dict[str, Any], violations: List[Dict[str, str]], full: bool = False) -> str:
    """사람이 읽는 집계 표를 만든다."""
    out: List[str] = []
    head = summary.get("head") or ""
    out.append(f"PR #{summary.get('number')} — {summary.get('title')}")
    out.append(f"HEAD {head[:10]}  state={summary.get('state')}")
    out.append("")
    out.append(f"{'리뷰어':<8} {'상태':<15} {'리뷰 커밋':<12} {'지적':<5} 출처")
    out.append("-" * 67)

    entries = summary.get("entries") or {}
    for who in REVIEWERS:
        entry = entries.get(who)
        if entry is None:
            out.append(f"{who:<8} {'MISSING':<15} {'-':<12} {'-':<5} -")
            continue
        if _run_failed(who, entry):
            status = "FAILED"
        elif not entry.get("fresh") and entry.get("findings"):
            status = "STALE/FINDINGS"
        elif not entry.get("fresh"):
            status = "STALE"
        elif entry.get("findings"):
            status = "FINDINGS"
        elif entry.get("self_reported_clear"):
            status = "OK(무지적)"
        else:
            status = "OK"
        reviewed = (entry.get("reviewed_commit") or "-")[:10]
        n = len(entry.get("findings") or [])
        out.append(f"{who:<8} {status:<15} {reviewed:<12} {n:<5} {entry.get('source')}")

    all_findings = [(who, f) for who, e in entries.items() for f in e.get("findings") or []]
    if all_findings:
        out.append("")
        out.append("지적:")
        for who, f in all_findings:
            out.append(f"  [{f['severity']}] {who} — {f['title']}")
            out.append(f"        {f.get('path')}:{f.get('line')}  {f.get('url')}")

    out.append("")
    if summary.get("disposed"):
        out.append(f"처분 코멘트: 있음 ({summary.get('latest_human_comment')})")
    elif all_findings:
        out.append("처분 코멘트: 없음 — 지적 이후 신뢰할 수 있는 구성원의 판단 기록이 없다")

    if full:
        for who in REVIEWERS:
            entry = entries.get(who)
            if not entry:
                continue
            out.append("")
            out.append(f"===== {who} 본문 ({entry.get('url')})")
            out.append(entry.get("body") or "")

    out.append("")
    if violations:
        out.append(f"차단 {len(violations)}건:")
        for v in violations:
            out.append(f"  [{v['kind']}] {v['reviewer']} — {v['detail']}")
            out.append(f"        → {v['remedy']}")
    else:
        out.append("차단 조건 없음.")
    return "\n".join(out)


# --- CLI -------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="PR 자동리뷰 3종(Claude·Gemini·Codex) 집계 + 머지 게이트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pr", type=int, help="PR 번호")
    parser.add_argument("--repo", help="OWNER/NAME (생략 시 현재 디렉터리에서 탐지)")
    parser.add_argument("--gate", action="store_true", help="위반이 있으면 exit 1")
    parser.add_argument("--json", action="store_true", dest="as_json", help="기계 판독 JSON 출력")
    parser.add_argument("--full", action="store_true", help="리뷰 본문 전체 포함")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 3

    try:
        repo = args.repo or detect_repo()
        payloads = fetch_payloads(repo, args.pr)
    except GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    summary = collect(payloads)
    violations = evaluate(summary)

    if args.as_json:
        print(json.dumps({"summary": summary, "violations": violations}, ensure_ascii=False, indent=2))
    else:
        print(render(summary, violations, full=args.full))

    return 1 if (args.gate and violations) else 0


if __name__ == "__main__":
    sys.exit(main())
