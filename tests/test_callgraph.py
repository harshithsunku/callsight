"""Unit tests for callscope.callgraph and include-func selection."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callscope import callgraph, flags

MAIN_C = """\
#include "helper.h"

/* fake( calls ) { in } comments don't count */
static int leaf(int x) { return x + 1; }

int mid(int x) {
    const char *s = "not_a_call( {";
    return leaf(x) + helper(x);
}

int main(void) {
    return mid(41);
}
"""

HELPER_C = """\
#include "helper.h"

int helper(int x) {
    if (x > 0)
        return helper(x - 1);   /* recursion */
    return 0;
}

int unrelated(int x) { return x * 2; }
"""


class CallgraphFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        d = Path(self.dir.name)
        (d / "main.c").write_text(MAIN_C)
        (d / "helper.c").write_text(HELPER_C)
        self.main = str(d / "main.c")
        self.helper = str(d / "helper.c")
        self.graph = callgraph.build_graph([self.main, self.helper])


class TestParse(CallgraphFixture):
    def test_definitions_found(self):
        self.assertIn("main", self.graph)
        self.assertIn("mid", self.graph)
        self.assertIn("leaf", self.graph)
        self.assertIn("helper", self.graph)

    def test_callees(self):
        self.assertEqual(self.graph["main"]["callees"], ["mid"])
        self.assertEqual(self.graph["mid"]["callees"], ["helper", "leaf"])
        self.assertEqual(self.graph["helper"]["callees"], ["helper"])
        self.assertEqual(self.graph["leaf"]["callees"], [])

    def test_files_recorded(self):
        self.assertEqual(self.graph["helper"]["files"], [self.helper])

    def test_comments_and_strings_ignored(self):
        # "not_a_call" only appears inside a string; "fake" inside a comment
        self.assertNotIn("not_a_call", self.graph["mid"]["callees"])
        self.assertNotIn("fake", self.graph["mid"]["callees"])


class TestExpand(CallgraphFixture):
    def test_full_depth(self):
        self.assertEqual(callgraph.expand(self.graph, ["main"]),
                         {"main", "mid", "leaf", "helper"})

    def test_depth_zero(self):
        self.assertEqual(callgraph.expand(self.graph, ["main"], 0), {"main"})

    def test_depth_one(self):
        self.assertEqual(callgraph.expand(self.graph, ["main"], 1),
                         {"main", "mid"})

    def test_recursion_terminates(self):
        self.assertEqual(callgraph.expand(self.graph, ["helper"]),
                         {"helper"})


class TestFunctionSelection(CallgraphFixture):
    def test_subtree_files_selected(self):
        selected, dropped, auto, reachable, warnings = \
            flags.function_selection([("main", None)],
                                     [self.main, self.helper], [], [])
        self.assertEqual(reachable, {"main", "mid", "leaf", "helper"})
        self.assertEqual(set(selected), {self.main, self.helper})
        self.assertEqual(dropped, [])
        # 'unrelated' is defined in a selected file but not reachable
        self.assertEqual(auto, ["unrelated"])
        self.assertEqual(warnings, [])

    def test_collision_guard(self):
        # 'sort' is NOT reachable but is a substring of reachable
        # 'sort_fast'; the compiler's substring exclude would kill both,
        # so the guard must drop 'sort' from the auto-excludes and warn.
        d = Path(self.dir.name)
        collide = d / "collide.c"
        collide.write_text(
            "int sort(int x) { return x; }\n"
            "int sort_fast(int x) { return x; }\n"
            "int entry(void) { return sort_fast(1); }\n")
        graph = callgraph.build_graph([str(collide)])
        selected, dropped, auto, reachable, warnings = \
            flags.function_selection([("entry", None)], [str(collide)],
                                     [], [])
        self.assertEqual(reachable, {"entry", "sort_fast"})
        self.assertEqual(auto, [])          # 'sort' was guarded out
        self.assertEqual(len(warnings), 1)
        self.assertIn("sort", warnings[0])

    def test_depth_zero_auto_excludes(self):
        selected, dropped, auto, reachable, warnings = \
            flags.function_selection([("main", 0)],
                                     [self.main, self.helper], [], [])
        self.assertEqual(reachable, {"main"})
        # only main.c is selected; helper.c is dropped at file level, and
        # main.c's non-reachable functions are auto-excluded by name
        self.assertEqual(selected, [self.main])
        self.assertEqual(dropped, [self.helper])
        self.assertEqual(set(auto), {"mid", "leaf"})

    def test_unknown_seed_exits(self):
        with self.assertRaises(SystemExit):
            flags.function_selection([("nonexistent", None)],
                                     [self.main], [], [])

    def test_excludes_still_win(self):
        selected, dropped, auto, reachable, _ = \
            flags.function_selection([("main", None)],
                                     [self.main, self.helper],
                                     [], [self.helper])
        self.assertEqual(selected, [self.main])
        self.assertEqual(dropped, [self.helper])


if __name__ == "__main__":
    unittest.main()
