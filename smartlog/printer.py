"""Compact primary-parent lanes with explicit additional merge edges."""

from collections import defaultdict
from datetime import datetime, timezone
import time
import unicodedata


def safe_text(value):
    """Never interpret commit/ref/PR metadata as terminal control sequences."""
    result = []
    for char in str(value):
        code = ord(char)
        if unicodedata.category(char) in {"Cc", "Cs", "Cf", "Zl", "Zp"}:
            if code <= 0xFF:
                result.append(f"\\x{code:02x}")
            elif code <= 0xFFFF:
                result.append(f"\\u{code:04x}")
            else:
                result.append(f"\\U{code:08x}")
        else:
            result.append(char)
    return "".join(result)


def format_commit_date(timestamp, *, now=None):
    now = time.time() if now is None else now
    seconds = int(now - timestamp)
    future = seconds < 0
    seconds = abs(seconds)
    if seconds < 10:
        return "just now"
    units = ((86400 * 7, "week"), (86400, "day"), (3600, "hour"),
             (60, "minute"), (1, "second"))
    if seconds >= 31 * 86400:
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            return "unknown date"
    for size, unit in units:
        if seconds >= size:
            count = seconds // size
            label = f"{count} {unit}{'' if count == 1 else 's'}"
            return f"in {label}" if future else f"{label} ago"
    return "just now"


def _abbreviations(oids):
    """Disambiguate all visible objects without a subprocess per row."""
    ordered = sorted(oids)
    result = {}
    for i, oid in enumerate(ordered):
        length = min(7, len(oid))
        for other in ordered[max(0, i - 1):i] + ordered[i + 1:i + 2]:
            common = 0
            for a, b in zip(oid, other):
                if a != b:
                    break
                common += 1
            length = max(length, common + 1)
        result[oid] = oid[:length]
    return result


def render_graph(graph, *, labels, head, color=False, ascii=False, annotations=None):
    """Render each commit once; named arrows retain secondary parent edges.

    Lanes represent each node's first visible parent. Additional parents are
    named explicitly so octopus merges and crossing edges remain unambiguous.
    Dotted primary connectors and ``...`` arrows denote contracted history.
    """
    if not graph.commits:
        return ""
    annotations = annotations or {}
    abbreviations = _abbreviations(graph.commits)
    abbreviations.update({oid: commit.abbrev for oid, commit in graph.commits.items()
                          if commit.abbrev})
    children = defaultdict(list)
    roots = []
    for oid, parents in graph.parents.items():
        if parents:
            children[parents[0]].append(oid)
        else:
            roots.append(oid)
    rank = {oid: index for index, oid in enumerate(graph.order)}
    for values in children.values():
        values.sort(key=lambda oid: (oid not in graph.mainline, -rank[oid]))
    roots.sort(key=lambda oid: -rank[oid])
    lines = []
    now = time.time()

    def paint(value, code):
        return f"\x1b[{code}m{value}\x1b[0m" if color else value

    def summary(oid):
        commit = graph.commits[oid]
        sha = paint(abbreviations[oid], "35" if oid == head else "33")
        author = safe_text(commit.email.rsplit("@", 1)[0] or commit.author)
        names = sorted(set(labels.get(oid, ())), key=lambda value: ("/" in value, value.casefold(), value))
        ref_text = paint("(" + ", ".join(safe_text(name) for name in names) + ")", "32") if names else ""
        metadata = [sha, author]
        if ref_text:
            metadata.append(ref_text)
        for line in commit.message.splitlines():
            if line.startswith("Differential Revision:"):
                revision = line.partition(":")[2].strip().rsplit("/", 1)[-1]
                if revision:
                    metadata.append(paint(safe_text(revision), "34"))
                break
        metadata.append(format_commit_date(commit.timestamp, now=now))
        result = ["  ".join(item for item in metadata if item),
                  paint(safe_text(commit.subject), "35") if oid == head else safe_text(commit.subject)]
        for parent in graph.parents[oid][1:]:
            arrow = "...>" if (oid, parent) in graph.collapsed else ("->" if ascii else "↪")
            target_names = labels.get(parent, ())
            target = abbreviations[parent]
            if target_names:
                target += " (" + ", ".join(safe_text(name) for name in sorted(set(target_names))) + ")"
            result.append(f"{arrow} merge parent {target}")
        result.extend(safe_text(value) for value in annotations.get(oid, ()))
        return result

    # Explicit events replace recursion, allowing deep chains/forked histories.
    for root_index, root in enumerate(roots):
        if root_index:
            lines.append("")
        stack = [("root", root, "", "", 0, "")]
        while stack:
            action, oid, prefix, before, index, connector = stack.pop()
            if action in {"visit", "root"}:
                events = []
                main_connector = ""
                for i, child in enumerate(children[oid]):
                    child_prefix = prefix + main_connector + (" " if i else "")
                    child_connector = ":" if (child, oid) in graph.collapsed else "|"
                    events.append(("visit", child, child_prefix, "", 0, ""))
                    events.append(("draw", child, prefix, main_connector, i, child_connector))
                    main_connector = child_connector
                if action == "root":
                    events.append(("draw_root", oid, "", "", 0, ""))
                stack.extend(reversed(events))
                continue
            values = summary(oid)
            bullet = "*" if oid == head else "o"
            if not ascii:
                bullet = "●" if oid == head else "○"
            graph_prefix = before + (" " if index else "") + bullet
            lines.append(prefix + graph_prefix + "  " + values[0])
            if action == "draw_root":
                lines.append("   " + values[1])
                lines.extend("   " + value for value in values[2:])
                continue
            second_prefix = connector if not index else before + "/ "
            lines.append(prefix + second_prefix + "  " + values[1])
            remaining_prefix = connector + ("  " if index else "")
            lines.extend(prefix + remaining_prefix + "  " + value for value in values[2:])
            lines.append(prefix + connector)
    return "\n".join(lines) + "\n"
