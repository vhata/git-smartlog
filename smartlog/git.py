"""Small, read-only Git interface used by smartlog."""

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess


class GitError(Exception):
    """A Git query could not be completed."""


@dataclass(frozen=True)
class Ref:
    name: str
    oid: str
    upstream: str | None = None
    symbolic_target: str | None = None


def git_environment():
    """Keep child queries in their explicit repository, without prompting."""
    env = os.environ.copy()
    selectors = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE", "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE",
        "GIT_REPLACE_REF_BASE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
    }
    for key in tuple(env):
        if key in selectors or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            env.pop(key)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
               GIT_NO_LAZY_FETCH="1", LC_ALL="C")
    return env


class Repository:
    """A repository discovered from an explicit directory (including worktrees)."""

    def __init__(self, path="."):
        self.cwd = Path(path).resolve()
        self.git_dir = Path(self.run("rev-parse", "--absolute-git-dir").removesuffix("\n"))
        common = self.run("rev-parse", "--git-common-dir").removesuffix("\n")
        self.common_dir = (self.cwd / common).resolve()
        self.head = self.resolve("HEAD")
        self.refs = self._read_refs()

    def _execute(self, args, *, input=None, check=True):
        try:
            result = subprocess.run(
                ["git", "--no-pager", "-C", str(self.cwd), *args],
                input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=git_environment(), timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(f"Could not run Git: {exc}") from exc
        if check and result.returncode:
            message = result.stderr.decode("utf-8", "replace").strip()
            raise GitError(message or f"Git exited with status {result.returncode}")
        return result

    def run(self, *args):
        return self._execute(args).stdout.decode("utf-8", "surrogateescape")

    def resolve(self, revision):
        if not revision or "\x00" in revision or revision.startswith("-"):
            return None
        result = self._execute(
            ("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"),
            check=False,
        )
        return result.stdout.decode("ascii").strip() if not result.returncode else None

    def _read_refs(self):
        output = self.run(
            "for-each-ref",
            "--format=%(refname)%00%(objectname)%00%(upstream)%00%(symref)%00"
            "%(objecttype)%00%(*objectname)%00%(*objecttype)",
        )
        refs = []
        for line in output.rstrip("\n").split("\n") if output else ():
            name, oid, upstream, target, kind, peeled, peeled_kind = line.split("\x00")
            if kind == "tag" and peeled_kind == "commit":
                oid = peeled
            refs.append(Ref(name, oid, upstream or None, target or None))
        return refs
