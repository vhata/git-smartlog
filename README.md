# git-smartlog

Show a sparse graph of the Git history relevant to your current work:
HEAD, local branches, their tracking branches, and where they meet the
base branch. Long stretches of base history stay collapsed, so busy
repositories remain readable.

The normal command works offline. It needs **Python 3.10 or later and
Git**, with no Python packages to install. Merge commits, local-only
repositories, linked worktrees, detached HEAD and shallow clones are
supported. Optional GitHub annotations add PR and check status.

## Install

Clone the repository, then link the script into a directory on your PATH:

```bash
mkdir -p "$HOME/bin"
ln -s "$PWD/git-smartlog.py" "$HOME/bin/git-smartlog"
git config --global alias.sl smartlog
```

Make sure `$HOME/bin` is on your PATH. Git discovers the executable
`git-smartlog` automatically, so `git smartlog` works without the alias;
`git sl` uses the optional alias above. The script uses `python3` from
PATH, which must be Python 3.10 or later.

Alternatively, install the console command in a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/git-smartlog --help
```

From the source directory, `python3 -m smartlog` also works.

## Use

From any directory inside a repository:

```bash
git sl                           # recent branches and current work
git sl --all                     # include branch tips older than 14 days
git sl --base upstream/main      # choose the base explicitly
git sl --limit 0                 # do not collapse long topic chains
git sl --all --limit 0           # all branch ages and topic-chain lengths
git sl -C /path/to/repository    # inspect a different repository
git sl --ascii --color never    # plain terminal output
git sl --github                 # optional GitHub PR and check status
```

`--all` controls branch age; it does **not** expand the entire base
history or disable the per-chain limit. HEAD and the base are always
included. A selected branch tip remains visible even when it lies in
the middle of a long chain. Dates describe commits, not the last time
a branch name moved.

The display highlights HEAD with `●` (`*` in ASCII mode) and
`HEAD -> branch` (or `HEAD` when detached). Commit lines include an
abbreviated hash, author, refs, relative date and subject. Phabricator `Differential Revision:`
trailers are also displayed. Lanes follow the first visible parent.
`:` marks omitted history;
additional direct merge parents use named arrows, and `...> ancestor`
marks an additional connection through omitted history.

The graph is read-only. It does not fetch branches or change your
index, working tree, refs or configuration. A shallow clone can only
show ancestry available locally.

## Base selection

`--base REF` overrides `[remote] head` in the configuration file.
An invalid explicit base produces an error rather than quietly choosing
another branch. Without an explicit base, selection uses:

1. Locally cached symbolic remote HEAD refs, preferring `origin`, then
   `upstream`, then other remotes in alphabetical order.
2. Existing remote `main`, `trunk`, `master` or `develop` branches in
   the same remote order.
3. Local branches with those names.
4. HEAD, with a notice suggesting `--base` if another branch is wanted.

A repository with no commits prints `No commits yet.`. An unborn branch
in a repository with other local branches can still display their
history. Default discovery does not contact a server.

## Configuration

The existing `.git/smartlog` INI format is supported:

```ini
[remote]
head = upstream/main

[extra_refs]
origin/release/Next
refs/tags/v1.0
```

Ref names are case-sensitive. Extra refs do not need values. INI syntax
trims surrounding whitespace; use `--base` for a ref containing leading
or trailing Unicode whitespace. Missing extra or tracking refs produce
a warning and leave the other branches usable. Extra refs follow the age filter; use `--all` to include old
ones.

Linked worktrees read `smartlog` from the common Git directory, then
their private Git directory if a worktree-specific file exists. A
private file can override a shared base or add extra refs. Bare
repositories use their Git directory directly.

## GitHub annotations

Install and authenticate the GitHub CLI separately, then use `--github`.
This is the only option that enables network access. The command makes
one bounded query for up to 100 recent PRs, including closed and merged
ones, with a default timeout of five seconds. Missing `gh`, failed
authentication, malformed responses and timeouts produce a warning
while the local graph still displays.

PRs match the head repository, branch **and commit hash**. A stale
tracking ref or a same-named branch from another fork does not receive
misleading status. A PR outside the query's recent results may have no
annotation. Unpushed local work does not inherit the older PR head's
checks.

```bash
git sl --github --github-timeout 10
git sl --github --github-repo owner/repository
git sl --github --github-repo github.example.com/team/repository
```

Public `github.com` remotes are detected automatically. Name an explicit
repository for GitHub Enterprise or SSH aliases. Checks aggregate both
check runs and legacy commit statuses. Failures take precedence over
running jobs; missing or unrecognized checks never imply a pass.

## Output and diagnostics

- `--days N` sets the branch age window (default: 14).
- `--limit N` limits interior commits in unbranched topic runs (default: 20;
  0 disables this limit). Branch tips and merge/fork points remain visible.
- `--color auto|always|never` controls color. Automatic color requires
  a terminal and honors `NO_COLOR` and `TERM=dumb`.
- `--ascii` uses ASCII graph characters.
- `--timing` reports elapsed time on stderr.
- `--help` lists all options; `--version` shows the package version.

Warnings and timing go to stderr, keeping redirected graph output clean.
Repository metadata containing terminal control characters is escaped.

## Development

The tests use Python's standard-library `unittest` and disposable local
Git repositories. GitHub responses are mocked; tests do not use an
account or hosted service.

```bash
PYTHON=python3 bash scripts/check.sh
```

See the [design comparison](docs/design.md) with
[Jennifer Taylor's repository][jennifer-taylor] for upstream features,
compatibility decisions and the merge traversal defect that motivated
the graph rewrite.

[jennifer-taylor]: https://github.com/DragonMinded/git-smartlog
