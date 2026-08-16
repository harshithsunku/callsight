"""Unit tests for callsight.flags compiler detection.

The -finstrument-functions-exclude-* flags are GCC-only (LLVM issue #15627),
so a selective config must be rejected with an explanation before the build
reaches clang."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import flags

CLANG_BANNER = "Ubuntu clang version 18.1.3\nTarget: x86_64-pc-linux-gnu\n"
GCC_BANNER = ("gcc (Ubuntu 14.2.0-19ubuntu2) 14.2.0\n"
              "Copyright (C) 2024 Free Software Foundation, Inc.\n")


def fake_version(banner, returncode=0):
    proc = mock.Mock(stdout=banner, stderr="", returncode=returncode)
    return mock.patch("subprocess.run", return_value=proc)


class TestDetectCompiler(unittest.TestCase):
    def setUp(self):
        flags._compiler_cache.clear()
        self.addCleanup(flags._compiler_cache.clear)

    def test_clang(self):
        with fake_version(CLANG_BANNER):
            self.assertEqual(flags.detect_compiler("clang"), "clang")

    def test_gcc(self):
        with fake_version(GCC_BANNER):
            self.assertEqual(flags.detect_compiler("gcc"), "gcc")

    def test_cross_toolchain_prefix(self):
        with fake_version(GCC_BANNER):
            self.assertEqual(
                flags.detect_compiler("aarch64-linux-gnu-gcc"), "gcc")

    def test_compiler_with_arguments(self):
        """CC may carry flags, e.g. 'ccache gcc' or 'gcc -m32'."""
        with fake_version(GCC_BANNER) as run:
            self.assertEqual(flags.detect_compiler("ccache gcc"), "gcc")
        run.assert_called_once()
        self.assertEqual(run.call_args[0][0], ["ccache", "gcc", "--version"])

    def test_missing_compiler_is_unknown(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(flags.detect_compiler("no-such-cc"))

    def test_timeout_is_unknown(self):
        err = subprocess.TimeoutExpired("cc", 30)
        with mock.patch("subprocess.run", side_effect=err):
            self.assertIsNone(flags.detect_compiler("cc"))

    def test_nonzero_exit_is_unknown(self):
        with fake_version(GCC_BANNER, returncode=1):
            self.assertIsNone(flags.detect_compiler("cc"))

    def test_unrecognized_banner_is_unknown(self):
        with fake_version("tcc version 0.9.27\n"):
            self.assertIsNone(flags.detect_compiler("tcc"))

    def test_empty_command_is_unknown(self):
        self.assertIsNone(flags.detect_compiler(""))

    def test_result_is_cached(self):
        with fake_version(CLANG_BANNER) as run:
            flags.detect_compiler("clang")
            flags.detect_compiler("clang")
        run.assert_called_once()


class TestCheckCompiler(unittest.TestCase):
    def test_clang_with_excludes_exits(self):
        with self.assertRaises(SystemExit) as cm:
            flags.check_compiler("clang", ["src/rng.c"], [])
        msg = str(cm.exception)
        self.assertIn("requires GCC", msg)
        self.assertIn("15627", msg)

    def test_clang_with_function_excludes_exits(self):
        with self.assertRaises(SystemExit):
            flags.check_compiler("clang", [], ["crc32_update"])

    def test_clang_without_excludes_is_fine(self):
        self.assertIsNone(flags.check_compiler("clang", [], []))

    def test_gcc_with_excludes_is_fine(self):
        self.assertIsNone(flags.check_compiler("gcc", ["src/rng.c"], ["fn"]))

    def test_unknown_compiler_is_treated_as_gcc(self):
        """Detection must never break a build that would otherwise work."""
        self.assertIsNone(flags.check_compiler(None, ["src/rng.c"], ["fn"]))


class TestFlagsMainCompilerHandling(unittest.TestCase):
    def setUp(self):
        flags._compiler_cache.clear()
        self.addCleanup(flags._compiler_cache.clear)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.selective = self.write_config("exclude src/rng.c\n")
        self.everything = self.write_config("# instrument everything\n")

    def write_config(self, text):
        path = Path(self.tmp.name) / f"cfg{len(os.listdir(self.tmp.name))}"
        path.write_text(text)
        return str(path)

    def run_main(self, argv):
        err = io.StringIO()
        with redirect_stderr(err):
            flags.main(argv)

    def test_clang_selective_config_exits(self):
        with fake_version(CLANG_BANNER):
            with self.assertRaises(SystemExit) as cm:
                self.run_main(["--config", self.selective,
                               "--compiler-cmd", "clang", "src/rng.c"])
        self.assertIn("requires GCC", str(cm.exception))

    def test_explicit_compiler_flag_overrides_detection(self):
        with mock.patch("subprocess.run") as run:
            with self.assertRaises(SystemExit):
                self.run_main(["--config", self.selective,
                               "--compiler", "clang", "src/rng.c"])
        run.assert_not_called()  # no detection needed

    def test_clang_without_excludes_emits_plain_flag(self):
        out = io.StringIO()
        with fake_version(CLANG_BANNER):
            with mock.patch("sys.stdout", out):
                self.run_main(["--config", self.everything,
                               "--compiler-cmd", "clang", "src/main.c"])
        self.assertIn("-finstrument-functions", out.getvalue())
        self.assertNotIn("exclude", out.getvalue())

    def test_cc_env_var_is_used_for_detection(self):
        with mock.patch.dict(os.environ, {"CC": "clang"}):
            with fake_version(CLANG_BANNER) as run:
                with self.assertRaises(SystemExit):
                    self.run_main(["--config", self.selective, "src/rng.c"])
        self.assertEqual(run.call_args[0][0], ["clang", "--version"])

    def test_gcc_selective_config_still_works(self):
        out = io.StringIO()
        with fake_version(GCC_BANNER):
            with mock.patch("sys.stdout", out):
                self.run_main(["--config", self.selective,
                               "--compiler-cmd", "gcc", "src/rng.c"])
        self.assertIn("-finstrument-functions-exclude-file-list=",
                      out.getvalue())


if __name__ == "__main__":
    unittest.main()
