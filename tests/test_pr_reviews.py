"""
tests/test_pr_reviews.py — scripts/pr_reviews.py 단위 테스트.

픽스처는 이 저장소의 실제 PR(#97~#104) 페이로드에서 잘라온 것이다(실측
2026-08-24). 봇 출력 형식이 바뀌면 이 테스트가 먼저 깨지는 게 목적이므로,
합성 문자열 대신 실제 본문을 쓴다.

무엇을 못박는가:
  - 세 리뷰어가 서로 다른 API 경로에 남긴다는 사실 자체 (한 경로만 보면 놓친다)
  - Codex 의 `Reviewed commit` 은 축약 sha 라 접두 비교해야 한다는 것
  - 리뷰 대상 커밋이 HEAD 와 다르면 STALE 로 잡힌다는 것 — 실제로 최근 8개 PR
    중 7개가 이 상태로 머지됐다
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from pr_reviews import (  # type: ignore[import-not-found]
    CODEX,
    GhError,
    collect,
    commits_match,
    evaluate,
    gh_json,
    has_clear_phrase,
    identify_reviewer,
    is_codex,
    main,
    parse_automation_state,
    parse_codex_commit,
    parse_codex_finding,
    render,
)

# --- 실측 페이로드 조각 (PR #103) -----------------------------------------

HEAD_103 = "91f1c09a3b33b53a8f0eb627f2a9451a582783d1"
OLD_103 = "f2c09be682"
CODEX_REVIEW_ID_103 = 5002092897

GEMINI_BODY = (
    "## \U0001f50e Gemini Code Review\n"
    "<!-- automation:gemini-auto-review:v2 -->\n"
    '<!-- automation-state:{"schema":2,"reviewer":"gemini","pr":103,'
    '"run_id":32631545162,"run_attempt":1,'
    '"attempt_head":"91f1c09a3b33b53a8f0eb627f2a9451a582783d1",'
    '"successful_head":"91f1c09a3b33b53a8f0eb627f2a9451a582783d1",'
    '"attempt_status":"success","diff_mode":"delta"} -->\n\n'
    "- Status: success\n\nNo blocking issues found.\n"
)

CLAUDE_BODY = (
    "## Claude Code Review (latest)\n"
    "<!-- automation:claude-code-review:v2 -->\n"
    '<!-- automation-state:{"schema":2,"reviewer":"claude","pr":103,'
    '"run_id":32631545150,"run_attempt":1,'
    '"attempt_head":"91f1c09a3b33b53a8f0eb627f2a9451a582783d1",'
    '"successful_head":"91f1c09a3b33b53a8f0eb627f2a9451a582783d1",'
    '"attempt_status":"success","diff_mode":"delta"} -->\n\n'
    "# 코드 리뷰 — fix(setup)\n\n차단 이슈 없음.\n"
)

CODEX_REVIEW_BODY = (
    "\n### \U0001f4a1 Codex Review\n\n"
    "Here are some automated review suggestions for this pull request.\n\n"
    "**Reviewed commit:** `f2c09be682`\n"
)

CODEX_INLINE_BODY = (
    "**<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>"
    "  Honor the profile's cam-state readiness settings**\n\n"
    "When a camera profile overrides `checks.cam_state.dir` ..."
)

CODEX_NO_FINDING_BODY = "Codex Review: Didn't find any major issues. Breezy!\n\n**Reviewed commit:** `a0b744ce68`\n"

BOT = {"login": "github-actions[bot]", "type": "Bot"}
CODEX_USER = {"login": "chatgpt-codex-connector[bot]", "type": "Bot"}
HUMAN = {"login": "jhw7500", "type": "User"}


def payloads_pr103():
    """PR #103 실제 모양 — codex 만 옛 커밋 기준."""
    return {
        "pr": {"number": 103, "title": "fix(setup)", "state": "open", "head": {"sha": HEAD_103}},
        "issue_comments": [
            {"user": BOT, "body": GEMINI_BODY, "created_at": "2026-08-23T09:31:33Z", "html_url": "u1"},
            {"user": BOT, "body": CLAUDE_BODY, "created_at": "2026-08-23T09:34:29Z", "html_url": "u2"},
        ],
        "review_comments": [
            {
                "user": CODEX_USER,
                "body": CODEX_INLINE_BODY,
                "path": "setup.py",
                "line": 298,
                "created_at": "2026-08-23T09:20:00Z",
                "html_url": "u3",
                "in_reply_to_id": None,
                "pull_request_review_id": CODEX_REVIEW_ID_103,
            }
        ],
        "reviews": [
            {
                "id": CODEX_REVIEW_ID_103,
                "user": CODEX_USER,
                "state": "COMMENTED",
                "body": CODEX_REVIEW_BODY,
                "submitted_at": "2026-08-23T09:20:00Z",
                "html_url": "u4",
            }
        ],
    }


