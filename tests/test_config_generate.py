"""Unit tests for callsight.flags.render_config (web UI config builder output)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import flags


class TestRenderConfig(unittest.TestCase):
    def directives(self, text):
        """Parse rendered text back into directive tuples via parse_config."""
        fd, path = tempfile.mkstemp(suffix=".config")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        return flags.parse_config(path)

    def test_empty_selection_renders_comments_only(self):
        text = flags.render_config()
        includes, excludes, funcs, include_funcs = self.directives(text)
        self.assertEqual((includes, excludes, funcs, include_funcs),
                         ([], [], [], []))
        self.assertTrue(text.startswith("#"))

    def test_all_directive_groups_round_trip(self):
        text = flags.render_config(
            excluded_files=["src/utils/rng.c", "tests"],
            include_funcs=[("handle_request", None), ("process", 2)],
            excluded_funcs=["crc32_update"])
        includes, excludes, funcs, include_funcs = self.directives(text)
        self.assertEqual(includes, [])
        self.assertEqual(excludes, ["src/utils/rng.c", "tests"])
        self.assertEqual(funcs, ["crc32_update"])
        self.assertEqual(include_funcs, [("handle_request", None),
                                         ("process", 2)])

    def test_depth_zero_is_preserved(self):
        text = flags.render_config(include_funcs=[("main", 0)])
        _, _, _, include_funcs = self.directives(text)
        self.assertEqual(include_funcs, [("main", 0)])

    def test_duplicates_dropped_order_kept(self):
        text = flags.render_config(
            excluded_files=["b.c", "a.c", "b.c"],
            excluded_funcs=["f1", "f1"])
        _, excludes, funcs, _ = self.directives(text)
        self.assertEqual(excludes, ["b.c", "a.c"])
        self.assertEqual(funcs, ["f1"])

    def test_rendered_lines_match_directive_syntax(self):
        text = flags.render_config(excluded_files=["src/x.c"],
                                   include_funcs=[["run", 1]])
        body = [ln for ln in text.splitlines()
                if ln and not ln.startswith("#")]
        self.assertEqual(body, ["exclude src/x.c", "include-func run 1"])


if __name__ == "__main__":
    unittest.main()
