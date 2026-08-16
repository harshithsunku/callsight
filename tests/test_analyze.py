"""Unit tests for callsight.analyze: trace file parsing, enter/exit matching,
symbol resolution and the report formats."""

import io
import json
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import analyze


def pack_events(events, magic=analyze.MAGIC, version=analyze.VERSION,
                event_size=None, trailing=b""):
    """Serialize [(tid, kind, func, ts)] into trace-file bytes."""
    size = analyze.EVENT.size if event_size is None else event_size
    out = analyze.HEADER.pack(magic, version, size)
    for tid, kind, func, ts in events:
        out += analyze.EVENT.pack(ts, func, 0, tid, kind)
    return out + trailing


def enter(tid, func, ts):
    return (tid, analyze.ENTER, func, ts)


def leave(tid, func, ts):
    return (tid, analyze.EXIT, func, ts)


class TraceDirFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write_trace(self, name, events, **kwargs):
        path = self.dir / name
        path.write_bytes(pack_events(events, **kwargs))
        return path


class TestReadEvents(TraceDirFixture):
    def test_roundtrip(self):
        evs = [enter(7, 0x400, 100), leave(7, 0x400, 300)]
        path = self.write_trace("trace.1.7.bin", evs)
        self.assertEqual(list(analyze.read_events(path)), evs)

    def test_streams_more_than_one_block(self):
        """The reader loops over fixed-size blocks; cross a block boundary."""
        n = analyze.READ_BLOCK_EVENTS + 5
        evs = [enter(1, 0x10, i) for i in range(n)]
        path = self.write_trace("trace.1.1.bin", evs)
        self.assertEqual(list(analyze.read_events(path)), evs)

    def test_bad_magic_skipped(self):
        path = self.write_trace("trace.1.1.bin", [enter(1, 0x10, 1)],
                                magic=b"NOPENOPE")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(list(analyze.read_events(path)), [])
        self.assertIn("bad or missing header", err.getvalue())

    def test_unknown_version_skipped(self):
        path = self.write_trace("trace.1.1.bin", [enter(1, 0x10, 1)],
                                version=analyze.VERSION + 1)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(list(analyze.read_events(path)), [])
        self.assertIn("version", err.getvalue())

    def test_event_size_mismatch_skipped(self):
        path = self.write_trace("trace.1.1.bin", [enter(1, 0x10, 1)],
                                event_size=analyze.EVENT.size + 8)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(list(analyze.read_events(path)), [])
        self.assertIn("event size", err.getvalue())

    def test_empty_file_yields_nothing(self):
        path = self.dir / "trace.1.1.bin"
        path.write_bytes(b"")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(list(analyze.read_events(path)), [])

    def test_truncated_final_record_is_tolerated(self):
        """A killed process can leave a partial event; keep the good ones."""
        evs = [enter(1, 0x10, 1), leave(1, 0x10, 2)]
        path = self.write_trace("trace.1.1.bin", evs, trailing=b"\x01\x02\x03")
        err = io.StringIO()
        with redirect_stderr(err):
            got = list(analyze.read_events(path))
        self.assertEqual(got, evs)
        self.assertIn("trailing bytes", err.getvalue())


