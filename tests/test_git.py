import os
from pathlib import Path
import unittest
from unittest.mock import patch

from graph_helpers import GitFixture
from smartlog.git import GitError, Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def test_unborn_repo(self):
        repo = Repository(self.fixture.path)
        self.assertIsNone(repo.head)
        self.assertEqual(repo.refs, [])
        self.assertEqual(repo.git_dir, self.fixture.path / ".git")
        self.assertEqual(repo.common_dir, repo.git_dir)

    def test_nested_discovery_and_isolated_environment(self):
        root = self.fixture.commit("root")
        self.fixture.ref("refs/heads/main", root)
        nested = self.fixture.path / "one" / "two"
        nested.mkdir(parents=True)
        with patch.dict(os.environ, {
            "GIT_DIR": "/not/a/repo", "GIT_WORK_TREE": "/not/a/worktree",
            "GIT_COMMON_DIR": "/not/a/common", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "invalid",
        }):
            repo = Repository(nested)
            self.assertEqual(repo.head, root)
            self.assertEqual(repo.common_dir, self.fixture.path / ".git")
            self.assertEqual(repo.resolve("HEAD"), root)

    def test_refs_keep_full_names_and_symbolic_target(self):
        root = self.fixture.commit("root")
        self.fixture.ref("refs/heads/MainCase", root)
        self.fixture.ref("refs/remotes/upstream/develop", root)
        self.fixture.git("symbolic-ref", "refs/remotes/upstream/HEAD", "refs/remotes/upstream/develop")
        refs = {ref.name: ref for ref in Repository(self.fixture.path).refs}
        self.assertEqual(refs["refs/heads/MainCase"].oid, root)
        self.assertEqual(refs["refs/remotes/upstream/HEAD"].symbolic_target,
                         "refs/remotes/upstream/develop")

    def test_invalid_revision_is_not_an_option(self):
        repo = Repository(self.fixture.path)
        self.assertIsNone(repo.resolve("--help"))
        self.assertIsNone(repo.resolve("missing"))
        self.assertIsNone(repo.resolve("bad\x00name"))

    def test_nonrepository_reports_git_error(self):
        with self.assertRaises(GitError):
            Repository(self.fixture.path / "missing")

    def test_annotated_tag_uses_commit_oid(self):
        root = self.fixture.commit("root")
        self.fixture.ref("refs/heads/main", root)
        self.fixture.git("tag", "-a", "release", "-m", "release", root)
        repo = Repository(self.fixture.path)
        self.assertEqual(repo.resolve("release"), root)
        self.assertEqual(next(ref.oid for ref in repo.refs if ref.name == "refs/tags/release"), root)

    def test_linked_worktree_common_directory(self):
        root = self.fixture.commit("root")
        self.fixture.ref("refs/heads/main", root)
        other = self.fixture.path / "linked"
        self.fixture.git("worktree", "add", "--detach", str(other), root)
        repo = Repository(other)
        self.assertEqual(repo.common_dir, self.fixture.path / ".git")
        self.assertNotEqual(repo.git_dir, repo.common_dir)
        self.assertEqual(repo.head, root)
