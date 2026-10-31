"""Build a sparse DAG without losing merge parents or named commits."""

from collections import defaultdict
from dataclasses import dataclass
import heapq
from typing import Iterable

from .git import GitError, Repository


@dataclass(frozen=True)
class Commit:
    oid: str
    author: str
    email: str
    timestamp: int
    subject: str
    message: str
    original_parents: tuple[str, ...] = ()


@dataclass
class Graph:
    commits: dict[str, Commit]
    parents: dict[str, tuple[str, ...]]
    order: list[str]
    mainline: frozenset[str] = frozenset()
    collapsed: frozenset[tuple[str, str]] = frozenset()
    omitted_count: int = 0


def _rev_list(repo, positive, negative=()):
    args = ["rev-list", "--topo-order", "--parents", "--boundary", *positive]
    if negative:
        args.extend(["--not", *negative])
    args.append("--")
    parents, boundary = {}, set()
    for line in repo.run(*args).splitlines():
        fields = line.split()
        oid = fields[0]
        if oid.startswith("-"):
            oid = oid[1:]
            boundary.add(oid)
        parents[oid] = tuple(fields[1:])
    return parents, boundary


def _topological(parents, timestamps=None):
    """Kahn order, newest available commit first; independent of Git dates."""
    incoming = dict.fromkeys(parents, 0)
    for values in parents.values():
        for oid in values:
            incoming[oid] += 1
    timestamps = timestamps or {}
    ready = [(-timestamps.get(oid, 0), oid) for oid, count in incoming.items() if not count]
    heapq.heapify(ready)
    order = []
    while ready:
        _, oid = heapq.heappop(ready)
        order.append(oid)
        for parent in parents[oid]:
            incoming[parent] -= 1
            if not incoming[parent]:
                heapq.heappush(ready, (-timestamps.get(parent, 0), parent))
    if len(order) != len(parents):
        raise GitError("Commit graph contains a cycle")
    return order


def _read_commits(repo, oids):
    """Load all visible commit objects in one process, using byte lengths."""
    oids = list(oids)
    if not oids:
        return {}
    output = repo._execute(
        ("cat-file", "--batch"),
        input=("\n".join(oids) + "\n").encode("ascii"),
    ).stdout
    commits, offset = {}, 0
    for expected in oids:
        end = output.find(b"\n", offset)
        header = output[offset:end].split()
        if len(header) != 3 or header[0].decode("ascii") != expected or header[1] != b"commit":
            raise GitError(f"Could not read commit {expected}")
        size = int(header[2])
        raw = output[end + 1:end + 1 + size]
        offset = end + 2 + size
        raw_header, _, message_bytes = raw.partition(b"\n\n")
        headers = raw_header.splitlines()
        encoding = "utf-8"
        for line in headers:
            if line.startswith(b"encoding "):
                encoding = line[9:].decode("ascii", "replace")
        try:
            message = message_bytes.decode(encoding, "replace")
        except LookupError:
            encoding = "utf-8"
            message = message_bytes.decode(encoding, "replace")
        author, email, timestamp, parents = "", "", 0, []
        for line in headers:
            if line.startswith(b"parent "):
                parents.append(line[7:].decode("ascii"))
            elif line.startswith(b"author "):
                identity, _, _ = line[7:].rsplit(b" ", 2)
                name, sep, address = identity.rpartition(b" <")
                author = (name if sep else identity).decode(encoding, "replace")
                email = address.removesuffix(b">").decode(encoding, "replace") if sep else ""
            elif line.startswith(b"committer "):
                _, stamp, _ = line[10:].rsplit(b" ", 2)
                try:
                    timestamp = int(stamp)
                except ValueError:
                    timestamp = 0
        subject = message.split("\n", 1)[0] if message else "(no commit message)"
        commits[expected] = Commit(
            expected, author, email, timestamp, subject, message, tuple(parents),
        )
    return commits


def build_graph(
    repo: Repository, base: str, tips: Iterable[str], *,
    anchors: Iterable[str] = (), limit: int = 20,
) -> Graph:
    """Keep topic work, anchors and topology; contract base history and long chains.

    ``limit`` bounds unbranched topic interiors per segment, not named tips,
    merge/fork points or base-history anchors. Zero disables topic trimming.
    Base ancestry older than the common ancestor of all base anchors is never
    walked. Shallow boundaries naturally act as roots of the available graph.
    """
    if limit < 0:
        raise ValueError("limit must be nonnegative")
    selected = set(tips) | set(anchors) | {base}
    off_graph, boundary = _rev_list(repo, sorted(selected), (base,))
    off_base = set(off_graph) - boundary
    mainline = (selected - off_base) | boundary

    # Obtain only the portion of base history needed to connect anchors. The
    # overwhelmingly common case with no older anchors needs no mainline walk.
    main_graph = {}
    if len(mainline) > 1:
        result = repo._execute(
            ("merge-base", "--all", "--octopus", *sorted(mainline)), check=False,
        )
        if result.returncode not in (0, 1):
            raise GitError(result.stderr.decode("utf-8", "replace").strip())
        common = result.stdout.decode("ascii").splitlines()
        main_graph, _ = _rev_list(repo, sorted(mainline), common)
    for oid in mainline:
        main_graph.setdefault(oid, ())
    full = {**off_graph, **main_graph}
    # rev-list boundary records can name excluded parents. Those are beyond
    # every relevant anchor and intentionally terminate the available graph.
    full = {oid: tuple(parent for parent in parents if parent in full)
            for oid, parents in full.items()}
    full_order = _topological(full)

    children = defaultdict(list)
    for oid, parents in full.items():
        for parent in parents:
            children[parent].append(oid)
    required = selected | mainline | {
        oid for oid in off_base
        if len(full[oid]) != 1 or len(children[oid]) != 1
    }
    keep = set(required)
    if not limit:
        keep.update(off_base)
    else:
        for start in required:
            for parent in full[start]:
                count = 0
                while parent not in required and parent in off_base:
                    if count < limit:
                        keep.add(parent)
                    count += 1
                    parent = full[parent][0]

    nearest, projected = {}, {}
    for oid in reversed(full_order):
        ancestor_ids = tuple(dict.fromkeys(
            ancestor for parent in full[oid] for ancestor in nearest[parent]
        ))
        if oid in keep:
            projected[oid] = ancestor_ids
            nearest[oid] = (oid,)
        else:
            nearest[oid] = ancestor_ids
    commits = _read_commits(repo, sorted(projected))
    order = _topological(projected, {oid: c.timestamp for oid, c in commits.items()})
    collapsed = frozenset(
        (oid, parent) for oid, parents in projected.items() for parent in parents
        if parent not in commits[oid].original_parents
    )
    return Graph(
        commits, projected, order, frozenset(mainline), collapsed,
        len(full) - len(projected),
    )
