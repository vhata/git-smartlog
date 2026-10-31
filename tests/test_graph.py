import unittest
from unittest.mock import patch

from graph_helpers import GitFixture
from smartlog.git import Repository
from smartlog.graph import build_graph


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.f = GitFixture()
        self.addCleanup(self.f.close)
        self.r = self.f.commit("root")
        self.f.ref("refs/heads/main", self.r)

    def graph(self, base, tips=(), **kwargs):
        return build_graph(Repository(self.f.path), base, tips, **kwargs)

    def assert_topological(self, graph):
        positions = {oid: i for i, oid in enumerate(graph.order)}
        self.assertEqual(set(positions), set(graph.commits))
        for child, parents in graph.parents.items():
            for parent in parents:
                self.assertLess(positions[child], positions[parent])

    def test_base_only_does_not_walk_main_history(self):
        main = self.f.commit("main", self.r)
        repo = Repository(self.f.path)
        with patch.object(repo, "run", wraps=repo.run) as run:
            graph = build_graph(repo, main, [main])
        self.assertEqual(set(graph.commits), {main})
        self.assertEqual(graph.parents[main], ())
        self.assertEqual(run.call_count, 1)

    def test_linear_topic_and_collapsed_main(self):
        a = self.f.commit("a", self.r)
        main = self.f.commit("main", a)
        feature = self.f.commit("feature", self.r)
        tip = self.f.commit("tip", feature)
        graph = self.graph(main, [tip])
        self.assertEqual(set(graph.commits), {self.r, main, feature, tip})
        self.assertEqual(graph.parents[main], (self.r,))
        self.assertEqual(graph.parents[tip], (feature,))
        self.assertIn((main, self.r), graph.collapsed)
        self.assert_topological(graph)

    def test_old_mainline_tip_is_retained(self):
        a = self.f.commit("a", self.r)
        b = self.f.commit("b", a)
        main = self.f.commit("main", b)
        graph = self.graph(main, [a])
        self.assertEqual(set(graph.commits), {a, main})
        self.assertEqual(graph.parents[main], (a,))
        self.assertEqual(graph.parents[a], ())

    def test_dragon_last_parent_crash_regression(self):
        a = self.f.commit("a", self.r)
        main = self.f.commit("main", a)
        feature = self.f.commit("feature", a)
        side = self.f.commit("side", self.r)
        merge = self.f.commit("merge", feature, side)
        graph = self.graph(main, [merge])
        self.assertEqual(graph.parents[merge], (feature, side))
        self.assertEqual(graph.parents[side], (self.r,))
        self.assertEqual(graph.parents[feature], (a,))
        self.assert_topological(graph)

    def test_merge_from_main_keeps_topic_work(self):
        a = self.f.commit("a", self.r)
        main = self.f.commit("main", a)
        feature = self.f.commit("feature", self.r)
        merge = self.f.commit("merge main", feature, main)
        graph = self.graph(main, [merge])
        self.assertEqual(graph.parents[merge], (feature, main))
        self.assertEqual(graph.parents[feature], (self.r,))
        self.assertEqual(graph.parents[main], (self.r,))
        self.assert_topological(graph)

    def test_octopus_and_disconnected_root(self):
        a = self.f.commit("a", self.r)
        b = self.f.commit("b", self.r)
        unrelated = self.f.commit("unrelated")
        merge = self.f.commit("octopus", a, b, unrelated)
        graph = self.graph(self.r, [merge])
        self.assertEqual(graph.parents[merge], (a, b, unrelated))
        self.assertEqual(graph.parents[unrelated], ())
        self.assert_topological(graph)

    def test_criss_cross_multiple_merge_bases(self):
        a = self.f.commit("a", self.r)
        b = self.f.commit("b", self.r)
        main = self.f.commit("main merge", a, b)
        topic = self.f.commit("topic merge", b, a)
        graph = self.graph(main, [topic])
        self.assertEqual(graph.parents[main], (a, b))
        self.assertEqual(graph.parents[topic], (b, a))
        self.assert_topological(graph)

    def test_limit_retains_intermediate_named_anchors(self):
        chain = [self.r]
        for i in range(35):
            chain.append(self.f.commit(str(i), chain[-1]))
        graph = self.graph(self.r, [chain[-1]], anchors=[chain[12]], limit=3)
        self.assertIn(chain[12], graph.commits)
        self.assertIn(chain[-1], graph.commits)
        self.assertEqual(len(graph.commits), 9)  # base, anchor, tip + 3 per segment
        self.assertTrue(graph.collapsed)
        self.assert_topological(graph)
        unlimited = self.graph(self.r, [chain[-1]], limit=0)
        self.assertEqual(set(unlimited.commits), set(chain))

    def test_limit_retains_fork_and_merge_points(self):
        fork = self.f.commit("fork", self.r)
        a, b = fork, fork
        for i in range(10):
            a = self.f.commit(f"a{i}", a)
            b = self.f.commit(f"b{i}", b)
        merge = self.f.commit("merge", a, b)
        graph = self.graph(self.r, [merge], limit=1)
        self.assertIn(fork, graph.commits)
        self.assertEqual(len(graph.parents[merge]), 2)
        self.assertEqual(graph.parents[a], (fork,))
        self.assertEqual(graph.parents[b], (fork,))
        self.assert_topological(graph)

    def test_shallow_boundary(self):
        child = self.f.commit("child", self.r)
        tip = self.f.commit("tip", child)
        (self.f.path / ".git" / "shallow").write_text(child + "\n")
        graph = self.graph(child, [tip])
        self.assertEqual(graph.parents[child], ())
        self.assertEqual(graph.parents[tip], (child,))

    def test_topology_does_not_assume_timestamp_order(self):
        child = self.f.commit("child", self.r, timestamp=1000000000)
        tip = self.f.commit("tip", child, timestamp=900000000)
        graph = self.graph(self.r, [tip])
        self.assert_topological(graph)

    def test_legacy_display_git_config_cannot_pollute_objects(self):
        child = self.f.commit("child", self.r)
        repo = Repository(self.f.path)
        graph = build_graph(repo, self.r, [child])
        self.assertEqual(graph.commits[child].subject, "child")
        self.assertEqual(graph.commits[child].email, "fixture@example.test")

    def test_commit_subject_keeps_unicode_separator_for_safe_rendering(self):
        child = self.f.commit("before\u2028after", self.r)
        graph = self.graph(self.r, [child])
        self.assertEqual(graph.commits[child].subject, "before\u2028after")