class TestMarkerParsing(unittest.TestCase):
    """automation 마커 — 세 리뷰어 구분의 유일한 근거."""

    def test_state_json_parsed(self):
        state = parse_automation_state(CLAUDE_BODY)
        self.assertIsNotNone(state)
        self.assertEqual(state["reviewer"], "claude")
        self.assertEqual(state["attempt_head"], HEAD_103)
        self.assertEqual(state["attempt_status"], "success")

    def test_reviewer_identified_from_state(self):
        self.assertEqual(identify_reviewer(CLAUDE_BODY), "claude")
        self.assertEqual(identify_reviewer(GEMINI_BODY), "gemini")

    def test_state_without_automation_marker_does_not_identify_reviewer(self):
        body = '<!-- automation-state:{"reviewer":"claude","attempt_status":"success"} -->'
        self.assertIsNone(identify_reviewer(body))

    def test_marker_and_state_reviewer_must_agree(self):
        body = CLAUDE_BODY.replace('"reviewer":"claude"', '"reviewer":"gemini"')
        self.assertIsNone(identify_reviewer(body))

    def test_claude_and_gemini_share_one_login(self):
        # 둘 다 github-actions[bot] 이라 작성자로는 구분이 불가능하다.
        # 마커가 없으면 리뷰어를 특정할 수 없어야 한다.
        self.assertIsNone(identify_reviewer("그냥 사람이 쓴 코멘트"))

    def test_malformed_state_json_is_not_fatal(self):
        self.assertIsNone(parse_automation_state("<!-- automation-state:{깨짐 -->"))

    def test_codex_requires_exact_rest_bot_identity(self):
        self.assertTrue(is_codex(CODEX_USER))
        self.assertFalse(is_codex({"login": "chatgpt-codex-connector", "type": "Bot"}))
        self.assertFalse(is_codex({"login": "chatgpt-codex-connector-evil[bot]", "type": "Bot"}))
        self.assertFalse(is_codex({"login": "chatgpt-codex-connector[bot]", "type": "User"}))
        self.assertFalse(is_codex(BOT))


class TestCodexParsing(unittest.TestCase):
    def test_reviewed_commit_is_abbreviated(self):
        self.assertEqual(parse_codex_commit(CODEX_REVIEW_BODY), "f2c09be682")
        self.assertEqual(parse_codex_commit(CODEX_NO_FINDING_BODY), "a0b744ce68")

    def test_finding_severity_and_title(self):
        f = parse_codex_finding(
            {
                "body": CODEX_INLINE_BODY,
                "path": "setup.py",
                "line": 298,
                "html_url": "u",
                "created_at": "t",
            }
        )
        self.assertEqual(f["severity"], "P2")
        self.assertEqual(f["title"], "Honor the profile's cam-state readiness settings")
        self.assertEqual(f["path"], "setup.py")

    def test_finding_without_badge_falls_back_to_first_line(self):
        f = parse_codex_finding({"body": "무언가 잘못됨\n상세", "path": "a.py", "line": 1})
        self.assertEqual(f["severity"], "?")
        self.assertEqual(f["title"], "무언가 잘못됨")


class TestCommitsMatch(unittest.TestCase):
    """Codex 는 축약 sha 라 접두 비교가 필수다."""

    def test_abbreviated_prefix_matches_full(self):
        self.assertTrue(commits_match("91f1c09a3b", HEAD_103))
        self.assertTrue(commits_match(HEAD_103, "91f1c09a3b"))

    def test_different_commit_does_not_match(self):
        self.assertFalse(commits_match(OLD_103, HEAD_103))

    def test_missing_side_never_matches(self):
        self.assertFalse(commits_match(None, HEAD_103))
        self.assertFalse(commits_match("", HEAD_103))
        self.assertFalse(commits_match(HEAD_103, None))


class TestClearPhrase(unittest.TestCase):
    def test_self_reported_clear(self):
        self.assertTrue(has_clear_phrase(CLAUDE_BODY))  # "차단 이슈 없음"
        self.assertTrue(has_clear_phrase(GEMINI_BODY))  # "No blocking issues found"
        self.assertFalse(has_clear_phrase("P1 결함이 있다"))


