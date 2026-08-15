"""Unit tests for callsight.symbols: function-definition enumeration."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import callgraph, symbols

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
            return symbols.list_functions(self.dir)["functions"]

    def test_backend_reported(self):
        with mock.patch("callsight.provision.find_ctags",
                        return_value=None):
            result = symbols.list_functions(self.dir)
        self.assertEqual(result["backend"], "regex")

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
            result = symbols.list_functions(self.dir)
        self.assertEqual(result["backend"], "regex")
        self.assertIn({"file": "src/a.c", "name": "add", "line": 3,
                       "static": False}, result["functions"])

    def test_ctags_bad_lines_skipped(self):
        # One malformed JSON line must not discard the whole ctags run.
        good = {"_type": "tag", "path": os.path.join(self.dir, "src/a.c"),
                "name": "add", "line": 3, "file": True}
        proc = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='not json\n' + json.dumps(good) + '\n[1, 2]\n',
            stderr="")
        with mock.patch("callsight.provision.find_ctags",
                        return_value="/fake/ctags"), \
                mock.patch("subprocess.run", return_value=proc):
            result = symbols.list_functions(self.dir)
        self.assertEqual(result["backend"], "ctags")
        self.assertEqual(result["functions"],
                         [{"file": os.path.join("src", "a.c"),
                           "name": "add", "line": 3, "static": True}])

    def test_ctags_all_bad_lines_falls_back(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0,
                                           stdout="garbage\n", stderr="")
        with mock.patch("callsight.provision.find_ctags",
                        return_value="/fake/ctags"), \
                mock.patch("subprocess.run", return_value=proc):
            result = symbols.list_functions(self.dir)
        self.assertEqual(result["backend"], "regex")
        self.assertIn({"file": "src/a.c", "name": "add", "line": 3,
                       "static": False}, result["functions"])


@unittest.skipUnless(shutil.which("ctags"), "ctags not installed")
class TestCtags(SymbolsFixture):
    def test_same_definitions_as_regex(self):
        result = symbols.list_functions(self.dir)
        self.assertEqual(result["backend"], "ctags")
        entries = result["functions"]
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


# Multi-line comment/string BEFORE the definition: line numbers must stay
# accurate (the strip blanks contents but keeps newlines).
LEADING_C = """\
/* a multi-line comment
   with fake() { inside */
const char *s = "multi \\
line string";

int target(void) {
    return 0;
}
"""

# Regression: a multi-line comment between the return type and the name
# must not hide the definition (the newline-preserving _strip broke this).
SPLIT_COMMENT_C = """\
static int /*
*/ foo(void) {
    return 1;
}
"""


class TestFindDefinitions(SymbolsFixture):
    def defs(self, rel, text):
        self.write(rel, text)
        return callgraph.find_definitions(os.path.join(self.dir, rel))

    def test_line_numbers_after_multiline_comment_and_string(self):
        self.assertEqual(self.defs("src/leading.c", LEADING_C),
                         [("target", 6, False)])

    def test_comment_between_type_and_name(self):
        self.assertEqual(self.defs("src/split.c", SPLIT_COMMENT_C),
                         [("foo", 1, True)])
        parsed = callgraph.parse_source(os.path.join(self.dir,
                                                     "src/split.c"))
        self.assertEqual(parsed, {"foo": []})


if __name__ == "__main__":
    unittest.main()
