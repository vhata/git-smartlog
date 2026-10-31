"""Optional, bounded GitHub enrichment; the local graph never needs the network."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class GitHubRepository:
    host: str
    owner: str
    name: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.host.lower(), self.owner.lower(), self.name.lower()

    @property
    def argument(self) -> str:
        return f"{self.host}/{self.owner}/{self.name}"


def parse_repository(value: str, *, explicit: bool = False) -> GitHubRepository | None:
    """Parse remote URLs, or an explicitly supplied [HOST/]OWNER/REPO slug.

    Only github.com URLs are autodetected: arbitrary Git hosts must not be
    contacted with GitHub credentials merely because they host a Git remote.
    Enterprise hosts and SSH aliases can be named with --github-repo.
    """
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return None
    host = ""
    path = ""
    if "://" in value:
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"https", "http", "ssh", "git"}:
                return None
            host, path = parsed.hostname or "", parsed.path.strip("/")
            if parsed.query or parsed.fragment:
                return None
        except ValueError:
            return None
    else:
        scp = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):([^:]+)", value)
        if scp:
            host, path = scp.groups()
            path = path.strip("/")
        elif explicit:
            parts = value.split("/")
            if len(parts) == 2:
                host, path = "github.com", value
            elif len(parts) == 3:
                host, path = parts[0], "/".join(parts[1:])
    if not explicit and host.lower() != "github.com":
        return None
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        return None
    parts = path.removesuffix(".git").split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts):
        return None
    if any(p in {".", ".."} or p.startswith("-") for p in parts):
        return None
    return GitHubRepository(host, parts[0], parts[1])


def check_status(checks: object) -> str:
    """Summarize every job and legacy status without keying on job names."""
    if checks is None or checks == []:
        return "NO CHECKS"
    if not isinstance(checks, list):
        return "UNKNOWN"
    states = []
    for check in checks:
        if not isinstance(check, dict):
            states.append("UNKNOWN")
            continue
        if "status" in check:
            status = check.get("status")
            if not isinstance(status, str):
                states.append("UNKNOWN")
                continue
            if status in {"QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"}:
                states.append("RUNNING")
            elif status == "COMPLETED":
                conclusion = check.get("conclusion")
                if not isinstance(conclusion, str):
                    states.append("UNKNOWN")
                    continue
                if conclusion == "SUCCESS":
                    states.append("PASSED")
                elif conclusion in {"SKIPPED", "NEUTRAL"}:
                    states.append("SKIPPED")
                elif conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}:
                    states.append("FAILED")
                else:
                    states.append("UNKNOWN")
            else:
                states.append("UNKNOWN")
        elif "state" in check:
            if not isinstance(check.get("state"), str):
                states.append("UNKNOWN")
                continue
            states.append({"SUCCESS": "PASSED", "FAILURE": "FAILED", "ERROR": "FAILED", "PENDING": "RUNNING", "EXPECTED": "RUNNING"}.get(check.get("state"), "UNKNOWN"))
        else:
            states.append("UNKNOWN")
    # Unknown data must not turn a partially understood rollup green.
    for state in ("FAILED", "RUNNING", "UNKNOWN", "PASSED", "SKIPPED"):
        if state in states:
            return state
    return "NO CHECKS"


def _plain(value: str) -> str:
    """Keep remote-controlled metadata on one terminal-safe line."""
    return "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in value)


@dataclass(frozen=True)
class PullRequest:
    number: int
    branch: str
    oid: str
    repository: GitHubRepository
    title: str
    url: str
    state: str
    decision: str
    checks: str
    draft: bool = False

    def lines(self) -> tuple[str, str]:
        status = ["DRAFT" if self.draft and self.state == "OPEN" else self.state]
        if self.decision:
            status.append(self.decision)
        status.append("CHECKS " + self.checks)
        return f"#{self.number}  {_plain(self.title)}  ({', '.join(status)})", _plain(self.url)


def parse_pull_requests(data: object, repository: GitHubRepository) -> list[PullRequest]:
    """Ignore malformed records; repository and exact head SHA are mandatory."""
    if not isinstance(data, list):
        raise ValueError("expected a list of pull requests")
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        number, branch, oid = item.get("number"), item.get("headRefName"), item.get("headRefOid")
        if type(number) is not int or number < 1 or not isinstance(branch, str) or not branch:
            continue
        if not isinstance(oid, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", oid):
            continue
        if not all(isinstance(item.get(field), str) for field in ("title", "url", "state")):
            continue
        if item["state"] not in {"OPEN", "CLOSED", "MERGED"}:
            continue
        try:
            url = urlsplit(item["url"])
        except ValueError:
            continue
        if url.scheme != "https" or url.hostname != repository.host.lower() or url.username or url.password:
            continue
        head_repo, head_owner = item.get("headRepository"), item.get("headRepositoryOwner")
        if not isinstance(head_repo, dict) or not isinstance(head_owner, dict):
            # Deleted forks have no trustworthy head repository identity.
            continue
        owner, name = head_owner.get("login"), head_repo.get("name")
        if not isinstance(owner, str) or not isinstance(name, str):
            continue
        identity = parse_repository(f"{repository.host}/{owner}/{name}", explicit=True)
        if identity is None:
            continue
        decision = item.get("reviewDecision")
        if decision is None:
            decision = ""
        if not isinstance(decision, str) or decision not in {"", "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
            decision = "UNKNOWN REVIEW"
        result.append(PullRequest(number, branch, oid.lower(), identity, item["title"], item["url"], item["state"], decision, check_status(item.get("statusCheckRollup")), item.get("isDraft") is True))
    return sorted(result, key=lambda pr: pr.number)


_FIELDS = "number,state,reviewDecision,title,headRefName,headRefOid,headRepository,headRepositoryOwner,statusCheckRollup,url,isDraft"


def fetch_pull_requests(repository: GitHubRepository, cwd: os.PathLike[str] | str, *, timeout: float = 5.0, warn: Callable[[str], None] | None = None) -> list[PullRequest]:
    """Fetch at most 100 recent PRs with a single time-bounded gh invocation."""
    warn = warn or (lambda message: None)
    env = os.environ.copy()
    env.update({"GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat", "NO_COLOR": "1"})
    try:
        proc = subprocess.run(["gh", "pr", "list", "--repo", repository.argument, "--state", "all", "--limit", "100", "--json", _FIELDS], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    except FileNotFoundError:
        warn("GitHub annotations unavailable: gh is not installed.")
        return []
    except subprocess.TimeoutExpired:
        warn(f"GitHub annotations timed out after {timeout:g} seconds.")
        return []
    except OSError:
        warn("GitHub annotations unavailable: could not run gh.")
        return []
    if proc.returncode:
        # Do not echo stderr: it can contain credentials or unsafe terminal text.
        warn("GitHub annotations unavailable: gh failed; check authentication and repository access.")
        return []
    try:
        data = json.loads(proc.stdout)
        prs = parse_pull_requests(data, repository)
    except (ValueError, UnicodeError, TypeError):
        warn("GitHub annotations unavailable: gh returned invalid data.")
        return []
    if len(data) >= 100:
        warn("GitHub annotations cover at most the 100 most recent pull requests.")
    if len(prs) != len(data):
        warn("Some GitHub pull requests had incomplete metadata and were skipped.")
    return prs


def match_annotations(prs: Iterable[PullRequest], refs: Iterable[object], remotes: Mapping[str, GitHubRepository], visible: Iterable[str]) -> dict[str, list[str]]:
    """Match repository, branch and SHA; branch names alone are ambiguous."""
    visible = set(visible)
    candidates = set()
    for ref in refs:
        name = ref.name if ref.name.startswith("refs/remotes/") else ref.upstream
        if not name or not name.startswith("refs/remotes/"):
            continue
        for remote in sorted(remotes, key=lambda value: (-len(value), value)):
            prefix = f"refs/remotes/{remote}/"
            if name.startswith(prefix):
                branch = name[len(prefix):]
                if branch != "HEAD":
                    candidates.add((remotes[remote].key, branch, ref.oid))
                break
    result: dict[str, list[str]] = {}
    for pr in prs:
        if pr.oid in visible and (pr.repository.key, pr.branch, pr.oid) in candidates:
            result.setdefault(pr.oid, []).extend(pr.lines())
    return result
