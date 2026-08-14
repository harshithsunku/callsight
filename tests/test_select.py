"""Unit tests for tracekit.flags: config parsing, pattern matching, selection."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tracekit import flags


class TestParseConfig(unittest.TestCase):
    def write(self, text):
        fd, path = tempfile.mkstemp(suffix=".config")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_directives(self):
        path = self.write(
            "# comment\n"
            "\n"
            "include src/net/**\n"
            "exclude src/utils/rng.c\n"
            "exclude-func crc32_update\n")
        includes, excludes, funcs = flags.parse_config(path)
        self.assertEqual(includes, ["src/net/**"])
        self.assertEqual(excludes, ["src/utils/rng.c"])
        self.assertEqual(funcs, ["crc32_update"])

    def test_unknown_directive_exits(self):
        path = self.write("frobnicate src/\n")
        with self.assertRaises(SystemExit):
            flags.parse_config(path)

    def test_missing_value_exits(self):
        path = self.write("exclude\n")
        with self.assertRaises(SystemExit):
            flags.parse_config(path)


class TestMatches(unittest.TestCase):
    def test_exact_path(self):
        self.assertTrue(flags.matches("src/utils/rng.c", "src/utils/rng.c"))

    def test_suffix_of_longer_path(self):
        self.assertTrue(
            flags.matches("../matrixlab/src/utils/rng.c", "src/utils/rng.c"))

    def test_bare_filename(self):
        self.assertTrue(flags.matches("src/utils/rng.c", "rng.c"))

    def test_glob(self):
        self.assertTrue(flags.matches("src/net/tcp.c", "src/net/**"))
        self.assertTrue(flags.matches("tests/test_rng.c", "*test*.c"))

    def test_directory_prefix(self):
        self.assertTrue(flags.matches("src/sort/quicksort.c", "src/sort"))
        self.assertTrue(flags.matches("src/sort/quicksort.c", "src/sort/"))

    def test_no_match(self):
        self.assertFalse(flags.matches("src/sort/quicksort.c", "src/net/**"))
        self.assertFalse(flags.matches("src/utils/rng.c", "src/utils/timer.c"))
        # 'sort' alone is not a suffix component of src/sort/quicksort.c... it
        # is a component: 'sort/quicksort.c' starts with 'sort/'.
        self.assertTrue(flags.matches("src/sort/quicksort.c", "sort"))


class TestSelect(unittest.TestCase):
    SRCS = ["src/a.c", "src/net/tcp.c", "src/utils/rng.c", "src/sort/qsort.c"]

    def test_default_includes_everything(self):
        selected, dropped = flags.select(self.SRCS, [], [])
        self.assertEqual(selected, self.SRCS)
        self.assertEqual(dropped, [])

    def test_include_narrows(self):
        selected, dropped = flags.select(self.SRCS, ["src/net/**"], [])
        self.assertEqual(selected, ["src/net/tcp.c"])
        self.assertEqual(len(dropped), 3)

    def test_exclude_wins_over_include(self):
        selected, dropped = flags.select(
            self.SRCS, ["src/**"], ["src/utils/rng.c"])
        self.assertNotIn("src/utils/rng.c", selected)
        self.assertEqual(len(selected), 3)

    def test_exclude_only(self):
        selected, dropped = flags.select(self.SRCS, [], ["src/sort"])
        self.assertEqual(dropped, ["src/sort/qsort.c"])
        self.assertEqual(len(selected), 3)


class TestInstrumentFlags(unittest.TestCase):
    def test_plain(self):
        out = flags.instrument_flags([], [], [], ["src/a.c"])
        self.assertEqual(out, "-finstrument-functions")

    def test_dropped_sources_become_file_excludes(self):
        out = flags.instrument_flags([], ["src/utils/rng.c"], ["crc32_update"],
                                     ["src/a.c", "src/utils/rng.c"])
        self.assertIn("-finstrument-functions-exclude-file-list=src/utils/rng.c", out)
        self.assertIn("-finstrument-functions-exclude-function-list=crc32_update", out)

    def test_header_excludes_passed_through(self):
        # Headers match no source path but must reach the compiler verbatim.
        out = flags.instrument_flags([], ["src/signal/fft.h"], [], ["src/a.c"])
        self.assertIn("src/signal/fft.h", out)

    def test_excludes_deduplicated(self):
        out = flags.instrument_flags([], ["src/a.c"], [], ["src/a.c"])
        self.assertEqual(out.count("src/a.c"), 1)


if __name__ == "__main__":
    unittest.main()