class TestAnalyze(unittest.TestCase):
    def test_single_call(self):
        stats, threads, unmatched, open_frames = analyze.analyze(
            [enter(1, 0xA, 100), leave(1, 0xA, 400)])
        self.assertEqual(stats[0xA], (1, 300, 300, 300))
        self.assertEqual(threads[1], (100, 400))
        self.assertEqual((unmatched, open_frames), (0, 0))

    def test_self_time_excludes_child(self):
        """outer 0..1000 with inner 200..900: self(outer) = 1000 - 700."""
        stats, _t, _u, _o = analyze.analyze([
            enter(1, 0xA, 0), enter(1, 0xB, 200),
            leave(1, 0xB, 900), leave(1, 0xA, 1000)])
        self.assertEqual(stats[0xA], (1, 1000, 300, 1000))
        self.assertEqual(stats[0xB], (1, 700, 700, 700))

    def test_max_time_is_the_slowest_call(self):
        stats, _t, _u, _o = analyze.analyze([
            enter(1, 0xA, 0), leave(1, 0xA, 10),
            enter(1, 0xA, 20), leave(1, 0xA, 120),
            enter(1, 0xA, 200), leave(1, 0xA, 205)])
        calls, incl, self_t, max_t = stats[0xA]
        self.assertEqual((calls, incl, self_t, max_t), (3, 115, 115, 100))

    def test_recursion(self):
        """Nested same-function frames: inclusive double-counts, self doesn't."""
        stats, _t, _u, _o = analyze.analyze([
            enter(1, 0xA, 0), enter(1, 0xA, 100),
            leave(1, 0xA, 400), leave(1, 0xA, 500)])
        calls, incl, self_t, _max = stats[0xA]
        self.assertEqual(calls, 2)
        self.assertEqual(incl, 300 + 500)
        self.assertEqual(self_t, 500)  # 300 inner + (500 - 300) outer

    def test_threads_are_independent(self):
        stats, threads, unmatched, _o = analyze.analyze([
            enter(1, 0xA, 0), enter(2, 0xA, 10),
            leave(1, 0xA, 100), leave(2, 0xA, 300)])
        self.assertEqual(stats[0xA], (2, 100 + 290, 390, 290))
        self.assertEqual(sorted(threads), [1, 2])
        self.assertEqual(unmatched, 0)

    def test_unmatched_exit_counted(self):
        _s, _t, unmatched, _o = analyze.analyze([leave(1, 0xA, 100)])
        self.assertEqual(unmatched, 1)

    def test_unclosed_enter_counted(self):
        _s, _t, _u, open_frames = analyze.analyze(
            [enter(1, 0xA, 0), enter(1, 0xB, 10), leave(1, 0xB, 20)])
        self.assertEqual(open_frames, 1)

    def test_dangling_frames_closed_on_outer_exit(self):
        """An exit for an outer frame discards frames left open above it."""
        stats, _t, unmatched, open_frames = analyze.analyze([
            enter(1, 0xA, 0), enter(1, 0xB, 10), leave(1, 0xA, 100)])
        self.assertIn(0xA, stats)
        self.assertNotIn(0xB, stats)
        self.assertEqual((unmatched, open_frames), (0, 0))


class TestFolded(unittest.TestCase):
    def feed(self, events):
        acc = analyze.Accumulator(folded=True)
        for ev in events:
            acc.feed(*ev)
        return acc

    def test_paths_carry_self_time(self):
        acc = self.feed([enter(1, 0xA, 0), enter(1, 0xB, 200),
                         leave(1, 0xB, 900), leave(1, 0xA, 1000)])
        self.assertEqual(acc.folded[(0xA,)], 300)
        self.assertEqual(acc.folded[(0xA, 0xB)], 700)

    def test_same_callee_under_two_callers_stays_split(self):
        acc = self.feed([enter(1, 0xA, 0), enter(1, 0xC, 0), leave(1, 0xC, 5),
                         leave(1, 0xA, 5),
                         enter(1, 0xB, 10), enter(1, 0xC, 10),
                         leave(1, 0xC, 40), leave(1, 0xB, 40)])
        self.assertEqual(acc.folded[(0xA, 0xC)], 5)
        self.assertEqual(acc.folded[(0xB, 0xC)], 30)

    def test_total_matches_sum_of_self_time(self):
        events = [enter(1, 0xA, 0), enter(1, 0xB, 200),
                  leave(1, 0xB, 900), leave(1, 0xA, 1000)]
        acc = self.feed(events)
        stats, _t, _u, _o = analyze.analyze(events)
        self.assertEqual(sum(acc.folded.values()),
                         sum(self_t for _c, _i, self_t, _m in stats.values()))

    def test_disabled_by_default(self):
        acc = analyze.Accumulator()
        acc.feed(*enter(1, 0xA, 0))
        acc.feed(*leave(1, 0xA, 1))
        self.assertIsNone(acc.folded)


