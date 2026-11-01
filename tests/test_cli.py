import configparser
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from smartlog.cli import ConfigurationError, load_config, ordered_remotes, parser, select_base, selected_tips


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "git-smartlog.py"
A, B, C = "a" * 40, "b" * 40, "c" * 40


def config(text=""):
    result = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    result.optionxform = str
    result.read_string(text)
    return result


def ref(name, oid=A, upstream=None, symbolic_target=None):
    return SimpleNamespace(name=name, oid=oid, upstream=upstream, symbolic_target=symbolic_target)


class FakeRepo:
    def __init__(self, refs=(), head=A):
        self.refs = list(refs)
        self.head = head
        self.current = "refs/heads/topic"
        self.dates = {A: 2000000, B: 100, C: 2000000}
        self.commands = []

    def resolve(self, name):
        if name == "HEAD":
            return self.head
        return next((r.oid for r in self.refs if r.name in (name, "refs/heads/" + name, "refs/remotes/" + name, "refs/tags/" + name)), None)

    def run(self, *args):
        self.commands.append(args)
        if args[:2] == ("symbolic-ref", "--quiet"):
            return self.current
        if args[0] == "show":
            return "\n".join(f"{oid}\x00{self.dates[oid]}" for oid in args if oid in self.dates)
        raise AssertionError(args)


class BaseSelectionTests(unittest.TestCase):
    def test_cached_symbolic_remote_head_with_nonstandard_branch(self):
        repo = FakeRepo([ref("refs/remotes/upstream/stable", B), ref("refs/remotes/upstream/HEAD", B, symbolic_target="refs/remotes/upstream/stable"), ref("refs/heads/main")])
        self.assertEqual(select_base(repo, config(), None, ["upstream"]), ("refs/remotes/upstream/stable", B, False))
        self.assertEqual(repo.commands, [])

    def test_origin_priority_then_upstream_then_sorted(self):
        self.assertEqual(ordered_remotes(["z", "upstream", "a", "origin"]), ["origin", "upstream", "a", "z"])

    def test_named_remote_fallback_does_not_force_origin(self):
        repo = FakeRepo([ref("refs/remotes/company/develop", B)])
        self.assertEqual(select_base(repo, config(), None, ["company"])[0], "refs/remotes/company/develop")

    def test_local_only_and_head_fallback(self):
        repo = FakeRepo([ref("refs/heads/main", B)])
        self.assertEqual(select_base(repo, config(), None, []), ("refs/heads/main", B, False))
        self.assertEqual(select_base(FakeRepo(), config(), None, []), ("HEAD", A, True))

    def test_explicit_and_configured_base(self):
        repo = FakeRepo([ref("refs/heads/Feature", B), ref("refs/heads/main", C)])
        settings = config("[remote]\nhead=Feature")
        self.assertEqual(select_base(repo, settings, None, [])[1], B)
        self.assertEqual(select_base(repo, settings, "main", [])[1], C)

    def test_invalid_explicit_base_does_not_silently_fall_back(self):
        repo = FakeRepo([ref("refs/heads/main")])
        for name in ("missing", "--help", ""):
            with self.subTest(name=name), self.assertRaises(ConfigurationError):
                select_base(repo, config(), name, [])
        with self.assertRaises(ConfigurationError):
            select_base(repo, config("[remote]\nhead"), None, [])

    def test_explicit_branch_wins_over_same_named_tag(self):
        repo = FakeRepo([ref("refs/tags/main", B), ref("refs/heads/main", C)])
        self.assertEqual(select_base(repo, config(), "main", [])[:2], ("refs/heads/main", C))

    def test_unicode_whitespace_is_a_valid_explicit_ref(self):
        repo = FakeRepo([ref("refs/heads/\u2000", B)])
        self.assertEqual(select_base(repo, config(), "\u2000", [])[:2], ("refs/heads/\u2000", B))

    def test_unborn_with_other_branches(self):
        repo = FakeRepo([ref("refs/heads/topic", B)], head=None)
        self.assertEqual(select_base(repo, config(), None, []), ("refs/heads/topic", B, True))


