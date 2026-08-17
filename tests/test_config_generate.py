"""Unit tests for callsight.flags.render_config (web UI config builder output)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import flags


def _parse(text):
    """Parse rendered config text back into directive tuples."""
    fd, path = tempfile.mkstemp(suffix=".config")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        return flags.parse_config(path)
    finally:
        os.unlink(path)


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
        includes, excludes, funcs, include_funcs = self.directives(text)[:4]
        self.assertEqual((includes, excludes, funcs, include_funcs),
                         ([], [], [], []))
        self.assertTrue(text.startswith("#"))

    def test_all_directive_groups_round_trip(self):
        text = flags.render_config(
            excluded_files=["src/utils/rng.c", "tests"],
            include_funcs=[("handle_request", None), ("process", 2)],
            excluded_funcs=["crc32_update"])
        includes, excludes, funcs, include_funcs = self.directives(text)[:4]
        self.assertEqual(includes, [])
        self.assertEqual(excludes, ["src/utils/rng.c", "tests"])
        self.assertEqual(funcs, ["crc32_update"])
        self.assertEqual(include_funcs, [("handle_request", None),
                                         ("process", 2)])

    def test_depth_zero_is_preserved(self):
        text = flags.render_config(include_funcs=[("main", 0)])
        include_funcs = self.directives(text).include_funcs
        self.assertEqual(include_funcs, [("main", 0)])

    def test_duplicates_dropped_order_kept(self):
        text = flags.render_config(
            excluded_files=["b.c", "a.c", "b.c"],
            excluded_funcs=["f1", "f1"])
        spec = self.directives(text)
        excludes, funcs = spec.excludes, spec.funcs
        self.assertEqual(excludes, ["b.c", "a.c"])
        self.assertEqual(funcs, ["f1"])

    def test_rendered_lines_match_directive_syntax(self):
        text = flags.render_config(excluded_files=["src/x.c"],
                                   include_funcs=[["run", 1]])
        body = [ln for ln in text.splitlines()
                if ln and not ln.startswith("#")]
        self.assertEqual(body, ["exclude src/x.c", "include-func run 1"])


class TestRenderConfigValidation(unittest.TestCase):
    def test_name_with_whitespace_rejected(self):
        with self.assertRaises(ValueError):
            flags.render_config(excluded_funcs=["bad name"])
        with self.assertRaises(ValueError):
            flags.render_config(include_funcs=[("bad\nname", None)])
        with self.assertRaises(ValueError):
            flags.render_config(include_funcs=[("bad\tname", 1)])

    def test_path_with_control_chars_rejected(self):
        with self.assertRaises(ValueError):
            flags.render_config(excluded_files=["src/x\n.c"])
        with self.assertRaises(ValueError):
            flags.render_config(excluded_files=["src/x\ty.c"])

    def test_path_with_space_round_trips(self):
        # Spaces in paths are legal: parse_config splits on the first
        # whitespace only.
        text = flags.render_config(excluded_files=["src/my dir/x.c"])
        excludes = _parse(text).excludes
        self.assertEqual(excludes, ["src/my dir/x.c"])

    def test_bad_depth_rejected(self):
        with self.assertRaises(ValueError):
            flags.render_config(include_funcs=[("run", -1)])
        with self.assertRaises(ValueError):
            flags.render_config(include_funcs=[["run", "x"]])


if __name__ == "__main__":
    unittest.main()