class TestResolve(unittest.TestCase):
    def fake_run(self, output, **kwargs):
        proc = mock.Mock(stdout=output, stderr="", returncode=0)
        return mock.patch("subprocess.run", return_value=proc, **kwargs)

    def test_parses_function_and_location(self):
        with self.fake_run("main\n/src/main.c:10\nhelper\n/src/h.c:3\n"):
            names = analyze.resolve({0x20, 0x10}, "app")
        self.assertEqual(names[0x10], ("main", "/src/main.c:10"))
        self.assertEqual(names[0x20], ("helper", "/src/h.c:3"))

    def test_short_output_falls_back_to_placeholders(self):
        with self.fake_run("main\n"):
            names = analyze.resolve({0x10, 0x20}, "app")
        self.assertEqual(names[0x20], ("??", "??:0"))

    def test_empty_address_set_runs_nothing(self):
        with mock.patch("subprocess.run") as run:
            self.assertEqual(analyze.resolve(set(), "app"), {})
        run.assert_not_called()

    def test_batches_large_address_sets(self):
        """Addresses are chunked so the command line cannot hit ARG_MAX."""
        n = analyze.ADDR2LINE_BATCH * 2 + 1
        addrs = set(range(0x1000, 0x1000 + n))
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            hexes = [c for c in cmd if c.startswith("0x")]
            return mock.Mock(returncode=0, stderr="",
                             stdout="".join(f"fn\n/f.c:1\n" for _ in hexes))

        with mock.patch("subprocess.run", side_effect=run):
            names = analyze.resolve(addrs, "app")
        self.assertEqual(len(names), n)
        self.assertEqual(len(calls), 3)
        for cmd in calls:
            self.assertLessEqual(len([c for c in cmd if c.startswith("0x")]),
                                 analyze.ADDR2LINE_BATCH)

    def test_missing_addr2line_explains_itself(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(RuntimeError) as cm:
                analyze.resolve({0x10}, "app")
        self.assertIn("binutils", str(cm.exception))

    def test_addr2line_failure_reports_exe(self):
        import subprocess
        err = subprocess.CalledProcessError(1, "addr2line", stderr="no such file")
        with mock.patch("subprocess.run", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                analyze.resolve({0x10}, "app")
        self.assertIn("app", str(cm.exception))
        self.assertIn("no such file", str(cm.exception))


class TestCollect(TraceDirFixture):
    """collect() over several files, with addr2line stubbed out."""

    def collect(self, **kwargs):
        names = {0xA: ("outer", "/src/a.c:1"), 0xB: ("inner", "/src/b.c:2")}
        with mock.patch.object(analyze, "resolve", return_value=names):
            return analyze.collect(self.dir, "app", **kwargs)

    def test_no_trace_files(self):
        with self.assertRaises(RuntimeError) as cm:
            analyze.collect(self.dir, "app")
        self.assertIn("no trace files", str(cm.exception))

    def test_header_only_files(self):
        self.write_trace("trace.1.1.bin", [])
        with self.assertRaises(RuntimeError) as cm:
            analyze.collect(self.dir, "app")
        self.assertIn("no events", str(cm.exception))

    def test_merges_files_and_threads(self):
        self.write_trace("trace.1.1.bin",
                         [enter(1, 0xA, 0), enter(1, 0xB, 100),
                          leave(1, 0xB, 300), leave(1, 0xA, 400)])
        self.write_trace("trace.1.2.bin",
                         [enter(2, 0xA, 50), leave(2, 0xA, 250)])
        data = self.collect()
        self.assertEqual(data["events"], 6)
        self.assertEqual(data["threads"], 2)
        self.assertEqual(data["functions"], 2)
        self.assertEqual(data["unmatched_exits"], 0)
        self.assertEqual(data["unclosed_enters"], 0)
        self.assertAlmostEqual(data["span_ms"], 400 / 1e6)
        outer = next(r for r in data["rows"] if r["function"] == "outer")
        self.assertEqual(outer["calls"], 2)
        self.assertAlmostEqual(outer["incl_ms"], 600 / 1e6)
        self.assertEqual([t["tid"] for t in data["per_thread"]], [1, 2])
        self.assertEqual([t["events"] for t in data["per_thread"]], [4, 2])

    def test_one_thread_split_across_files_still_matches(self):
        """Streaming mode can split a thread over several files; the pairs
        must still be matched, not counted as unmatched exits."""
        self.write_trace("trace.stream.1.bin", [enter(1, 0xA, 0)])
        self.write_trace("trace.stream.2.bin", [leave(1, 0xA, 900)])
        data = self.collect()
        self.assertEqual(data["unmatched_exits"], 0)
        self.assertEqual(data["unclosed_enters"], 0)
        self.assertAlmostEqual(data["rows"][0]["incl_ms"], 900 / 1e6)

    def test_folded_rows_only_when_requested(self):
        # outer 0..400 with inner 100..200: self is 300 / 100.
        self.write_trace("trace.1.1.bin",
                         [enter(1, 0xA, 0), enter(1, 0xB, 100),
                          leave(1, 0xB, 200), leave(1, 0xA, 400)])
        self.assertNotIn("folded", self.collect())
        folded = self.collect(folded=True)["folded"]
        self.assertEqual(folded, [("outer", 300), ("outer;inner", 100)])

    def test_pie_warning_when_nothing_resolves(self):
        self.write_trace("trace.1.1.bin", [enter(1, 0xA, 0), leave(1, 0xA, 5)])
        err = io.StringIO()
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("??", "??:0")}):
            with redirect_stderr(err):
                analyze.collect(self.dir, "app")
        self.assertIn("-no-pie", err.getvalue())

    def test_no_pie_warning_when_symbols_resolve(self):
        self.write_trace("trace.1.1.bin", [enter(1, 0xA, 0), leave(1, 0xA, 5)])
        err = io.StringIO()
        with redirect_stderr(err):
            self.collect()
        self.assertEqual(err.getvalue(), "")