class TestCollect(unittest.TestCase):
    """세 경로를 한 번에 모으는 것이 이 도구의 존재 이유다."""

    def test_automation_review_requires_exact_actions_bot_identity(self):
        invalid_users = (
            HUMAN,
            {"login": "review-forger[bot]", "type": "Bot"},
            {"login": "github-actions[bot]", "type": "User"},
        )
        for user in invalid_users:
            with self.subTest(user=user):
                p = payloads_pr103()
                p["issue_comments"] = [c for c in p["issue_comments"] if "claude" not in c["body"]]
                p["issue_comments"].append(
                    {
                        "user": user,
                        "body": CLAUDE_BODY,
                        "created_at": "2026-08-23T10:00:00Z",
                        "html_url": "forged",
                    }
                )
                self.assertNotIn("claude", collect(p)["entries"])

    def test_latest_automation_artifact_wins_even_if_payload_order_is_reversed(self):
        p = payloads_pr103()
        p["issue_comments"] = [c for c in p["issue_comments"] if "claude" not in c["body"]]
        old_body = CLAUDE_BODY.replace(HEAD_103, OLD_103).replace(
            '"attempt_status":"success"', '"attempt_status":"failure"'
        )
        p["issue_comments"].extend(
            [
                {
                    "user": BOT,
                    "body": CLAUDE_BODY,
                    "created_at": "2026-08-23T11:00:00Z",
                    "html_url": "latest",
                },
                {
                    "user": BOT,
                    "body": old_body,
                    "created_at": "2026-08-23T09:00:00Z",
                    "html_url": "old-but-last-in-payload",
                },
            ]
        )

        entry = collect(p)["entries"]["claude"]

        self.assertEqual(entry["url"], "latest")
        self.assertEqual(entry["run_status"], "success")
        self.assertTrue(entry["fresh"])

    def test_all_three_reviewers_collected_from_three_paths(self):
        s = collect(payloads_pr103())
        self.assertEqual(set(s["entries"]), {"claude", "gemini", "codex"})
        self.assertEqual(s["entries"]["claude"]["source"], "issues/comments")
        self.assertEqual(s["entries"]["gemini"]["source"], "issues/comments")
        self.assertEqual(s["entries"]["codex"]["source"], "pulls/reviews")

    def test_freshness_computed_per_reviewer(self):
        s = collect(payloads_pr103())
        self.assertTrue(s["entries"]["claude"]["fresh"])
        self.assertTrue(s["entries"]["gemini"]["fresh"])
        self.assertFalse(s["entries"]["codex"]["fresh"])  # 옛 커밋 기준

    def test_codex_findings_attached(self):
        s = collect(payloads_pr103())
        findings = s["entries"]["codex"]["findings"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "P2")

    def test_reply_comments_are_not_findings(self):
        p = payloads_pr103()
        p["review_comments"].append(
            {
                "user": CODEX_USER,
                "body": "추가 설명",
                "path": "setup.py",
                "line": 298,
                "in_reply_to_id": 12345,
                "created_at": "t",
                "html_url": "u",
            }
        )
        s = collect(p)
        self.assertEqual(len(s["entries"]["codex"]["findings"]), 1)

    def test_codex_no_findings_path_is_issue_comment(self):
        p = payloads_pr103()
        p["reviews"] = []
        p["review_comments"] = []
        p["issue_comments"].append(
            {
                "user": CODEX_USER,
                "body": CODEX_NO_FINDING_BODY,
                "created_at": "2026-08-23T11:00:48Z",
                "html_url": "u5",
            }
        )
        s = collect(p)
        self.assertEqual(s["entries"][CODEX]["source"], "issues/comments")
        self.assertTrue(s["entries"][CODEX]["self_reported_clear"])
        self.assertEqual(s["entries"][CODEX]["findings"], [])

    def test_latest_codex_artifact_wins_across_api_paths(self):
        """새 no-findings 코멘트를 과거 review 객체가 덮어쓰면 안 된다."""
        p = payloads_pr103()
        p["pr"]["head"]["sha"] = "a0b744ce68" + "0" * 30
        p["issue_comments"].append(
            {
                "user": CODEX_USER,
                "body": CODEX_NO_FINDING_BODY,
                "created_at": "2026-08-23T11:00:48Z",
                "html_url": "u5",
            }
        )

        entry = collect(p)["entries"][CODEX]

        self.assertEqual(entry["source"], "issues/comments")
        self.assertEqual(entry["reviewed_commit"], "a0b744ce68")
        self.assertTrue(entry["fresh"])
        self.assertEqual(entry["findings"], [])

    def test_codex_findings_are_scoped_to_latest_review(self):
        """과거 review 지적이 최신 review의 지적으로 다시 나타나면 안 된다."""
        p = payloads_pr103()
        latest_review_id = 5003000000
        latest_body = CODEX_REVIEW_BODY.replace(OLD_103, HEAD_103[:10])
        latest_finding = CODEX_INLINE_BODY.replace(
            "Honor the profile's cam-state readiness settings",
            "Keep findings scoped to the selected review",
        )
        p["reviews"].append(
            {
                "id": latest_review_id,
                "user": CODEX_USER,
                "state": "COMMENTED",
                "body": latest_body,
                "commit_id": HEAD_103,
                "submitted_at": "2026-08-23T10:00:00Z",
                "html_url": "u7",
            }
        )
        p["review_comments"].append(
            {
                "user": CODEX_USER,
                "body": latest_finding,
                "path": "scripts/pr_reviews.py",
                "line": 1,
                "created_at": "2026-08-23T10:00:00Z",
                "html_url": "u8",
                "in_reply_to_id": None,
                "pull_request_review_id": latest_review_id,
            }
        )

        findings = collect(p)["entries"][CODEX]["findings"]

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["title"], "Keep findings scoped to the selected review")

    def test_disposition_requires_human_comment_after_bot(self):
        p = payloads_pr103()
        s = collect(p)
        self.assertFalse(s["disposed"])
        p["issue_comments"].append(
            {
                "user": HUMAN,
                "body": "자동 리뷰 처분 요약 ...",
                "author_association": "OWNER",
                "created_at": "2026-08-23T09:38:03Z",
                "html_url": "u6",
            }
        )
        self.assertTrue(collect(p)["disposed"])

    def test_outsider_comment_cannot_dispose_findings(self):
        p = payloads_pr103()
        p["issue_comments"].append(
            {
                "user": {"login": "untrusted-user", "type": "User"},
                "author_association": "NONE",
                "body": "처분했습니다",
                "created_at": "2026-08-23T09:38:03Z",
                "html_url": "outsider",
            }
        )
        self.assertFalse(collect(p)["disposed"])

    def test_trusted_inline_reply_can_dispose_findings(self):
        p = payloads_pr103()
        p["review_comments"].append(
            {
                "user": HUMAN,
                "author_association": "OWNER",
                "body": "Codex 지적 검토 및 처분",
                "created_at": "2026-08-23T09:38:03Z",
                "html_url": "inline-disposition",
                "in_reply_to_id": 3840563292,
            }
        )
        self.assertTrue(collect(p)["disposed"])

    def test_outsider_inline_reply_cannot_dispose_findings(self):
        p = payloads_pr103()
        p["review_comments"].append(
            {
                "user": {"login": "untrusted-user", "type": "User"},
                "author_association": "NONE",
                "body": "처분했습니다",
                "created_at": "2026-08-23T09:38:03Z",
                "html_url": "outsider-inline",
                "in_reply_to_id": 3840563292,
            }
        )
        self.assertFalse(collect(p)["disposed"])

    def test_trusted_non_reply_review_comment_is_not_disposition(self):
        p = payloads_pr103()
        p["review_comments"].append(
            {
                "user": HUMAN,
                "author_association": "OWNER",
                "body": "별도 인라인 리뷰 지적",
                "created_at": "2026-08-23T09:38:03Z",
                "html_url": "new-human-finding",
                "in_reply_to_id": None,
            }
        )
        self.assertFalse(collect(p)["disposed"])

    def test_sticky_review_update_after_disposition_requires_new_disposition(self):
        p = payloads_pr103()
        p["issue_comments"].append(
            {
                "user": HUMAN,
                "author_association": "OWNER",
                "body": "지적 처분",
                "created_at": "2026-08-23T09:38:03Z",
                "html_url": "disposition",
            }
        )
        claude = next(c for c in p["issue_comments"] if "claude-code-review" in c["body"])
        claude["updated_at"] = "2026-08-23T09:40:00Z"

        self.assertFalse(collect(p)["disposed"])

    def test_human_comment_before_review_is_not_disposition(self):
        p = payloads_pr103()
        p["issue_comments"].insert(
            0,
            {
                "user": HUMAN,
                "body": "리뷰 부탁",
                "created_at": "2026-08-23T08:00:00Z",
                "html_url": "u0",
                "author_association": "OWNER",
            },
        )
        self.assertFalse(collect(p)["disposed"])


