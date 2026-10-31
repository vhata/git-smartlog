import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from smartlog.github import (
    GitHubRepository,
    check_status,
    fetch_pull_requests,
    match_annotations,
    parse_pull_requests,
    parse_repository,
)


OID = "a" * 40
REPO = GitHubRepository("github.com", "owner", "project")


def pull_request(**overrides):
    result = {
        "number": 12,
        "headRefName": "topic",
        "headRefOid": OID,
        "headRepository": {"name": "project"},
        "headRepositoryOwner": {"login": "owner"},
        "title": "Improve things",
        "url": "https://github.com/owner/project/pull/12",
        "state": "OPEN",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
    }
    result.update(overrides)
    return result


class CheckStatusTests(unittest.TestCase):
    def test_empty_is_not_a_pass(self):
        for value in (None, []):
            self.assertEqual(check_status(value), "NO CHECKS")

    def test_duplicate_job_names_do_not_hide_failure(self):
        self.assertEqual(check_status([
            {"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ]), "FAILED")

    def test_failure_outweighs_running(self):
        self.assertEqual(check_status([
            {"status": "QUEUED"},
            {"status": "COMPLETED", "conclusion": "TIMED_OUT"},
        ]), "FAILED")

    def test_running_outweighs_passed(self):
        self.assertEqual(check_status([
            {"status": "IN_PROGRESS"},
            {"status": "COMPLETED", "conclusion": "SUCCESS"},
        ]), "RUNNING")

    def test_mixed_passed_and_skipped(self):
        self.assertEqual(check_status([
            {"status": "COMPLETED", "conclusion": "NEUTRAL"},
            {"status": "COMPLETED", "conclusion": "SUCCESS"},
        ]), "PASSED")
        self.assertEqual(check_status([{"status": "COMPLETED", "conclusion": "SKIPPED"}]), "SKIPPED")

    def test_legacy_status_contexts(self):
        for state, expected in (("SUCCESS", "PASSED"), ("FAILURE", "FAILED"), ("ERROR", "FAILED"), ("PENDING", "RUNNING"), ("EXPECTED", "RUNNING")):
            with self.subTest(state=state):
                self.assertEqual(check_status([{"context": "build", "state": state}]), expected)

    def test_malformed_and_unknown_rollups_never_pass(self):
        for value in ({}, "bad", [None], [{}], [{"state": []}], [{"status": []}], [{"status": "COMPLETED", "conclusion": []}], [{"status": "FUTURE_STATE"}], [{"status": "COMPLETED", "conclusion": "FUTURE_VALUE"}]):
            with self.subTest(value=value):
                self.assertEqual(check_status(value), "UNKNOWN")
        self.assertEqual(check_status([{"state": "SUCCESS"}, {}]), "UNKNOWN")


class RepositoryParsingTests(unittest.TestCase):
    def test_supported_github_remote_spellings(self):
        for remote in ("git@github.com:owner/project.git", "https://github.com/owner/project.git", "ssh://git@github.com/owner/project", "git://github.com/owner/project"):
            with self.subTest(remote=remote):
                self.assertEqual(parse_repository(remote), REPO)

    def test_only_github_is_autodetected(self):
        self.assertIsNone(parse_repository("git@gitlab.com:owner/project.git"))
        self.assertIsNone(parse_repository("/tmp/owner/project"))
        self.assertIsNone(parse_repository("owner/project"))

    def test_explicit_enterprise_and_slug(self):
        self.assertEqual(parse_repository("owner/project", explicit=True), REPO)
        self.assertEqual(parse_repository("git.example.org/owner/project", explicit=True).argument, "git.example.org/owner/project")

    def test_invalid_identity(self):
        for value in ("", "--help", "github.com/a/b/c", "a/..", "a/-b", "https://github.com/a/b?token=secret", "https://[broken/a/b", " a/b"):
            with self.subTest(value=value):
                self.assertIsNone(parse_repository(value, explicit=True))


class PullRequestParsingTests(unittest.TestCase):
    def test_complete_pr(self):
        pr, = parse_pull_requests([pull_request()], REPO)
        self.assertEqual(pr.repository, REPO)
        self.assertEqual(pr.checks, "PASSED")
        self.assertEqual(pr.lines()[0], "#12  Improve things  (OPEN, APPROVED, CHECKS PASSED)")

    def test_null_checks_and_empty_review(self):
        pr, = parse_pull_requests([pull_request(statusCheckRollup=None, reviewDecision=None)], REPO)
        self.assertEqual(pr.lines()[0], "#12  Improve things  (OPEN, CHECKS NO CHECKS)")

    def test_draft_and_terminal_controls(self):
        pr, = parse_pull_requests([pull_request(title="bad\x1b[31m\nname", isDraft=True)], REPO)
        self.assertNotIn("\x1b", pr.lines()[0])
        self.assertNotIn("\n", pr.lines()[0])
        self.assertIn("DRAFT", pr.lines()[0])

    def test_missing_or_malformed_data_ignored(self):
        bad = [None, "invalid", {}, pull_request(number=True), pull_request(headRefOid="oops"), pull_request(headRepository=None), pull_request(headRepositoryOwner=[]), pull_request(state="FUTURE"), pull_request(url="https://bad.example/pull/12"), pull_request(title=[])]
        self.assertEqual(parse_pull_requests(bad, REPO), [])
        with self.assertRaises(ValueError):
            parse_pull_requests({}, REPO)

    def test_malformed_review_is_unknown(self):
        pr, = parse_pull_requests([pull_request(reviewDecision=[])], REPO)
        self.assertEqual(pr.decision, "UNKNOWN REVIEW")
        pr, = parse_pull_requests([pull_request(reviewDecision=["APPROVED"])], REPO)
        self.assertEqual(pr.decision, "UNKNOWN REVIEW")

    def test_matching_requires_repository_branch_and_exact_sha(self):
        prs = parse_pull_requests([
            pull_request(),
            pull_request(number=13, headRepositoryOwner={"login": "fork"}),
            pull_request(number=14, headRefName="other"),
            pull_request(number=15, headRefOid="b" * 40),
        ], REPO)
        refs = [SimpleNamespace(name="refs/remotes/upstream/topic", oid=OID, upstream=None)]
        result = match_annotations(prs, refs, {"upstream": REPO}, [OID, "b" * 40])
        self.assertEqual(len(result[OID]), 2)
        self.assertIn("#12", result[OID][0])
        self.assertNotIn("b" * 40, result)

    def test_local_branch_uses_upstream_identity(self):
        prs = parse_pull_requests([pull_request()], REPO)
        refs = [SimpleNamespace(name="refs/heads/renamed-local", oid=OID, upstream="refs/remotes/fork/topic")]
        self.assertIn(OID, match_annotations(prs, refs, {"fork": REPO}, [OID]))
        refs[0].upstream = None
        self.assertEqual(match_annotations(prs, refs, {"fork": REPO}, [OID]), {})

    def test_no_duplicate_annotation_for_local_and_remote_ref(self):
        prs = parse_pull_requests([pull_request()], REPO)
        refs = [SimpleNamespace(name="refs/heads/topic", oid=OID, upstream="refs/remotes/upstream/topic"), SimpleNamespace(name="refs/remotes/upstream/topic", oid=OID, upstream=None)]
        self.assertEqual(len(match_annotations(prs, refs, {"upstream": REPO}, [OID])[OID]), 2)


class GitHubFetchTests(unittest.TestCase):
    def test_fetch_is_bounded_explicit_and_noninteractive(self):
        warnings = []
        with patch("smartlog.github.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps([pull_request()]).encode())) as run:
            result = fetch_pull_requests(REPO, "/tmp", timeout=2.5, warn=warnings.append)
        self.assertEqual(len(result), 1)
        argv = run.call_args.args[0]
        self.assertIn("github.com/owner/project", argv)
        self.assertEqual(argv[argv.index("--limit") + 1], "100")
        self.assertEqual(run.call_args.kwargs["timeout"], 2.5)
        self.assertEqual(run.call_args.kwargs["env"]["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(warnings, [])

    def test_failures_degrade_to_warning(self):
        for error in (FileNotFoundError(), OSError(), subprocess.TimeoutExpired("gh", 1)):
            warnings = []
            with self.subTest(error=error), patch("smartlog.github.subprocess.run", side_effect=error):
                self.assertEqual(fetch_pull_requests(REPO, "/tmp", warn=warnings.append), [])
                self.assertEqual(len(warnings), 1)

    def test_error_stderr_is_not_echoed(self):
        warnings = []
        with patch("smartlog.github.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b"secret credential")):
            self.assertEqual(fetch_pull_requests(REPO, "/tmp", warn=warnings.append), [])
        self.assertNotIn("secret", warnings[0])

    def test_malformed_json_or_schema_is_graceful(self):
        for data in (b"not json", b"{}", b"\xff"):
            warnings = []
            with self.subTest(data=data), patch("smartlog.github.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=data)):
                self.assertEqual(fetch_pull_requests(REPO, "/tmp", warn=warnings.append), [])
                self.assertIn("invalid data", warnings[0])


if __name__ == "__main__":
    unittest.main()
