"""Unit tests for callsight.symbols: function-definition enumeration."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import symbols

A_C = """\
#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

static int helper(int x) {
    return x * 2;
}
"""

# Multi-line parameter list; `static` sits before the name on the first line.
B_C = """\
static int mul(int a,
               int b) {
    return a * b;
}

void run(void) {
    mul(1, 2);
}
"""


class SymbolsFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.write("src/a.c", A_C)
        self.write("src/b.c", B_C)
        # Skipped by flags.scan_sources: build dirs and VCS dirs.
        self.write("build/generated.c", "int gen(void) { return 0; }\n")
        self.write(".git/hooks/hook.c", "int hook(void) { return 0; }\n")

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)


class TestRegexFallback(SymbolsFixture):
    def run_symbols(self):
        with mock.patch("callsight.provision.find_ctags",
                        return_value=None):
            return symbols.list_functions(self.dir)

    def test_finds_all_definitions(self):
        by_name = {e["name"]: e for e in self.run_symbols()}
        self.assertEqual(set(by_name), {"add", "helper", "mul", "run"})

    def test_file_line_static(self):
        by_name = {e["name"]: e for e in self.run_symbols()}
        self.assertEqual(by_name["add"],
                         {"file": "src/a.c", "name": "add", "line": 3,
                          "static": False})
        self.assertEqual(by_name["helper"],
                         {"file": "src/a.c", "name": "helper", "line": 7,
                          "static": True})
        self.assertEqual(by_name["mul"],
                         {"file": "src/b.c", "name": "mul", "line": 1,
                          "static": True})
        self.assertEqual(by_name["run"],
                         {"file": "src/b.c", "name": "run", "line": 6,
                          "static": False})

    def test_skip_dirs_apply(self):
        names = [e["name"] for e in self.run_symbols()]
        self.assertNotIn("gen", names)
        self.assertNotIn("hook", names)

    def test_sorted_by_file_then_line(self):
        entries = self.run_symbols()
        keys = [(e["file"], e["line"]) for e in entries]
        self.assertEqual(keys, sorted(keys))

    def test_ctags_failure_falls_back(self):
        # find_ctags() finds a binary but running it fails: silent regex
        # fallback.
        with mock.patch("callsight.provision.find_ctags",
                        return_value="/nonexistent/ctags"):
            entries = symbols.list_functions(self.dir)
        self.assertIn({"file": "src/a.c", "name": "add", "line": 3,
                       "static": False}, entries)


@unittest.skipUnless(shutil.which("ctags"), "ctags not installed")
class TestCtags(SymbolsFixture):
    def test_same_definitions_as_regex(self):
        entries = symbols.list_functions(self.dir)
        by_name = {e["name"]: e for e in entries}
        self.assertEqual(set(by_name), {"add", "helper", "mul", "run"})
        self.assertEqual(by_name["add"],
                         {"file": "src/a.c", "name": "add", "line": 3,
                          "static": False})
        self.assertEqual(by_name["helper"],
                         {"file": "src/a.c", "name": "helper", "line": 7,
                          "static": True})
        self.assertEqual(by_name["mul"],
                         {"file": "src/b.c", "name": "mul", "line": 1,
                          "static": True})
        keys = [(e["file"], e["line"]) for e in entries]
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