class TestEvaluate(unittest.TestCase):
    def test_stale_codex_is_blocked(self):
        v = evaluate(collect(payloads_pr103()))
        kinds = {(x["reviewer"], x["kind"]) for x in v}
        self.assertIn((CODEX, "STALE"), kinds)
        self.assertNotIn(("claude", "STALE"), kinds)
        self.assertNotIn(("gemini", "STALE"), kinds)

    def test_stale_codex_remedy_names_the_trigger(self):
        v = evaluate(collect(payloads_pr103()))
        stale = [x for x in v if x["kind"] == "STALE"][0]
        self.assertIn("@codex review", stale["remedy"])

    def test_missing_reviewer_is_blocked(self):
        p = payloads_pr103()
        p["issue_comments"] = [c for c in p["issue_comments"] if "gemini" not in c["body"]]
        v = evaluate(collect(p))
        self.assertIn(("gemini", "MISSING"), {(x["reviewer"], x["kind"]) for x in v})

    def test_failed_run_is_blocked(self):
        p = payloads_pr103()
        p["issue_comments"][1]["body"] = CLAUDE_BODY.replace('"attempt_status":"success"', '"attempt_status":"failure"')
        v = evaluate(collect(p))
        self.assertIn(("claude", "FAILED"), {(x["reviewer"], x["kind"]) for x in v})

    def test_missing_automation_run_status_is_blocked(self):
        """상태 필드가 사라진 자동 리뷰를 성공으로 오인하면 안 된다."""
        p = payloads_pr103()
        p["issue_comments"][1]["body"] = CLAUDE_BODY.replace('"attempt_status":"success",', "")
        v = evaluate(collect(p))
        self.assertIn(("claude", "FAILED"), {(x["reviewer"], x["kind"]) for x in v})

    def test_findings_without_disposition_are_blocked(self):
        v = evaluate(collect(payloads_pr103()))
        self.assertIn("FINDINGS", {x["kind"] for x in v})

    def test_findings_with_disposition_are_not_blocked(self):
        p = payloads_pr103()
        p["issue_comments"].append(
            {
                "user": HUMAN,
                "body": "처분 요약",
                "created_at": "2026-08-23T09:38:03Z",
                "html_url": "u6",
                "author_association": "OWNER",
            }
        )
        v = evaluate(collect(p))
        self.assertNotIn("FINDINGS", {x["kind"] for x in v})

    def test_clean_pr_has_no_violations(self):
        """세 리뷰어 모두 HEAD 기준 + 지적 없음 → 통과."""
        p = {
            "pr": {"number": 104, "title": "t", "state": "open", "head": {"sha": "a0b744ce68f1" + "0" * 28}},
            "issue_comments": [
                {
                    "user": BOT,
                    "body": GEMINI_BODY.replace(HEAD_103, "a0b744ce68f1" + "0" * 28),
                    "created_at": "t1",
                    "html_url": "u",
                },
                {
                    "user": BOT,
                    "body": CLAUDE_BODY.replace(HEAD_103, "a0b744ce68f1" + "0" * 28),
                    "created_at": "t2",
                    "html_url": "u",
                },
                {"user": CODEX_USER, "body": CODEX_NO_FINDING_BODY, "created_at": "t3", "html_url": "u"},
            ],
            "review_comments": [],
            "reviews": [],
        }
        self.assertEqual(evaluate(collect(p)), [])


