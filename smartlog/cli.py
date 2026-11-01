"""Command-line entry point and local repository selection policy."""

from __future__ import annotations

import argparse
from collections import defaultdict
import configparser
import math
import os
from pathlib import Path
import sys
from time import monotonic, time
from typing import Iterable, Sequence

from . import __version__


class ConfigurationError(ValueError):
    pass


def _nonnegative(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return result


def _positive_seconds(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Show a sparse Git graph of your branches and nearby work.")
    result.add_argument("-C", "--directory", default=".", metavar="PATH", help="run in this repository or subdirectory (default: current directory)")
    result.add_argument("-a", "--all", action="store_true", help="include branch tips of every age (does not disable --limit)")
    result.add_argument("--base", metavar="REF", help="base branch or commit (overrides [remote] head in .git/smartlog)")
    result.add_argument("--days", type=_nonnegative, default=14, metavar="N", help="include branch tips updated within N days (default: 14)")
    result.add_argument("--limit", type=_nonnegative, default=20, metavar="N", help="maximum commits in each unbranched run; 0 keeps all (default: 20)")
    result.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="color output (default: auto; honors NO_COLOR)")
    result.add_argument("--ascii", action="store_true", help="use ASCII graph characters")
    result.add_argument("--github", action="store_true", help="fetch optional PR/check annotations using gh (network access)")
    result.add_argument("--github-repo", metavar="[HOST/]OWNER/REPO", help="GitHub repository for annotations; requires --github")
    result.add_argument("--github-timeout", type=_positive_seconds, default=5.0, metavar="SECONDS", help="GitHub request timeout (default: 5)")
    result.add_argument("--timing", action="store_true", help="report elapsed time on stderr")
    result.add_argument("--version", action="version", version=f"git-smartlog {__version__}")
    return result


def _safe(value: object) -> str:
    return "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in str(value))


def warn(message: str) -> None:
    print(f"git-smartlog: {_safe(message)}", file=sys.stderr)


def load_config(repo: object) -> configparser.ConfigParser:
    config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    config.optionxform = str  # Branch names are case-sensitive.
    paths = [Path(repo.common_dir) / "smartlog"]
    local = Path(repo.git_dir) / "smartlog"
    if local != paths[0]:
        paths.append(local)
    for path in paths:
        try:
            with path.open(encoding="utf-8") as stream:
                config.read_file(stream, source=str(path))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError, configparser.Error) as exc:
            raise ConfigurationError(f"cannot read {_safe(path)}: {_safe(exc)}") from exc
    return config


