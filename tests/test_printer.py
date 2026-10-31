import unittest

from smartlog.graph import Commit, Graph
from smartlog.printer import format_commit_date, render_graph, safe_text


def commit(oid, subject="subject", message=None):
    return Commit(oid, "Author", "author@example.test", 1000000000, subject,
                  message if message is not None else subject)


class PrinterTests(unittest.TestCase):
    def test_first_parent_lanes_and_explicit_other_parent(self):
        a, b, c, d = (str(i) * 40 for i in range(1, 5))
        graph = Graph({oid: commit(oid) for oid in (a, b, c, d)},
                      {d: (b, c), b: (a,), c: (a,), a: ()}, [d, c, b, a])
        output = render_graph(graph, labels={d: ["HEAD -> topic"], c: ["main"]},
                              head=d, ascii=True)
        self.assertIn("*  4444444", output)
        self.assertIn("merge parent 3333333 (main)", output)
        self.assertIn("/ ", output)
        self.assertEqual(output.count("  1111111  "), 1)
        self.assertNotIn("\x1b", output)

    def test_sanitizes_all_untrusted_metadata(self):
        oid = "a" * 40
        graph = Graph({oid: Commit(oid, "author", "a\x1b[31m@evil", 0,
                                  "hello\rworld\x1b]0;title\a", "message")},
                      {oid: ()}, [oid])
        output = render_graph(graph, labels={oid: ["main\x1b[2J"]}, head=oid,
                              annotations={oid: ["PR\u202evil"]})
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\r", output)
        self.assertIn("\\x1b", output)
        self.assertIn("\\u202e", output)

    def test_keeps_differential_revision_and_colors_head_summary(self):
        oid = "a" * 40
        graph = Graph({oid: commit(oid, "topic", "topic\n\nDifferential Revision: https://host/D123\n")},
                      {oid: ()}, [oid])
        output = render_graph(graph, labels={}, head=oid, color=True)
        self.assertIn("D123", output)
        self.assertIn("\x1b[35mtopic\x1b[0m", output)

    def test_disambiguates_short_hash_collisions(self):
        a = "a" * 39 + "1"
        b = "a" * 39 + "2"
        graph = Graph({a: commit(a), b: commit(b)}, {b: (a,), a: ()}, [b, a])
        output = render_graph(graph, labels={}, head=b)
        self.assertIn(a, output)
        self.assertIn(b, output)

    def test_iterative_deep_history(self):
        ids = [f"{i:040x}" for i in range(1200)]
        graph = Graph({oid: commit(oid) for oid in ids},
                      {oid: (ids[i-1],) if i else () for i, oid in enumerate(ids)},
                      list(reversed(ids)))
        output = render_graph(graph, labels={}, head=ids[-1], ascii=True)
        self.assertEqual(output.count("subject"), 1200)

    def test_collapsed_connectors(self):
        a, b = "a" * 40, "b" * 40
        graph = Graph({a: commit(a), b: commit(b)}, {b: (a,), a: ()}, [b, a],
                      collapsed=frozenset({(b, a)}))
        output = render_graph(graph, labels={}, head=b, ascii=True)
        self.assertIn(":  subject", output)

    def test_future_and_timezone_aware_dates(self):
        self.assertEqual(format_commit_date(1060, now=1000), "in 1 minute")
        self.assertEqual(format_commit_date(940, now=1000), "1 minute ago")
        self.assertEqual(format_commit_date(1000, now=1000), "just now")
        self.assertEqual(format_commit_date(0, now=10000000), "1970-01-01")