class TestRender(unittest.TestCase):
    def test_render_lists_every_reviewer(self):
        s = collect(payloads_pr103())
        text = render(s, evaluate(s))
        for who in ("claude", "gemini", "codex"):
            self.assertIn(who, text)
        self.assertIn("STALE", text)

    def test_render_reports_missing_reviewer(self):
        p = payloads_pr103()
        p["issue_comments"] = []
        s = collect(p)
        self.assertIn("MISSING", render(s, evaluate(s)))

    def test_render_combines_stale_and_findings_status(self):
        text = render(collect(payloads_pr103()), [])
        self.assertRegex(text, r"(?m)^codex\s+STALE/FINDINGS\s+")


class TestGhErrors(unittest.TestCase):
    def test_missing_gh_binary_raises_gherror(self):
        import unittest.mock as mock

        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(GhError):
                gh_json(["api", "x"])

    def test_nonzero_exit_raises_gherror(self):
        import subprocess as sp
        import unittest.mock as mock

        proc = sp.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
        with mock.patch("subprocess.run", return_value=proc):
            with self.assertRaises(GhError):
                gh_json(["api", "x"])


class TestCli(unittest.TestCase):
    def test_invalid_arguments_return_documented_input_error_code(self):
        import contextlib
        import io

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([]), 3)


if __name__ == "__main__":
    unittest.main()
