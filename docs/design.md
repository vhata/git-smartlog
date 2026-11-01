# Design and upstream comparison

Git-smartlog is a view of the work around local branch tips. It keeps
HEAD, the base branch and the places branches diverge, without showing
every commit in a busy base branch. It reads Git objects and refs; it
does not fetch, check out branches or change the working tree.

## Original implementation

The original version builds a tree from the merge base of each local
branch and `origin/HEAD`. It shows recent branches and their tracking
refs, and accepts a custom base and extra refs in `.git/smartlog`.
Commit summaries include author, relative date, branch labels and
Phabricator Differential Revision trailers.

A tree cannot represent arbitrary Git histories. The original builder
rejects merges, and its recursive printer can hide selected commits
inside long chains. Reference detection also assumes a configured
remote default. There were no automated tests.

## [Jennifer Taylor][jennifer-taylor]'s changes

The comparison is against
[Jennifer Taylor's repository][jennifer-taylor] at `0ff88ae`, with the
original implementation at `1db2182`. The useful behaviors are retained
in the new implementation, with the following changes.

| Commit | Contribution | Implementation decision |
| --- | --- | --- |
| `c6132a7` | Common default branch names | Support cached remote defaults and local names, including `develop`. |
| `7be7e51` | GitHub PR metadata | Optional `--github`; bind metadata to the correct repository and branch. |
| `8913dd7` | Remote default inference | Read local refs instead of parsing networked, localized `git remote show`. |
| `8892b53` | Null check-rollup handling | Handle missing checks without reporting success or failure. |
| `dffbab9` | Empty checks become pending | Distinguish no checks from passing checks. |
| `0e489bb` | Highlight the current commit | Highlight HEAD and its subject when color is enabled. |
| `e3134c9` | Missing-remote handling | Support local-only and detached repositories. |
| `2ac9a50` | Aggregate check results | Preserve failure precedence and duplicate job names. |
| `f1bcd68` | Prefer origin/upstream | Carry the selected remote through ref and PR resolution. |
| `1337ede` | Ignore legacy status contexts | Interpret legacy statuses as well as modern check runs. |
| `b3c63a5` | Dependencies and locale fixes | Use Python's standard library and stable Git output. |
| `9083655` | Last-parent merge traversal | Replace with a directed acyclic graph preserving all parents. |
| `0ff88ae` | Merge the original upstream | No additional independent feature. |

Her implementation removes the `HEAD ->` label and Differential Revision
annotation. Both are useful existing behavior and remain supported. Its
default remote probing and GitHub calls have no timeout; the new normal
command is offline, and optional GitHub queries are bounded and may fail
without preventing local graph output.

## Why merge traversal changed

Consider a main branch `R -> A -> X`, topic work `A -> F`, side work
`R -> Y`, and a topic merge with parents `F` and `Y`. The merge base of
the topic and main is `A`. Following the last parent walks through `Y`
to `R`, never reaches `A`, and eventually indexes an empty parent list.
This failure occurs in a real Git repository, not only mocked objects.

The replacement models commits and edges explicitly. Long single-path
chains can be contracted, but selected refs and branch/merge junctions
remain visible. Contracted edges are marked as omitted history, and
multiple parent connections remain represented.

## Additional improvements

- Keep ordinary operation offline and free of Python dependencies.
- Read refs and commit metadata in batches rather than spawning Git for
  each displayed hash.
- Support shared configuration in linked worktrees, case-sensitive ref
  names, stale tracking refs, shallow repositories and detached HEAD.
- Keep redirected output free of automatic ANSI color; offer explicit
  color and ASCII modes.
- Escape terminal control characters in repository metadata.
- Provide both an executable script and an installable console command.
- Exercise behavior using real disposable Git repositories and mocked
  GitHub responses, without accessing hosted accounts during tests.

Live GitHub authentication and service availability depend on the user's
`gh` setup. Synthetic response tests establish parsing and matching
behavior, not live account access. Graph work can still grow with a
large amount of unmerged work or widely separated relevant branch
points; the display limit is not a hard cap on Git history processing.

[jennifer-taylor]: https://github.com/DragonMinded/git-smartlog
