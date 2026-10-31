"""Disposable Git repositories; never reuse inherited repository selectors."""

import os
from pathlib import Path
import subprocess
import tempfile
import time


class GitFixture:
    def __init__(self, *, directory=None):
        self.temp = tempfile.TemporaryDirectory(prefix="smartlog-graph-") if directory is None else None
        self.path = Path(self.temp.name if self.temp else directory).resolve()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Fixture Author", GIT_AUTHOR_EMAIL="fixture@example.test",
            GIT_COMMITTER_NAME="Fixture Author", GIT_COMMITTER_EMAIL="fixture@example.test",
        )
        self.git("init", "--initial-branch=main")
        self.tree = self.git("mktree", input=b"").strip()
        self.clock = int(time.time()) - 1000

    def git(self, *args, input=None, env=None):
        result = subprocess.run(
            ["git", "-C", str(self.path), *args], input=input,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env or self.env, check=True,
        )
        return result.stdout.decode("utf-8", "surrogateescape")

    def commit(self, name, *parents, timestamp=None):
        self.clock += 1
        date = f"@{self.clock if timestamp is None else timestamp} +0000"
        env = dict(self.env, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        args = ["commit-tree", self.tree]
        for parent in parents:
            args.extend(["-p", parent])
        return self.git(*args, input=(name + "\n").encode("utf-8"), env=env).strip()

    def ref(self, name, oid):
        self.git("update-ref", name, oid)

    def close(self):
        if self.temp:
            self.temp.cleanup()