def short_ref(name: str) -> str:
    for prefix in ("refs/heads/", "refs/remotes/", "refs/tags/"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def ordered_remotes(names: Iterable[str]) -> list[str]:
    return sorted(set(names), key=lambda name: (0 if name == "origin" else 1 if name == "upstream" else 2, name))


def _find_ref(refs: Iterable[object], name: str) -> object | None:
    options = (name, f"refs/heads/{name}", f"refs/remotes/{name}", f"refs/tags/{name}")
    lookup = {ref.name: ref for ref in refs}
    return next((lookup[option] for option in options if option in lookup), None)


def select_base(repo: object, config: configparser.ConfigParser, explicit: str | None, remotes: Sequence[str]) -> tuple[str, str, bool]:
    """Return full ref name/label, SHA and whether HEAD fallback was needed."""
    requested = explicit
    if requested is None and config.has_option("remote", "head"):
        requested = config.get("remote", "head")
        if requested is None or not requested:
            raise ConfigurationError("[remote] head must name a branch or commit")
    if requested is not None:
        if not requested or requested.startswith("-"):
            raise ConfigurationError("base must name a branch or commit")
        ref = _find_ref(repo.refs, requested)
        oid = repo.resolve(ref.name if ref is not None else requested)
        if oid is None:
            raise ConfigurationError(f"cannot resolve base '{_safe(requested)}'; choose an existing branch with --base")
        return ref.name if ref is not None else requested, oid, False
    refs = {ref.name: ref for ref in repo.refs}
    for remote in remotes:
        symbolic = refs.get(f"refs/remotes/{remote}/HEAD")
        if symbolic is not None and symbolic.symbolic_target in refs:
            target = refs[symbolic.symbolic_target]
            return target.name, target.oid, False
    for remote in remotes:
        for branch in ("main", "trunk", "master", "develop"):
            ref = refs.get(f"refs/remotes/{remote}/{branch}")
            if ref is not None:
                return ref.name, ref.oid, False
    for branch in ("main", "trunk", "master", "develop"):
        ref = refs.get(f"refs/heads/{branch}")
        if ref is not None:
            return ref.name, ref.oid, False
    if repo.head:
        return "HEAD", repo.head, True
    # An unborn checked-out branch can coexist with committed local branches.
    local = sorted((ref for ref in repo.refs if ref.name.startswith("refs/heads/")), key=lambda ref: ref.name)
    if local:
        return local[0].name, local[0].oid, True
    raise ConfigurationError("no commits are available; create a commit first")


def selected_tips(repo: object, config: configparser.ConfigParser, base: str, *, include_all: bool, days: int, now: float | None = None) -> tuple[list[str], dict[str, list[str]], int]:
    """Select recent local/tracking tips; always retain HEAD and the base."""
    refs = {ref.name: ref for ref in repo.refs}
    labels: dict[str, set[str]] = defaultdict(set)
    candidates = set()
    current_name = None
    try:
        current_name = repo.run("symbolic-ref", "--quiet", "HEAD").rstrip("\n")
    except Exception as exc:
        # Detached HEAD is an expected nonzero symbolic-ref result. Other Git
        # errors will still be reported by subsequent required operations.
        from .git import GitError
        if not isinstance(exc, GitError):
            raise
    for ref in repo.refs:
        if not ref.name.startswith("refs/heads/"):
            continue
        candidates.add(ref.oid)
        name = short_ref(ref.name)
        labels[ref.oid].add("HEAD -> " + name if ref.name == current_name else name)
        if ref.upstream:
            upstream = refs.get(ref.upstream)
            if upstream is None:
                warn(f"tracking ref '{short_ref(ref.upstream)}' is missing; skipping it.")
            else:
                candidates.add(upstream.oid)
                labels[upstream.oid].add(short_ref(upstream.name))
    if config.has_section("extra_refs"):
        for name in config["extra_refs"]:
            oid = repo.resolve(name)
            if oid is None:
                warn(f"extra ref '{name}' is missing; check the smartlog configuration.")
                continue
            candidates.add(oid)
            labels[oid].add(short_ref(name))
    if repo.head:
        candidates.add(repo.head)
        if current_name is None:
            labels[repo.head].add("HEAD")
    required = {base}
    if repo.head:
        required.add(repo.head)
    kept = set(candidates) | required
    skipped = 0
    if not include_all and candidates - required:
        # A batch avoids starting a Git process for every branch.
        rows = repo.run("show", "-s", "--no-show-signature", "--no-notes", "--no-decorate", "--format=%H%x00%ct", *sorted(candidates - required), "--")
        dates = {}
        for row in rows.splitlines():
            oid, separator, timestamp = row.partition("\x00")
            if separator:
                try:
                    dates[oid] = int(timestamp)
                except ValueError as exc:
                    raise ConfigurationError("Git returned an invalid commit date") from exc
        cutoff = (time() if now is None else now) - days * 86400
        for oid in candidates - required:
            if oid not in dates:
                raise ConfigurationError("Git did not return a date for a selected commit")
            if dates[oid] < cutoff:
                kept.remove(oid)
                skipped += 1
    return sorted(kept), {oid: sorted(names, key=lambda name: (not name.startswith("HEAD -> "), name.casefold(), name)) for oid, names in labels.items()}, skipped


def _annotations(repo: object, args: argparse.Namespace, remote_names: Sequence[str], base_name: str, visible: Iterable[str]) -> dict[str, list[str]]:
    from .git import GitError
    from .github import fetch_pull_requests, match_annotations, parse_repository
    identities = {}
    for remote in remote_names:
        try:
            url = repo.run("remote", "get-url", remote).rstrip("\n")
        except GitError:
            continue
        identity = parse_repository(url)
        if identity is not None:
            identities[remote] = identity
    explicit = parse_repository(args.github_repo, explicit=True) if args.github_repo else None
    if args.github_repo and explicit is None:
        raise ConfigurationError("--github-repo must be [HOST/]OWNER/REPO")
    preferred = next((name for name in sorted(remote_names, key=lambda value: -len(value)) if base_name.startswith(f"refs/remotes/{name}/")), None)
    target = explicit or identities.get(preferred) or next(iter(identities.values()), None)
    if target is None:
        warn("GitHub annotations unavailable: no github.com remote; use --github-repo for an explicit repository.")
        return {}
    if explicit:
        # For enterprise/SSH-alias URLs, recognize matching identity only when
        # the explicit repository proves the host is intended for GitHub use.
        for remote in remote_names:
            try:
                url = repo.run("remote", "get-url", remote).rstrip("\n")
            except GitError:
                continue
            identity = parse_repository(url, explicit=True)
            if identity is not None and identity.host.lower() == explicit.host.lower():
                identities[remote] = identity
            elif identity is not None and identity.key[1:] == explicit.key[1:]:
                identities[remote] = explicit
    prs = fetch_pull_requests(target, repo.cwd, timeout=args.github_timeout, warn=warn)
    return match_annotations(prs, repo.refs, identities, visible)


def _run(args: argparse.Namespace) -> int:
    from .git import GitError, Repository
    from .graph import build_graph
    from .printer import render_graph
    repo = Repository(args.directory)
    config = load_config(repo)
    if repo.head is None and not repo.refs and args.base is None and not config.has_option("remote", "head"):
        print("No commits yet.")
        return 0
    remotes = ordered_remotes(name for name in repo.run("remote").split("\n") if name)
    base_name, base_oid, fallback = select_base(repo, config, args.base, remotes)
    if fallback:
        warn(f"using {short_ref(base_name)} as the base; use --base to choose another reference.")
    tips, labels, skipped = selected_tips(repo, config, base_oid, include_all=args.all, days=args.days)
    base_label = short_ref(base_name)
    if base_label != "HEAD" and base_label not in labels.get(base_oid, []) and "HEAD -> " + base_label not in labels.get(base_oid, []):
        labels.setdefault(base_oid, []).append(base_label)
    # All selected tips are anchors, including intermediate named commits.
    graph = build_graph(repo, base_oid, tips, anchors=tips, limit=args.limit)
    annotations = _annotations(repo, args, remotes, base_name, graph.commits) if args.github else {}
    color = args.color == "always" or (args.color == "auto" and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb" and sys.stdout.isatty())
    rendered = render_graph(graph, labels=labels, head=repo.head, color=color, ascii=args.ascii, annotations=annotations)
    if rendered:
        print(rendered, end="" if rendered.endswith("\n") else "\n", flush=True)
    if skipped:
        warn(f"skipped {skipped} old branch tip{'s' if skipped != 1 else ''}; use --all to include them.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argparser = parser()
    args = argparser.parse_args(argv)
    if args.github_repo and not args.github:
        argparser.error("--github-repo requires --github")
    started = monotonic()
    try:
        from .git import GitError
        try:
            return _run(args)
        except (GitError, ConfigurationError) as exc:
            warn(str(exc))
            return 1
        except BrokenPipeError:
            raise
        except OSError as exc:
            warn(str(exc))
            return 1
    except BrokenPipeError:
        # Prevent another exception from the interpreter's final stdout flush.
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        if args.timing:
            warn(f"finished in {monotonic() - started:.3f} s.")