class ConfigAndSelectionTests(unittest.TestCase):
    def test_common_config_and_worktree_override_preserve_case_and_percent(self):
        with tempfile.TemporaryDirectory() as directory:
            common = Path(directory) / "common"
            local = Path(directory) / "local"
            common.mkdir()
            local.mkdir()
            (common / "smartlog").write_text("[remote]\nhead=main\n[extra_refs]\norigin/Feature%Done\n", encoding="utf-8")
            (local / "smartlog").write_text("[remote]\nhead=develop\n", encoding="utf-8")
            result = load_config(SimpleNamespace(common_dir=common, git_dir=local))
            self.assertEqual(result.get("remote", "head"), "develop")
            self.assertEqual(list(result["extra_refs"]), ["origin/Feature%Done"])

    def test_remote_option_case_is_legacy_compatible_across_worktrees(self):
        with tempfile.TemporaryDirectory() as directory:
            common, local = Path(directory) / "common", Path(directory) / "local"
            common.mkdir()
            local.mkdir()
            (common / "smartlog").write_text("[remote]\nhead=main\n", encoding="utf-8")
            (local / "smartlog").write_text("[remote]\nHead=develop\n[extra_refs]\norigin/Feature\n", encoding="utf-8")
            settings = load_config(SimpleNamespace(common_dir=common, git_dir=local))
            self.assertEqual(settings.get("remote", "head"), "develop")
            self.assertEqual(list(settings["extra_refs"]), ["origin/Feature"])

    def test_worktree_defaults_remain_inherited(self):
        with tempfile.TemporaryDirectory() as directory:
            common, local = Path(directory) / "common", Path(directory) / "local"
            common.mkdir()
            local.mkdir()
            (common / "smartlog").write_text("[DEFAULT]\nHead=main\n[remote]\n", encoding="utf-8")
            (local / "smartlog").write_text("[DEFAULT]\nHEAD=develop\n", encoding="utf-8")
            settings = load_config(SimpleNamespace(common_dir=common, git_dir=local))
            self.assertEqual(settings.get("remote", "head"), "develop")

    def test_duplicate_remote_keys_and_valueless_continuation_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "smartlog"
            for content in ("[remote]\nHead=main\nhead=develop\n", "[extra_refs]\norigin/Feature\n  indented continuation\n"):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ConfigurationError):
                        load_config(SimpleNamespace(common_dir=directory, git_dir=directory))

    def test_bad_config_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "smartlog").write_text("not an ini file", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_config(SimpleNamespace(common_dir=directory, git_dir=directory))

    def test_old_tips_filtered_but_head_and_base_kept(self):
        repo = FakeRepo([ref("refs/heads/topic", A), ref("refs/heads/old", B), ref("refs/heads/recent", C)])
        tips, labels, skipped = selected_tips(repo, config(), A, include_all=False, days=14, now=2000000)
        self.assertEqual(tips, [A, C])
        self.assertEqual(skipped, 1)
        self.assertIn("HEAD -> topic", labels[A])
        self.assertEqual(sum(args[0] == "show" for args in repo.commands), 1)
        date_query = next(args for args in repo.commands if args[0] == "show")
        for option in ("--no-show-signature", "--no-notes", "--no-decorate"):
            self.assertIn(option, date_query)
        tips, _, skipped = selected_tips(repo, config(), B, include_all=False, days=14, now=2000000)
        self.assertEqual(tips, [A, B, C])
        self.assertEqual(skipped, 0)

    def test_all_does_not_query_dates(self):
        repo = FakeRepo([ref("refs/heads/old", B)])
        tips, _, skipped = selected_tips(repo, config(), A, include_all=True, days=14)
        self.assertEqual(tips, [A, B])
        self.assertEqual(skipped, 0)
        self.assertFalse(any(args[0] == "show" for args in repo.commands))

    def test_missing_upstream_and_extra_ref_warn_without_failing(self):
        repo = FakeRepo([ref("refs/heads/topic", A, "refs/remotes/origin/gone")])
        output = io.StringIO()
        with redirect_stderr(output):
            tips, _, _ = selected_tips(repo, config("[extra_refs]\norigin/Other"), A, include_all=True, days=14)
        self.assertEqual(tips, [A])
        self.assertIn("tracking ref", output.getvalue())
        self.assertIn("extra ref", output.getvalue())

    def test_extra_refs_preserve_case(self):
        repo = FakeRepo([ref("refs/remotes/origin/Feature", B)])
        tips, labels, _ = selected_tips(repo, config("[extra_refs]\norigin/Feature"), A, include_all=True, days=14)
        self.assertEqual(tips, [A, B])
        self.assertEqual(labels[B], ["origin/Feature"])

    def test_invalid_options(self):
        for args in (["--days", "-1"], ["--limit", "-1"], ["--github-timeout", "0"], ["--github-timeout", "nan"], ["--github-timeout", "inf"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args(args)


class CLIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_") and key not in {"GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH"}}
        self.env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"})
        self.git("init", "-q", "-b", "main")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()

    def commit(self, message):
        self.git("commit", "--allow-empty", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def run_cli(self, *args, cwd=None, env=None, script=SCRIPT):
        return subprocess.run([sys.executable, str(script), *args], cwd=cwd or self.repo, env=env or self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)

    def test_empty_repo_and_nonrepo(self):
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("No commits", proc.stdout)
        proc = self.run_cli(cwd=self.root)
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback", proc.stderr)

    def test_local_only_subdirectory_and_directory_option(self):
        oid = self.commit("initial subject")
        nested = self.repo / "nested"
        nested.mkdir()
        proc = self.run_cli(cwd=nested)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(oid[:7], proc.stdout)
        self.assertIn("HEAD -> main", proc.stdout)
        self.assertIn("initial subject", proc.stdout)
        self.assertNotIn("\x1b", proc.stdout)
        proc = self.run_cli("-C", str(nested), cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("initial subject", proc.stdout)

    def test_detached_head(self):
        self.commit("initial")
        self.git("checkout", "--detach", "-q")
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("HEAD", proc.stdout)
        self.assertNotIn("HEAD ->", proc.stdout)

    def test_invalid_base_and_config(self):
        self.commit("initial")
        proc = self.run_cli("--base", "does-not-exist")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot resolve base", proc.stderr)
        (self.repo / ".git" / "smartlog").write_text("invalid", encoding="utf-8")
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot read", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_network_is_opt_in(self):
        self.commit("initial")
        self.git("remote", "add", "origin", "https://github.com/owner/project.git")
        with patch("smartlog.github.fetch_pull_requests", side_effect=AssertionError("network called")):
            # Run main in-process so the sentinel observes any enrichment call.
            from smartlog.cli import main
            with patch("sys.stdout", io.StringIO()):
                self.assertEqual(main(["-C", str(self.repo)]), 0)
        self.assertNotIn("Finished", self.run_cli().stdout)

    def test_color_and_timing_controls(self):
        self.commit("initial")
        proc = self.run_cli("--color", "always", "--timing")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("\x1b", proc.stdout)
        self.assertIn("finished in", proc.stderr)
        proc = self.run_cli("--color", "never")
        self.assertNotIn("\x1b", proc.stdout)

    def test_direct_symlink_launcher(self):
        self.commit("symlink example")
        bindir = self.root / "bin"
        bindir.mkdir()
        link = bindir / "git-smartlog"
        link.symlink_to(SCRIPT)
        proc = self.run_cli(script=link)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("symlink example", proc.stdout)

    def test_python_module_launcher(self):
        self.commit("module example")
        env = dict(self.env, PYTHONPATH=str(ROOT))
        proc = subprocess.run([sys.executable, "-m", "smartlog", "-C", str(self.repo)], cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("module example", proc.stdout)

    def test_worktree_uses_common_config(self):
        self.commit("initial")
        self.git("branch", "Feature")
        (self.repo / ".git" / "smartlog").write_text("[remote]\nhead = Feature\n", encoding="utf-8")
        worktree = self.root / "linked"
        self.git("worktree", "add", "-q", "-b", "topic", str(worktree))
        proc = self.run_cli(cwd=worktree)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Feature", proc.stdout)

    def test_unicode_ref_and_remote_names_are_not_trimmed_or_split(self):
        self.commit("initial")
        branch = "topic\u2000"
        remote = "team\u2028one"
        self.git("checkout", "-qb", branch)
        oid = self.commit("unicode branch")
        self.git("remote", "add", remote, str(self.root / "unused-remote.git"))
        target = f"refs/remotes/{remote}/release"
        self.git("update-ref", target, oid)
        self.git("symbolic-ref", f"refs/remotes/{remote}/HEAD", target)
        proc = self.run_cli("--all")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("HEAD -> " + branch, proc.stdout)
        self.assertIn("team\\u2028one/release", proc.stdout)
        self.assertNotIn("\u2028", proc.stdout)
        self.assertNotIn("using HEAD", proc.stderr)

    def test_bare_repository(self):
        self.commit("bare example")
        bare = self.root / "bare.git"
        self.git("clone", "-q", "--bare", str(self.repo), str(bare))
        proc = self.run_cli(cwd=bare)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("bare example", proc.stdout)


if __name__ == "__main__":
    unittest.main()