class TestReportFormats(TraceDirFixture):
    def setUp(self):
        super().setUp()
        # outer 0..400 with inner 100..200: self time 300 / 100, so row and
        # folded ordering is unambiguous.
        self.write_trace("trace.1.1.bin",
                         [enter(1, 0xA, 0), enter(1, 0xB, 100),
                          leave(1, 0xB, 200), leave(1, 0xA, 400)])
        names = {0xA: ("outer", "/src/a.c:1"), 0xB: ("inner", "/src/b.c:2")}
        patch = mock.patch.object(analyze, "resolve", return_value=names)
        patch.start()
        self.addCleanup(patch.stop)

    def run_report(self, fmt, top=20):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = analyze.report(self.dir, "app", top, fmt)
        return rc, out.getvalue()

    def test_text_report(self):
        rc, out = self.run_report("text")
        self.assertEqual(rc, 0)
        self.assertIn("events=4 threads=1 functions=2", out)
        self.assertIn("unmatched_exits=0", out)
        self.assertIn("TOP BY SELF TIME", out)
        self.assertIn("outer", out)
        self.assertIn("PER-THREAD SUMMARY", out)

    def test_json_report(self):
        _rc, out = self.run_report("json")
        data = json.loads(out)
        self.assertEqual(data["tool"], "callsight")
        self.assertEqual(data["events"], 4)
        self.assertEqual([r["function"] for r in data["rows"]],
                         ["outer", "inner"])  # sorted by self time

    def test_json_top_limits_rows(self):
        _rc, out = self.run_report("json", top=1)
        self.assertEqual(len(json.loads(out)["rows"]), 1)

    def test_json_top_zero_keeps_all_rows(self):
        _rc, out = self.run_report("json", top=0)
        self.assertEqual(len(json.loads(out)["rows"]), 2)

    def test_folded_report(self):
        _rc, out = self.run_report("folded")
        lines = out.splitlines()
        self.assertEqual(lines, ["outer 300", "outer;inner 100"])
        for line in lines:
            path, value = line.rsplit(" ", 1)
            self.assertTrue(int(value) >= 0)
            self.assertTrue(path)


class TestFindExe(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(analyze.find_exe("some/binary"), "some/binary")


if __name__ == "__main__":
    unittest.main()
